"""Reusable Components V2 building blocks (discord.py 2.6+).

Components V2 lets a message mix text, media and interactive components inside a
single :class:`discord.ui.LayoutView` instead of the classic ``content``/``embed``
split. A CV2 message **cannot** also carry ``content``, ``embeds``, stickers or polls,
so the helpers here return a ready :class:`discord.ui.LayoutView` that callers send on
its own (``await ctx.send(view=...)``).

This module keeps the raw component plumbing in one place so feature cogs can compose
an embed-like card (heading, body, fields, thumbnail, footer) without repeating the
container/section/separator boilerplate. It lives in ``app/core`` like the rest of the
framework and is re-exported from :mod:`app.core`.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import discord

from app.utils.helpers import Colour

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    'Accent',
    'NoticeView',
    'make_notice',
)


class Accent(enum.Enum):
    """Semantic accent colours for a :class:`discord.ui.Container`'s rail.

    Mirrors the tone of the classic :meth:`~app.core.Context.send_success` /
    ``send_error`` helpers so CV2 cards read consistently with the rest of the bot.
    """

    success = Colour.lime_green()
    error = Colour.darker_red()
    warning = Colour.light_orange()
    info = Colour.royal_blue()
    neutral = Colour.white()

    @property
    def colour(self) -> discord.Colour:
        return self.value


class NoticeView(discord.ui.LayoutView):
    """A self-contained, non-interactive CV2 card.

    Defaults to ``timeout=None`` because a display-only layout has no components to
    expire; pass a timeout only when you later attach interactive children.
    """

    def __init__(self, container: discord.ui.Container, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.add_item(container)


def make_notice(
    title: str,
    description: str | None = None,
    *,
    accent: Accent | discord.Colour | int = Accent.neutral,
    thumbnail: str | None = None,
    fields: Iterable[tuple[str, str]] | None = None,
    footer: str | None = None,
) -> NoticeView:
    """Build an embed-like Components V2 card.

    Parameters
    ----------
    title:
        The heading, rendered as bold markdown text.
    description:
        Optional body markdown shown beneath the heading.
    accent:
        The container's left-rail colour; an :class:`Accent` or a raw colour/int.
    thumbnail:
        Optional image URL placed beside the heading/body via a section accessory.
    fields:
        Optional ``(name, value)`` pairs rendered after a separator, embed-style.
    footer:
        Optional muted line rendered last, after a separator.

    Returns
    -------
    NoticeView
        A ready-to-send layout view (send it with ``view=`` and no content/embed).
    """
    colour = accent.colour if isinstance(accent, Accent) else accent
    container = discord.ui.Container(accent_colour=colour)

    heading = f'## {title}'
    body = f'{heading}\n{description}' if description else heading

    if thumbnail is not None:
        container.add_item(
            discord.ui.Section(body, accessory=discord.ui.Thumbnail(thumbnail))
        )
    else:
        container.add_item(discord.ui.TextDisplay(body))

    field_list = list(fields) if fields is not None else []
    if field_list:
        container.add_item(discord.ui.Separator())
        for name, value in field_list:
            container.add_item(discord.ui.TextDisplay(f'**{name}**\n{value}'))

    if footer is not None:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'-# {footer}'))

    return NoticeView(container)
