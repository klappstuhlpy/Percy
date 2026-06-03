"""Purge message-filter construction.

Extracted from the ``moderation`` cog's ``purge`` command. Turning the command's
flags into a single message predicate -- combining the per-criterion filters under
``all``/``any`` and deciding whether an empty filter should prompt for confirmation --
is pure logic, so it lives here free of Discord and is unit-testable.

The predicate operates on Discord messages structurally (via the :class:`PurgeMessage`
protocol), so the service never imports ``discord`` at runtime: tests pass lightweight
fakes and the cog passes real ``discord.Message`` objects, both of which satisfy the
protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sized

__all__ = (
    "PurgeMessage",
    "PurgePlan",
    "build_purge_predicate",
)

# Custom emoji form, e.g. ``<:name:1234>`` -- matches the cog's original pattern.
EMOJI_REGEX = re.compile(r"<:(\w+):(\d+)>")


class PurgeAuthor(Protocol):
    bot: bool


class PurgeMessage(Protocol):
    """The subset of a Discord message the purge predicates read."""

    author: PurgeAuthor
    webhook_id: int | None
    interaction: object | None
    embeds: Sized
    attachments: Sized
    reactions: Sized
    content: str
    pinned: bool


@dataclass(slots=True)
class PurgePlan:
    """The compiled purge filter and whether an empty filter needs confirmation."""

    predicate: Callable[[PurgeMessage], bool]
    require_prompt: bool


def build_purge_predicate(
    *,
    bot: bool = False,
    webhooks: bool = False,
    embeds: bool = False,
    files: bool = False,
    reactions: bool = False,
    emoji: bool = False,
    user: object | None = None,
    contains: str | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
    delete_pinned: bool = True,
    require: Literal["any", "all"] = "all",
) -> PurgePlan:
    """Compile the purge flags into a single message predicate.

    Mirrors the cog logic exactly. ``bot`` removes bot messages (excluding webhooks
    unless ``webhooks`` is also set, but always keeping interaction responses); the
    other flags filter on embeds, attachments, reactions, custom emoji, author, and
    substring/prefix/suffix matches. Unless ``delete_pinned`` is set, pinned messages
    are spared. The criteria are combined with ``all`` (default) or ``any`` per
    ``require``. When no criterion is given the predicate matches everything and
    ``require_prompt`` is True, signalling the caller to confirm the bulk delete.
    """
    predicates: list[Callable[[PurgeMessage], object]] = []

    if bot:
        if webhooks:
            predicates.append(lambda m: m.author.bot)
        else:
            predicates.append(lambda m: (m.webhook_id is None or m.interaction is not None) and m.author.bot)
    elif webhooks:
        predicates.append(lambda m: m.webhook_id is not None)

    if embeds:
        predicates.append(lambda m: len(m.embeds))

    if files:
        predicates.append(lambda m: len(m.attachments))

    if reactions:
        predicates.append(lambda m: len(m.reactions))

    if emoji:
        predicates.append(lambda m: EMOJI_REGEX.search(m.content))

    if user is not None:
        predicates.append(lambda m: m.author == user)

    if contains:
        predicates.append(lambda m: contains in m.content)

    if prefix:
        predicates.append(lambda m: m.content.startswith(prefix))

    if suffix:
        predicates.append(lambda m: m.content.endswith(suffix))

    if not delete_pinned:
        predicates.append(lambda m: not m.pinned)

    require_prompt = False
    if not predicates:
        require_prompt = True
        predicates.append(lambda _: True)

    op = all if require == "all" else any

    def predicate(m: PurgeMessage) -> bool:
        return op(p(m) for p in predicates)

    return PurgePlan(predicate=predicate, require_prompt=require_prompt)
