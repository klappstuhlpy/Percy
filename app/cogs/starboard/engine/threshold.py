"""Pure decision logic for the starboard — no ``discord`` imports.

Given a message's current star count and the guild's configured threshold, decide
whether to create, update, delete, or ignore the corresponding starboard post, and
which star emoji tier to display. Unit-tested without a bot instance.
"""

from __future__ import annotations

import enum

__all__ = ('StarboardAction', 'decide_action', 'star_emoji_for')


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
