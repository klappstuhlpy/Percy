"""Groq chat-completions client (OpenAI-compatible) built on :class:`BaseHTTPClient`.

Groq serves open models behind an OpenAI-style ``/chat/completions`` endpoint. This
client adds the bearer auth and response parsing on top of the shared resilience
layer (429 handling, backoff, circuit breaker). The cog owns prompt construction and
rate limiting; this stays a thin transport wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from app.clients.base import BaseHTTPClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    import aiohttp

__all__ = ('GroqClient', 'GroqResponseError')


class GroqResponseError(RuntimeError):
    """Raised when Groq returns a 2xx response that lacks a usable completion."""


class GroqClient(BaseHTTPClient):
    """Minimal async client for Groq's chat-completions API."""

    BASE_URL: ClassVar[str] = 'https://api.groq.com/openai/v1/'

    def __init__(self, session: aiohttp.ClientSession, *, api_key: str, model: str) -> None:
        super().__init__(session, name='Groq')
        self.api_key: str = api_key
        self.model: str = model

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 600,
    ) -> str:
        """Run a chat completion and return the assistant's reply text.

        Parameters
        ----------
        messages:
            OpenAI-style ``{"role": ..., "content": ...}`` turns (system/user/assistant).
        model:
            Override the default model for this call.
        temperature:
            Sampling temperature.
        max_tokens:
            Upper bound on the completion length.

        Raises
        ------
        GroqResponseError
            If the response is malformed or carries no choices.
        """
        payload: dict[str, Any] = {
            'model': model or self.model,
            'messages': list(messages),
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        headers = {'Authorization': f'Bearer {self.api_key}'}
        data = await self.fetch('POST', 'chat/completions', json=payload, headers=headers)

        try:
            return data['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise GroqResponseError('Groq returned a response without a completion.') from exc
