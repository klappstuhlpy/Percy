"""``AIService`` — Percy's single entry point for AI inference.

Reached as ``self.bot.ai`` (mirroring ``self.bot.db`` / ``self.bot.render``). Cogs never
construct an :class:`~app.clients.ollama.OllamaClient` or build raw prompts; they call
:meth:`AIService.complete` (free-form text) or :meth:`AIService.parse` (schema-validated
structured output) and treat ``None`` as "AI unavailable — fall back to the non-AI path".

Responsibilities centralised here so every AI caller inherits them:

* **Model-tier selection** — callers pass a :class:`ModelTier`; the service resolves it to
  a configured model tag and can auto-downgrade BALANCED→FAST under load.
* **Graceful degradation** — *any* failure (disabled, transport, timeout, non-JSON,
  schema-invalid) returns ``None``; it never raises into a cog.
* **Concurrency cap** — a process-wide semaphore matches Ollama's ``OLLAMA_NUM_PARALLEL``
  so local CPU inference can't pile up and starve the box.
* **Per-call timeout** — a hard ``asyncio`` ceiling so a slow model falls back instead of
  hanging a command.
* **Exact-match caching** — deterministic structured calls are memoised (keyed on model +
  system prompt + normalised user prompt) to spare repeated CPU-bound inference.

The service is Discord-free and unit-testable with a fake client (see
``tests/test_ai_service.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

from app.clients.base import HTTPClientError
from app.clients.ollama import OllamaResponseError
from app.services.ai.schemas import SchemaError
from app.utils.cache import ExpiringCache

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.clients.ollama import OllamaClient
    from app.services.ai.schemas import Parsable

__all__ = ('AIHealthReport', 'AIService', 'ModelTier')

log = logging.getLogger(__name__)

T = TypeVar('T', bound='Parsable')

#: Latency (ms) above which the health probe flags the engine as degraded, triggering
#: BALANCED→FAST auto-downgrade until it recovers.
DEGRADED_LATENCY_MS = 2500.0


def _describe_probe_error(exc: Exception) -> str:
    """Turn a health-probe failure into a short, actionable reason string.

    Distinguishes a proxy/WAF rejection (401/403 — the host is up but something in front of
    Ollama blocked us) from a genuinely unreachable engine, since Ollama itself has no auth
    and never returns those statuses.
    """
    status = getattr(exc, 'status', None)
    if status in (401, 403):
        return (
            f'HTTP {status} — reached the host but was rejected by an auth/proxy layer in front '
            f'of Ollama (e.g. a reverse proxy or WAF), not the engine being down: Ollama has no '
            f'auth and never returns this. Point OLLAMA_HOST at the instance directly (or use the '
            f'SSH tunnel for local testing) instead of a gated public host.'
        )
    if status is not None:
        return f'HTTP {status} from the Ollama host.'
    if isinstance(exc, TimeoutError):
        return 'timed out — the host did not respond within the probe timeout.'
    return f'{type(exc).__name__}: {exc}'


class ModelTier(Enum):
    """Which model class a call wants. The caller's domain picks the tier.

    FAST — tiny model for latency-sensitive routing/extraction.
    BALANCED — the workhorse for structured decisions (the default coder model).
    SMART — larger model for free-form/conversational replies.
    """

    FAST = 'fast'
    BALANCED = 'balanced'
    SMART = 'smart'


@dataclass(slots=True)
class AIHealthReport:
    """A point-in-time snapshot of the AI engine, surfaced via ``/api/internal/bot/stats``."""

    enabled: bool
    reachable: bool
    degraded: bool
    latency_ms: float | None
    version: str | None
    models: dict[str, str]
    calls: int
    failures: int
    cache_hits: int
    cache_misses: int
    #: When unreachable, a short human-readable reason (HTTP status / transport error),
    #: with a hint when the failure looks like a proxy/WAF block rather than a dead engine.
    error: str | None = None


class AIService:
    """Provider-agnostic facade over the Ollama client. See module docstring."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        models: Mapping[ModelTier, str],
        default_timeout: float = 8.0,
        cache_ttl: float = 300.0,
        cache_maxsize: int = 512,
        max_concurrency: int = 1,
        enabled: bool = True,
    ) -> None:
        self._client = client
        self._models: dict[ModelTier, str] = dict(models)
        self._timeout = default_timeout
        self._enabled = enabled
        self._sem = asyncio.Semaphore(max_concurrency)
        self._cache: ExpiringCache = ExpiringCache(cache_ttl)
        self._cache_maxsize = cache_maxsize
        self._degraded = False

        # Lightweight counters for the health/stats surface.
        self._calls = 0
        self._failures = 0
        self._cache_hits = 0
        self._cache_misses = 0

        # Cached health probe result so the stats endpoint doesn't probe on every hit.
        self._last_health: AIHealthReport | None = None
        self._last_health_at = 0.0

    # -- introspection --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether AI is switched on for this instance (config-level)."""
        return self._enabled

    @property
    def available(self) -> bool:
        """Whether a call is worth attempting right now (enabled + breaker closed)."""
        return self._enabled and not self._client.breaker_open

    def model_for(self, tier: ModelTier) -> str:
        """Resolve a tier to a model tag, applying load-based auto-downgrade."""
        if self._degraded and tier is ModelTier.BALANCED:
            tier = ModelTier.FAST
        return self._models[tier]

    # -- core call paths ------------------------------------------------------

    async def _chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str,
        temperature: float,
        json_mode: bool,
        timeout: float | None,
    ) -> str | None:
        """Single guarded call: concurrency cap + hard timeout + None-on-failure."""
        if not self._enabled:
            return None

        limit = timeout or self._timeout
        self._calls += 1
        try:
            async with self._sem:
                async with asyncio.timeout(limit):
                    return await self._client.chat(
                        messages,
                        model=model,
                        temperature=temperature,
                        json_mode=json_mode,
                        request_timeout=limit,
                    )
        except (HTTPClientError, OllamaResponseError, TimeoutError) as exc:
            self._failures += 1
            log.warning('AI call failed (model=%s): %r', model, exc)
            return None

    async def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        tier: ModelTier = ModelTier.SMART,
        temperature: float = 0.7,
        json_mode: bool = False,
        timeout: float | None = None,
    ) -> str | None:
        """Run a free-form chat completion. Returns the reply text, or ``None`` on failure.

        Used by the conversational assistant. Not cached (conversational, non-deterministic).
        """
        return await self._chat(
            messages,
            model=self.model_for(tier),
            temperature=temperature,
            json_mode=json_mode,
            timeout=timeout,
        )

    async def parse(
        self,
        user_prompt: str,
        *,
        schema: type[T],
        system: str,
        tier: ModelTier = ModelTier.FAST,
        temperature: float = 0.0,
        timeout: float | None = None,
        retry_on_invalid: bool = True,
    ) -> T | None:
        """Return schema-validated structured output for ``user_prompt``, or ``None``.

        Deterministic (``temperature=0.0``, JSON mode) and exact-match cached. On invalid
        JSON / schema violation it optionally retries once with a stricter reminder, then
        degrades to ``None`` so the caller runs its non-AI fallback.
        """
        if not self._enabled:
            return None

        model = self.model_for(tier)
        key = self._cache_key(model, system, user_prompt)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache_hits += 1
            return cached
        self._cache_misses += 1

        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}]
        result = await self._attempt_parse(messages, schema, model, temperature, timeout)

        if result is None and retry_on_invalid and self.available:
            # One stricter retry: remind the model to emit only a JSON object.
            messages = [*messages, {'role': 'user', 'content': 'Return ONLY a valid JSON object, nothing else.'}]
            result = await self._attempt_parse(messages, schema, model, temperature, timeout)

        if result is not None:
            self._remember(key, result)
        return result

    async def _attempt_parse(
        self,
        messages: Sequence[dict[str, str]],
        schema: type[T],
        model: str,
        temperature: float,
        timeout: float | None,
    ) -> T | None:
        raw = await self._chat(messages, model=model, temperature=temperature, json_mode=True, timeout=timeout)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise SchemaError(f'expected a JSON object, got {type(payload).__name__}')
            return schema.from_payload(payload)
        except (json.JSONDecodeError, SchemaError, KeyError, TypeError, ValueError) as exc:
            log.warning('AI parse: invalid structured output for %s: %r', schema.__name__, exc)
            return None

    # -- caching --------------------------------------------------------------

    @staticmethod
    def _cache_key(model: str, system: str, user_prompt: str) -> str:
        """Hash the (model, system prompt, normalised user prompt) into a compact key."""
        normalised = ' '.join(user_prompt.split()).casefold()
        digest = hashlib.sha256(f'{model}\x00{system}\x00{normalised}'.encode()).hexdigest()
        return digest[:32]

    def _remember(self, key: str, value: object) -> None:
        # ExpiringCache has no maxsize bound; cap it crudely to avoid unbounded growth.
        if len(self._cache) >= self._cache_maxsize:
            self._cache.clear()
        self._cache[key] = value

    # -- health ---------------------------------------------------------------

    async def health(self, *, max_age: float = 30.0) -> AIHealthReport:
        """Return a (cached) health snapshot, probing Ollama at most every ``max_age`` s.

        Also updates the auto-downgrade flag: unreachable or slow → degraded.
        """
        now = time.monotonic()
        if self._last_health is not None and (now - self._last_health_at) < max_age:
            return self._refresh_counters(self._last_health)

        reachable = False
        latency_ms: float | None = None
        version: str | None = None
        error: str | None = None

        if self._enabled:
            start = time.perf_counter()
            try:
                async with asyncio.timeout(self._timeout):
                    version = await self._client.version()
                latency_ms = (time.perf_counter() - start) * 1000
                reachable = True
            except (HTTPClientError, OllamaResponseError, TimeoutError) as exc:
                error = _describe_probe_error(exc)
                log.warning('AI health probe failed: %s', error)

        self._degraded = self._enabled and (not reachable or (latency_ms is not None and latency_ms > DEGRADED_LATENCY_MS))

        report = AIHealthReport(
            enabled=self._enabled,
            reachable=reachable,
            degraded=self._degraded,
            latency_ms=round(latency_ms, 1) if latency_ms is not None else None,
            version=version or None,
            models={tier.value: tag for tier, tag in self._models.items()},
            calls=self._calls,
            failures=self._failures,
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
            error=error,
        )
        self._last_health = report
        self._last_health_at = now
        return report

    def _refresh_counters(self, report: AIHealthReport) -> AIHealthReport:
        """Return the cached health report with live counters re-read (no re-probe)."""
        report.calls = self._calls
        report.failures = self._failures
        report.cache_hits = self._cache_hits
        report.cache_misses = self._cache_misses
        return report
