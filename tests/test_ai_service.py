"""Tests for :class:`~app.services.ai.AIService`.

These exercise the service's contract — schema-validated parsing, graceful degradation to
``None`` on every failure mode, exact-match caching, the load-based auto-downgrade and the
health snapshot — against a fake Ollama client so no network or real inference happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from app.clients.base import CircuitBreakerOpen, HTTPClientError
from app.clients.ollama import OllamaResponseError
from app.services.ai import AIService, ModelTier, SchemaError, require_int, require_str

if TYPE_CHECKING:
    from collections.abc import Mapping


class FakeOllama:
    """Stand-in for :class:`OllamaClient`: returns queued replies / raises queued errors."""

    def __init__(
        self, replies: list[Any] | None = None, *, version: str = '0.1.0', version_error: BaseException | None = None
    ) -> None:
        self._replies = list(replies or [])
        self._version = version
        self._version_error = version_error
        self.calls: list[dict[str, Any]] = []
        self.breaker_open = False

    async def chat(self, messages: Any, *, model: str, json_mode: bool = False, **_kwargs: Any) -> str:
        self.calls.append({'messages': list(messages), 'model': model, 'json_mode': json_mode})
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def version(self) -> str:
        if self._version_error is not None:
            raise self._version_error
        if self.breaker_open:
            raise CircuitBreakerOpen('Ollama', 5.0)
        return self._version


@dataclass(slots=True)
class Greeting:
    """A tiny schema used to drive parse tests."""

    name: str
    count: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Greeting:
        return cls(name=require_str(payload, 'name'), count=require_int(payload, 'count'))


MODELS = {
    ModelTier.FAST: 'fast-model',
    ModelTier.BALANCED: 'balanced-model',
    ModelTier.SMART: 'smart-model',
}


def make_service(replies: list[Any] | None = None, *, enabled: bool = True, **kwargs: Any) -> tuple[AIService, FakeOllama]:
    client = FakeOllama(replies)
    service = AIService(client, models=MODELS, enabled=enabled, **kwargs)  # type: ignore[arg-type]
    return service, client


# -- complete ---------------------------------------------------------------------


async def test_complete_returns_text() -> None:
    service, _ = make_service(['hello there'])
    assert await service.complete([{'role': 'user', 'content': 'hi'}]) == 'hello there'


async def test_complete_uses_smart_tier_by_default() -> None:
    service, client = make_service(['ok'])
    await service.complete([{'role': 'user', 'content': 'hi'}])
    assert client.calls[0]['model'] == 'smart-model'


async def test_complete_returns_none_when_disabled() -> None:
    service, client = make_service(['ok'], enabled=False)
    assert await service.complete([{'role': 'user', 'content': 'hi'}]) is None
    assert client.calls == []  # never touched the client


async def test_complete_returns_none_on_transport_error() -> None:
    service, _ = make_service([HTTPClientError.__new__(HTTPClientError)])
    assert await service.complete([{'role': 'user', 'content': 'hi'}]) is None


async def test_complete_returns_none_on_timeout() -> None:
    service, _ = make_service([TimeoutError('slow')])
    assert await service.complete([{'role': 'user', 'content': 'hi'}]) is None


# -- parse ------------------------------------------------------------------------


async def test_parse_returns_validated_schema() -> None:
    service, client = make_service(['{"name": "Percy", "count": 3}'])
    result = await service.parse('greet', schema=Greeting, system='sys')
    assert result == Greeting(name='Percy', count=3)
    assert client.calls[0]['json_mode'] is True
    assert client.calls[0]['model'] == 'fast-model'  # parse defaults to FAST tier


async def test_parse_invalid_json_falls_back_to_none() -> None:
    # Both the first attempt and the stricter retry return junk.
    service, client = make_service(['not json at all', 'still not json'])
    assert await service.parse('greet', schema=Greeting, system='sys') is None
    assert len(client.calls) == 2  # one retry happened


async def test_parse_schema_violation_returns_none() -> None:
    # Valid JSON, but missing the required 'count' field.
    service, _ = make_service(['{"name": "Percy"}', '{"name": "Percy"}'])
    assert await service.parse('greet', schema=Greeting, system='sys') is None


async def test_parse_non_object_json_returns_none() -> None:
    service, _ = make_service(['[1, 2, 3]', '[1, 2, 3]'])
    assert await service.parse('greet', schema=Greeting, system='sys') is None


async def test_parse_no_retry_when_disabled() -> None:
    service, client = make_service(['garbage'])
    assert await service.parse('greet', schema=Greeting, system='sys', retry_on_invalid=False) is None
    assert len(client.calls) == 1


async def test_parse_caches_successful_result() -> None:
    service, client = make_service(['{"name": "Percy", "count": 1}'])
    first = await service.parse('  GREET   me ', schema=Greeting, system='sys')
    # Second call with differently-spaced/cased prompt hits the cache (no new client call).
    second = await service.parse('greet me', schema=Greeting, system='sys')
    assert first == second
    assert len(client.calls) == 1


async def test_parse_returns_none_when_disabled() -> None:
    service, client = make_service(['{"name": "x", "count": 1}'], enabled=False)
    assert await service.parse('greet', schema=Greeting, system='sys') is None
    assert client.calls == []


# -- model tier / auto-downgrade --------------------------------------------------


async def test_model_for_resolves_tier() -> None:
    service, _ = make_service()
    assert service.model_for(ModelTier.BALANCED) == 'balanced-model'


async def test_balanced_downgrades_to_fast_when_degraded() -> None:
    service, _ = make_service()
    service._degraded = True  # simulate a degraded health probe
    assert service.model_for(ModelTier.BALANCED) == 'fast-model'
    assert service.model_for(ModelTier.SMART) == 'smart-model'  # only BALANCED downgrades


# -- health -----------------------------------------------------------------------


async def test_health_reports_reachable() -> None:
    service, _ = make_service()
    report = await service.health()
    assert report.enabled is True
    assert report.reachable is True
    assert report.degraded is False
    assert report.models['balanced'] == 'balanced-model'


async def test_health_unreachable_sets_degraded() -> None:
    service, client = make_service()
    client.breaker_open = True
    report = await service.health()
    assert report.reachable is False
    assert report.degraded is True


async def test_health_disabled_is_not_reachable() -> None:
    service, _ = make_service(enabled=False)
    report = await service.health()
    assert report.enabled is False
    assert report.reachable is False
    assert report.degraded is False


async def test_available_reflects_enabled_and_breaker() -> None:
    service, client = make_service()
    assert service.available is True
    client.breaker_open = True
    assert service.available is False


def test_require_helpers_reject_bad_types() -> None:
    with pytest.raises(SchemaError):
        require_str({'k': 5}, 'k')
    with pytest.raises(SchemaError):
        require_int({'k': True}, 'k')  # bool is not an int here
    with pytest.raises(SchemaError):
        require_str({}, 'missing')


def test_ollama_response_error_is_runtime_error() -> None:
    assert issubclass(OllamaResponseError, RuntimeError)


# -- startup health-check logging -------------------------------------------------
#
# Bot._check_ai_health is a thin logging wrapper over AIService.health; exercise it with a
# stub carrying just .ai and .log so we don't need a live bot.


class _StubAI:
    def __init__(self, *, enabled: bool, report: Any) -> None:
        self.enabled = enabled
        self._report = report

    async def health(self) -> Any:
        return self._report


def _make_report(*, reachable: bool) -> Any:
    from app.services.ai import AIHealthReport

    return AIHealthReport(
        enabled=True,
        reachable=reachable,
        degraded=not reachable,
        latency_ms=12.0 if reachable else None,
        version='0.3.0' if reachable else None,
        models={'balanced': 'qwen2.5-coder:3b'},
        calls=0,
        failures=0,
        cache_hits=0,
        cache_misses=0,
    )


async def test_check_ai_health_warns_when_unreachable(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    from types import SimpleNamespace

    from app.core.bot import Bot

    stub = SimpleNamespace(ai=_StubAI(enabled=True, report=_make_report(reachable=False)), log=logging.getLogger('test.ai'))
    with caplog.at_level(logging.WARNING, logger='test.ai'):
        await Bot._check_ai_health(stub)  # type: ignore[arg-type]

    assert any(r.levelno == logging.WARNING and 'UNREACHABLE' in r.getMessage() for r in caplog.records)


async def test_check_ai_health_info_when_reachable(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    from types import SimpleNamespace

    from app.core.bot import Bot

    stub = SimpleNamespace(ai=_StubAI(enabled=True, report=_make_report(reachable=True)), log=logging.getLogger('test.ai'))
    with caplog.at_level(logging.INFO, logger='test.ai'):
        await Bot._check_ai_health(stub)  # type: ignore[arg-type]

    assert any(r.levelno == logging.INFO and 'reachable' in r.getMessage() for r in caplog.records)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


async def test_check_ai_health_notes_disabled(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    from types import SimpleNamespace

    from app.core.bot import Bot

    stub = SimpleNamespace(ai=_StubAI(enabled=False, report=None), log=logging.getLogger('test.ai'))
    with caplog.at_level(logging.INFO, logger='test.ai'):
        await Bot._check_ai_health(stub)  # type: ignore[arg-type]

    assert any('disabled' in r.getMessage() for r in caplog.records)


# -- probe error diagnostics ------------------------------------------------------


def test_describe_probe_error_flags_proxy_block() -> None:
    from app.services.ai.service import _describe_probe_error

    err = type('E', (Exception,), {'status': 403})()
    msg = _describe_probe_error(err)
    assert '403' in msg
    assert 'x-ollama-auth' in msg  # actionable hint about the WAF/proxy layer


def test_describe_probe_error_generic_status() -> None:
    from app.services.ai.service import _describe_probe_error

    err = type('E', (Exception,), {'status': 502})()
    assert '502' in _describe_probe_error(err)


def test_describe_probe_error_timeout() -> None:
    from app.services.ai.service import _describe_probe_error

    assert 'timed out' in _describe_probe_error(TimeoutError())


async def test_health_reports_proxy_block_error() -> None:
    # A 403 from Cloudflare-in-front-of-Ollama surfaces as an actionable error, not just "down".
    from types import SimpleNamespace

    response = SimpleNamespace(status=403, reason='Forbidden')
    client = FakeOllama(version_error=HTTPClientError(response, 'blocked'))  # type: ignore[arg-type]
    service = AIService(client, models=MODELS)  # type: ignore[arg-type]

    report = await service.health()

    assert report.reachable is False
    assert report.error is not None
    assert '403' in report.error
    assert 'x-ollama-auth' in report.error


# -- Ollama SSH tunnel gating -----------------------------------------------------
#
# The tunnel is beta/Windows-only and reuses the DB's SSH creds; only its gating is
# unit-testable (the live SSH path needs infra, like the DB tunnel it mirrors).


async def test_ollama_tunnel_skipped_when_not_beta(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import bot as botmod

    monkeypatch.setattr(botmod, 'beta', False)
    assert await botmod.Bot._open_ollama_tunnel(object()) is None  # type: ignore[arg-type]


async def test_ollama_tunnel_skipped_without_ssh_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import bot as botmod

    monkeypatch.setattr(botmod, 'beta', True)
    monkeypatch.setattr(botmod.DatabaseConfig, 'ssh_host', None)
    assert await botmod.Bot._open_ollama_tunnel(object()) is None  # type: ignore[arg-type]
