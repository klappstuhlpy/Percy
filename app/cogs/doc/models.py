from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

import discord

MAX_SIGNATURE_AMOUNT = 3
PRIORITY_PACKAGES = ("python",)

#: Emoji used to decorate the different admonition banners when rendering a symbol card.
ADMONITION_EMOJI: dict[str, str] = {
    "note": "\N{MEMO}",
    "warning": "\N{WARNING SIGN}",
    "important": "\N{HEAVY EXCLAMATION MARK SYMBOL}",
    "caution": "\N{WARNING SIGN}",
    "danger": "\N{NO ENTRY}",
    "error": "\N{CROSS MARK}",
    "hint": "\N{ELECTRIC LIGHT BULB}",
    "tip": "\N{ELECTRIC LIGHT BULB}",
    "attention": "\N{WARNING SIGN}",
    "see also": "\N{RIGHT-POINTING MAGNIFYING GLASS}",
    "deprecated": "\N{WARNING SIGN}",
    "admonition": "\N{INFORMATION SOURCE}",
}


@dataclass(slots=True)
class Admonition:
    """A callout block (``Note``, ``Warning``, ``Tip``, …) pulled out of a symbol's description."""

    title: str
    body: str
    kind: str = "note"

    @property
    def emoji(self) -> str:
        return ADMONITION_EMOJI.get(self.kind.lower(), ADMONITION_EMOJI["admonition"])


@dataclass(slots=True)
class Operation:
    """A single entry of a class' *Supported Operations* table."""

    name: str
    description: str
    #: A ``New in version`` / ``Changed in version`` note that belongs *under* this operation.
    version: str | None = None


@dataclass(slots=True)
class DocField:
    """A named field of a symbol (``Parameters``, ``Raises``, ``Returns``, ``Return type``, …)."""

    name: str
    value: str


@dataclass(slots=True)
class Member:
    """A nested definition listed under a *section* lookup (a struct, function, macro, attribute, …).

    Used to render the individual entries of a documentation *category* page — e.g. CPython's
    "Create Config" — as a tidy list where each signature is clearly tied to its own description.
    """

    signature: str
    description: str
    version: str | None = None
    #: Sphinx domain the member belongs to (``c``, ``py``, ``cpp``, …) — drives code highlighting.
    domain: str = "py"


@dataclass(slots=True)
class DocResult:
    """The fully parsed representation of a documentation symbol.

    This replaces the previous "single markdown blob + loose field dict" approach: scraping now
    yields a structured object so the renderer can lay out signatures, the description, callout
    banners, version notes, supported operations, field lists and section members independently
    (and cache the lot). It is intentionally domain-agnostic so it works across any Sphinx site
    (discord.py, CPython's Python *and* C API, aiohttp, …), not just a single project.
    """

    description: str = ""
    #: An optional nicer heading than the raw symbol id (e.g. a section's title).
    title: str = ""
    signatures: list[str] = field(default_factory=list)
    fields: list[DocField] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    admonitions: list[Admonition] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    #: Top-level ``New in version`` / ``Changed in version`` / ``Deprecated`` notes for the symbol.
    version_changes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.description,
                self.signatures,
                self.fields,
                self.operations,
                self.admonitions,
                self.members,
                self.version_changes,
            )
        )


class DocItem:
    """Holds inventory symbol information and its (lazily scraped) parsed documentation."""

    __slots__ = ("base_url", "domain", "group", "name", "package", "relative_url_path", "result", "symbol_id")

    def __init__(
        self,
        package: str,
        group: str,
        base_url: str,
        relative_url_path: str,
        symbol_id: str,
        result: DocResult | None = None,
        domain: str = "py",
        name: str = "",
    ) -> None:
        self.package: str = package
        self.group: str = group
        self.base_url: str = base_url
        self.relative_url_path: str = relative_url_path
        self.symbol_id: str = symbol_id
        #: The inventory symbol name (e.g. ``PyConfig_Read``, ``reference/Image``). Unlike ``symbol_id``
        #: (the page anchor) this is always present — page/label entries have no anchor at all.
        self.name: str = name
        #: Sphinx domain (``py``, ``c``, ``cpp``, ``std``, …); used to pick the code-fence language.
        self.domain: str = domain
        #: Cached parsed documentation, so re-rendering the CV2 card never re-scrapes.
        self.result: DocResult | None = result

    def __str__(self) -> str:
        return f"{self.package}.{self.symbol_id or self.name}"

    def __repr__(self) -> str:
        return f"<DocItem package={self.package!r} name={self.name!r} symbol_id={self.symbol_id!r}>"

    @property
    def display_name(self) -> str:
        """A always-non-empty, user-facing label for the symbol (never the bare page anchor)."""
        return self.name or self.symbol_id or "—"

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol's *page* (no anchor; used as the page cache/batch key)."""
        return self.base_url + self.relative_url_path

    @property
    def anchor_url(self) -> str:
        """Return the absolute url to the symbol, including its ``#anchor`` when it has one."""
        return f"{self.url}#{self.symbol_id}" if self.symbol_id else self.url


DocItemT = TypeVar("DocItemT", bound=DocItem | discord.Embed)
