from __future__ import annotations

from typing import Any, TypeVar

import discord

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = ("python",)


class DocItem:
    """Holds inventory symbol information."""

    def __init__(
        self,
        package: str,
        group: str,
        base_url: str,
        relative_url_path: str,
        symbol_id: str,
        resolved_fields: dict[str, str] | None = None,
        embed: discord.Embed | None = None,
    ) -> None:
        self.package: str = package
        self.group: str = group
        self.base_url: str = base_url
        self.relative_url_path: str = relative_url_path
        self.symbol_id: str = symbol_id
        self.embed: discord.Embed | None = embed
        #: Cached scraped markdown body, so re-rendering the CV2 card never re-scrapes.
        self.markdown: str | None = None

        self.resolved_fields: dict[str, Any] = resolved_fields or {}

    def __str__(self) -> str:
        return f"{self.package}.{self.symbol_id}"

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


DocItemT = TypeVar("DocItemT", bound=DocItem | discord.Embed)
