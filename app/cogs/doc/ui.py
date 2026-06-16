from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.core import Context, LayoutView
from app.utils import helpers, truncate

from .html import fence_language, format_version_note
from .models import Admonition, DocItem, DocResult, Member, Operation

if TYPE_CHECKING:
    from .cog import Documentation

#: Thumbnail shown in the top-right of every documentation card.
THUMBNAIL_URL = "https://klappstuhl.me/gallery/raw/lVUYV.png"
#: Total character budget for a Components V2 card (the hard API limit is 4000).
_CONTENT_BUDGET = 3900
#: Pretty labels for the inventory groups, used on the symbol switcher.
_GROUP_LABELS = {
    "class": "\N{PACKAGE} class",
    "method": "\N{WRENCH} method",
    "function": "\N{GEAR} function",
    "attribute": "\N{LABEL} attribute",
    "property": "\N{KEY} property",
    "exception": "\N{POLICE CARS REVOLVING LIGHT} exception",
    "data": "\N{FLOPPY DISK} data",
    "module": "\N{OPEN BOOK} module",
    "doc": "\N{PAGE FACING UP} doc",
}


def _quote(text: str) -> str:
    """Render `text` as a Discord blockquote, prefixing every line so the block stays joined."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _format_admonition(admonition: Admonition) -> str:
    """Render a callout banner as a titled blockquote (``> ### 📝 Note``)."""
    header = f"> ### {admonition.emoji} {admonition.title}"
    if admonition.body:
        return f"{header}\n{_quote(admonition.body)}"
    return header


def _format_operation(operation: Operation) -> str:
    """Render a *Supported Operations* entry, tabbing any version note beneath it."""
    line = f"**`{operation.name}`**"
    if operation.description:
        line += f" — {operation.description}"
    if operation.version:
        # Subtext + arrow visually tabs the version note under the operation it belongs to.
        line += format_version_note(operation.version).rstrip()
    return line


def _format_member(member: Member) -> str:
    """Render a section member as a list entry: a code signature with a one-line summary beneath it."""
    block = f"**`{truncate(member.signature, 240)}`**"
    if member.description:
        block += f"\n{member.description}"
    if member.version:
        block += f"\n-# \N{DOWNWARDS ARROW WITH TIP RIGHTWARDS} {member.version}"
    return block


class _CardBuilder:
    """Lays a :class:`DocResult` out into Components V2 items within a fixed character budget."""

    def __init__(self, item: DocItem) -> None:
        self.item = item
        self.result: DocResult = item.result or DocResult()
        self.container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        self.remaining = _CONTENT_BUDGET

    def _consume(self, text: str) -> str | None:
        """Reserve budget for `text`, truncating it to whatever space is left; None if exhausted."""
        if self.remaining <= 0 or not text:
            return None
        if len(text) > self.remaining:
            text = truncate(text, self.remaining)
        self.remaining -= len(text)
        return text

    def _add_text(self, text: str) -> bool:
        chunk = self._consume(text)
        if chunk is None:
            return False
        self.container.add_item(discord.ui.TextDisplay(chunk))
        return True

    def _add_separator(self) -> None:
        self.container.add_item(discord.ui.Separator())

    def _add_header(self) -> None:
        title = self.result.title or self.item.display_name
        lines = [f"## {discord.utils.escape_markdown(title)}"]
        group = _GROUP_LABELS.get(self.item.group, self.item.group)
        lines.append(f"-# {group}  ·  `{self.item.package}`")
        if self.result.signatures:
            language = fence_language(self.item.domain)
            signatures = "\n".join(self.result.signatures)
            lines.append(f"```{language}\n{signatures}\n```")

        header = self._consume("\n".join(lines)) or lines[0]
        self.container.add_item(
            discord.ui.Section(header, accessory=discord.ui.Thumbnail(THUMBNAIL_URL))
        )

    def _add_description(self) -> None:
        if self.result.description:
            self._add_text(self.result.description)
        if self.result.version_changes:
            notes = "\n".join(f"-# {change}" for change in self.result.version_changes)
            self._add_text(notes)

    def _add_admonitions(self) -> None:
        for admonition in self.result.admonitions:
            if self.remaining < 80:
                break
            self._add_separator()
            if not self._add_text(_format_admonition(admonition)):
                break

    def _add_fields(self) -> None:
        for doc_field in self.result.fields:
            if self.remaining < 80:
                break
            self._add_separator()
            if not self._add_text(f"### {doc_field.name}\n{doc_field.value}"):
                break

    def _add_operations(self) -> None:
        if not self.result.operations:
            return
        self._add_separator()
        self._add_text("### \N{GEAR} Supported Operations")
        for operation in self.result.operations:
            if self.remaining < 60:
                break
            self._add_text(_format_operation(operation))

    def _add_members(self) -> None:
        if not self.result.members:
            return
        self._add_separator()
        self._add_text("### \N{BOOKMARK TABS} Definitions")
        for member in self.result.members:
            if self.remaining < 80:
                self._add_text("-# …more on the documentation page.")
                break
            self._add_text(_format_member(member))

    def _add_footer(self) -> None:
        self._add_separator()
        self.container.add_item(
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="View Documentation",
                    style=discord.ButtonStyle.link,
                    url=self.item.anchor_url,
                )
            )
        )
        self.container.add_item(discord.ui.TextDisplay(f"-# {self.item.package} documentation"))

    def build(self) -> discord.ui.Container:
        self._add_header()
        self._add_description()
        self._add_admonitions()
        self._add_fields()
        self._add_operations()
        self._add_members()
        self._add_footer()
        return self.container


def build_symbol_container(item: DocItem) -> discord.ui.Container:
    """Build a Components V2 documentation card for `item` from its parsed :class:`DocResult`."""
    return _CardBuilder(item).build()


def build_search_container(package: str, query: str, matches: list[DocItem]) -> discord.ui.Container:
    """Build a Components V2 card listing the `matches` of an ``rtfm`` search."""
    container = discord.ui.Container(accent_colour=helpers.Colour.brand())

    header = (
        f"## \N{LEFT-POINTING MAGNIFYING GLASS} {discord.utils.escape_markdown(package)} documentation\n"
        f"-# {len(matches)} result{'' if len(matches) == 1 else 's'} for `{truncate(query, 80)}`"
    )
    container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(THUMBNAIL_URL)))
    container.add_item(discord.ui.Separator())

    lines = [
        f"{_GROUP_LABELS.get(item.group, item.group)}  ·  [`{truncate(item.display_name, 90)}`]({item.anchor_url})"
        for item in matches
    ]
    container.add_item(discord.ui.TextDisplay("\n".join(lines) or "-# No results."))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"-# {package} documentation"))
    return container


class DocSelect(discord.ui.Select):
    """Switches the rendered symbol when several similar matches were found."""

    def __init__(self, view: DocView) -> None:
        super().__init__(placeholder="Select a similar symbol…")
        self._docview: DocView = view  # `parent`/`view` are managed by discord.py
        for index, item in enumerate(view.entries):
            # ``display_name`` is never empty (page/label entries have no anchor); clamp to Discord's
            # 1-100 char option-label limit so the select never 400s.
            self.add_option(
                label=truncate(item.display_name, 100) or "—",
                description=truncate(_GROUP_LABELS.get(item.group, item.group), 100),
                value=str(index),
                default=index == view.index,
            )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._docview.index = int(self.values[0])
        await self._docview.refresh(interaction)


class DocView(LayoutView):
    """A Components V2 documentation card with an optional symbol switcher.

    The symbol body, its fields and a "View Documentation" link all live in one CV2 container, with a
    select beneath it when the lookup returned more than one candidate symbol.
    """

    def __init__(self, cog: Documentation, entries: list[DocItem], *, author: discord.abc.Snowflake) -> None:
        super().__init__(members=author, timeout=450)
        self.cog: Documentation = cog
        self.entries: list[DocItem] = entries
        self.index: int = 0

    async def _compose(self) -> None:
        self.clear_items()
        container = await self.cog.create_symbol_container(self.entries[self.index])
        if container is not None:
            self.add_item(container)
        if len(self.entries) > 1:
            self.add_item(discord.ui.ActionRow(DocSelect(self)))

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self._compose()
        await interaction.response.edit_message(view=self)

    @classmethod
    async def start(cls, ctx: Context, *, entries: list[DocItem], cog: Documentation) -> DocView:
        """Build and send the documentation card for the first matched symbol."""
        self = cls(cog, entries, author=ctx.author)
        await self._compose()
        self.message = await ctx.send(view=self)
        return self


class DocSearchView(LayoutView):
    """A Components V2 card listing ``rtfm`` search results (non-interactive)."""

    def __init__(self, *, package: str, query: str, matches: list[DocItem], author: discord.abc.Snowflake) -> None:
        super().__init__(members=author, timeout=300)
        self.add_item(build_search_container(package, query, matches))

    @classmethod
    async def start(cls, ctx: Context, *, package: str, query: str, matches: list[DocItem]) -> DocSearchView:
        """Build and send the search-results card."""
        self = cls(package=package, query=query, matches=matches, author=ctx.author)
        self.message = await ctx.send(view=self, reference=ctx.replied_reference)
        return self
