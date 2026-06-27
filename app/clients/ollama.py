"""Ollama chat client built on :class:`BaseHTTPClient`.

Percy's inference runs on a self-hosted `Ollama <https://ollama.com>`_ instance. This
client talks to Ollama's native ``/api/chat`` endpoint (non-streaming) and adds response
parsing on top of the shared resilience layer (429 handling, transport backoff, circuit
breaker). It stays a thin transport wrapper: the :class:`~app.services.ai.AIService`
owns prompt construction, model-tier selection, caching, timeouts and concurrency.

See ``docs/ai/AI_INTEGRATION_BLUEPRINT.md`` for the wider design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp

from app.clients.base import BaseHTTPClient

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('OllamaClient', 'OllamaResponseError')


class OllamaResponseError(RuntimeError):
    """Raised when Ollama returns a 2xx response that lacks a usable completion."""


class OllamaClient(BaseHTTPClient):
    """Minimal async client for a self-hosted Ollama instance (``/api/chat``)."""

    #: Default host; overridden per-instance from config in :meth:`__init__`.
    BASE_URL: ClassVar[str] = 'http://127.0.0.1:11434/'

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        host: str | None = None,
        default_model: str = 'qwen2.5-coder:3b',
    ) -> None:
        super().__init__(session, name='Ollama')
        # Instance attribute shadows the class BASE_URL so a deployment can point at a
        # remote/alternate Ollama host. yarl joins need a trailing slash to keep the path.
        self.BASE_URL = (host or type(self).BASE_URL).rstrip('/') + '/'
        self.default_model: str = default_model

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        json_mode: bool = False,
        request_timeout: float | None = None,
    ) -> str:
        """Run a chat completion and return the assistant's reply text.

        Parameters
        ----------
        messages:
            OpenAI-style ``{"role": ..., "content": ...}`` turns (system/user/assistant).
        model:
            Ollama model tag to run (e.g. ``qwen2.5-coder:3b``). Falls back to the default.
        temperature:
            Sampling temperature; ``0.0`` for deterministic structured routing.
        json_mode:
            When ``True``, asks Ollama to constrain output to a JSON object
            (``"format": "json"``) — used for schema-enforced structured calls.
        request_timeout:
            Optional per-request transport timeout (seconds). The service also applies a
            hard ``asyncio`` ceiling on top of this.

        Raises
        ------
        OllamaResponseError
            If the response is malformed or carries no message content.
        HTTPClientError / CircuitBreakerOpen
            On transport/HTTP failure (handled by the resilience layer).
        """
        payload: dict[str, Any] = {
            'model': model or self.default_model,
            'messages': list(messages),
            'stream': False,
            'options': {'temperature': temperature},
        }
        if json_mode:
            payload['format'] = 'json'

        kwargs: dict[str, Any] = {}
        if request_timeout is not None:
            kwargs['timeout'] = aiohttp.ClientTimeout(total=request_timeout)

        data = await self.fetch('POST', 'api/chat', json=payload, **kwargs)

        try:
            return data['message']['content'].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise OllamaResponseError('Ollama returned a response without message content.') from exc

    async def version(self) -> str:
        """Return the running Ollama version, used as a lightweight reachability probe.

        Raises the usual transport/HTTP errors if the instance is unreachable.
        """
        data = await self.fetch('GET', 'api/version')
        if isinstance(data, dict):
            return str(data.get('version', ''))
        return ''
