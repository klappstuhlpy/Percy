"""Pure decision logic for the starboard — no ``discord`` imports.

Given a message's current star count and the guild's configured threshold, decide
whether to create, update, delete, or ignore the corresponding starboard post, and
which star emoji tier to display. Unit-tested without a bot instance.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

__all__ = ('StarboardAction', 'color_for_stars', 'decide_action', 'is_too_old', 'star_emoji_for')


class StarboardAction(enum.Enum):
    """The action the cog should take for a message after a star change."""

    IGNORE = enum.auto()
    CREATE = enum.auto()
    UPDATE = enum.auto()
    DELETE = enum.auto()


def decide_action(*, star_count: int, threshold: int, has_entry: bool) -> StarboardAction:
    """Decide what to do with a message given its star count and whether it's posted.

    Parameters
    ----------
    star_count:
        The number of qualifying star reactions on the original message.
    threshold:
        The minimum stars required to appear on the starboard.
    has_entry:
        Whether the message already has a starboard post.

    Returns
    -------
    StarboardAction
        ``CREATE`` to post it for the first time, ``UPDATE`` to refresh the count,
        ``DELETE`` to remove a post that fell below the threshold, or ``IGNORE``.
    """
    if star_count >= threshold:
        return StarboardAction.UPDATE if has_entry else StarboardAction.CREATE
    return StarboardAction.DELETE if has_entry else StarboardAction.IGNORE


#: Star-count tiers and their display emoji, highest tier first.
_EMOJI_TIERS: tuple[tuple[int, str], ...] = (
    (15, '\N{SPARKLES}'),
    (10, '\N{DIZZY SYMBOL}'),
    (5, '\N{GLOWING STAR}'),
    (0, '\N{WHITE MEDIUM STAR}'),
)


def star_emoji_for(count: int) -> str:
    """Return the display star emoji for a given star count (it escalates with popularity)."""
    for floor, emoji in _EMOJI_TIERS:
        if count >= floor:
            return emoji
    return _EMOJI_TIERS[-1][1]


#: Colour ramp endpoints (RGB tuples). The embed colour interpolates between these as a
#: post climbs from the threshold toward ``threshold + _COLOR_RAMP_SPAN`` extra stars:
#: a pale gold for freshly-qualified posts deepening into a warm amber for very popular ones.
_COLOR_FLOOR: tuple[int, int, int] = (0xF8, 0xDB, 0x5E)  # energy yellow (matches helpers.Colour.energy_yellow)
_COLOR_CEIL: tuple[int, int, int] = (0xF1, 0x9B, 0x2C)  # warm amber/gold
#: How many stars *beyond the threshold* it takes to reach the top of the ramp.
_COLOR_RAMP_SPAN = 15


def color_for_stars(count: int, threshold: int) -> int:
    """Return an RGB ``int`` for a post's embed, warming as it gathers more stars.

    The colour sits at :data:`_COLOR_FLOOR` once a post just meets ``threshold`` and
    interpolates linearly toward :data:`_COLOR_CEIL`, reaching it ``_COLOR_RAMP_SPAN``
    stars past the threshold. Counts below the threshold clamp to the floor; counts far
    above clamp to the ceiling. The result is usable directly as ``discord.Colour(value)``.
    """
    span = max(_COLOR_RAMP_SPAN, 1)
    progress = (count - threshold) / span
    progress = min(1.0, max(0.0, progress))

    r = round(_COLOR_FLOOR[0] + (_COLOR_CEIL[0] - _COLOR_FLOOR[0]) * progress)
    g = round(_COLOR_FLOOR[1] + (_COLOR_CEIL[1] - _COLOR_FLOOR[1]) * progress)
    b = round(_COLOR_FLOOR[2] + (_COLOR_CEIL[2] - _COLOR_FLOOR[2]) * progress)
    return (r << 16) | (g << 8) | b


def is_too_old(created_at: datetime.datetime, now: datetime.datetime, max_age_hours: int) -> bool:
    """Whether a message is too old to be eligible for the starboard.

    ``max_age_hours`` of ``0`` (or less) disables the age limit, so nothing is ever too old.
    Otherwise a message is too old once it is *strictly* older than ``max_age_hours``; a
    message exactly at the boundary is still eligible.
    """
    if max_age_hours <= 0:
        return False
    age_hours = (now - created_at).total_seconds() / 3600
    return age_hours > max_age_hours
