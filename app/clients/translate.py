"""Lightweight translation client built on :class:`BaseHTTPClient`.

Uses Google's public ``translate_a/single`` endpoint, which needs no API key — keeping
the feature available to self-hosters out of the box. The shared base supplies 429
handling, backoff and a circuit breaker; this wrapper only adds request shaping and
parsing of the (quirky, nested-array) response into a tidy :class:`Translation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from app.clients.base import BaseHTTPClient

if TYPE_CHECKING:
    import aiohttp

__all__ = ('TranslateClient', 'Translation', 'TranslationError')


class TranslationError(RuntimeError):
    """Raised when the translation endpoint returns an unparseable payload."""


@dataclass(frozen=True, slots=True)
class Translation:
    """A finished translation."""

    text: str
    source_language: str
    target_language: str


class TranslateClient(BaseHTTPClient):
    """Minimal async client for Google's keyless translation endpoint."""

    BASE_URL: ClassVar[str] = 'https://translate.googleapis.com/translate_a/single'

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session, name='Translate')

    async def translate(self, text: str, *, target: str = 'en', source: str = 'auto') -> Translation:
        """Translate ``text`` into ``target`` (ISO-639-1), auto-detecting the source by default.

        Raises
        ------
        TranslationError
            If the response cannot be parsed into a translation.
        """
        params = {
            'client': 'gtx',
            'sl': source,
            'tl': target,
            'dt': 't',
            'q': text,
        }
        data: Any = await self.fetch('GET', self.BASE_URL, params=params)

        try:
            translated = ''.join(chunk[0] for chunk in data[0] if chunk and chunk[0])
            detected = data[2] if len(data) > 2 and data[2] else source
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError('Translation endpoint returned an unexpected payload.') from exc

        return Translation(text=translated, source_language=detected, target_language=target)
