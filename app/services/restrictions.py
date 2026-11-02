"""Pure rules for member restrictions (voice ban, frozen nickname, channel mute). Discord-free.

The SQL is :mod:`app.database.repositories.restrictions`; the Discord side is
:mod:`app.cogs.restrictions.actions`.
"""

from __future__ import annotations

import datetime

__all__ = (
    'CHANNEL_MUTE_DENIALS',
    'MAX_NICK_LENGTH',
    'NO_CHANNEL',
    'RESTRICTION_ACTIONS',
    'is_current',
    'normalize_nickname',
)

#: Member actions this module owns (none of them can be filed as a request).
RESTRICTION_ACTIONS = frozenset({
    'voicekick', 'voiceban', 'voiceunban', 'nick', 'nickfreeze', 'nickunfreeze', 'channelmute', 'channelunmute',
})

#: ``member_restrictions.channel_id`` for a kind that is not channel-scoped.
NO_CHANNEL = 0

MAX_NICK_LENGTH = 32  # Discord's limit

#: Permission names a channel mute denies on the member's overwrite.
CHANNEL_MUTE_DENIALS = ('send_messages', 'add_reactions', 'send_messages_in_threads', 'speak')


def normalize_nickname(name: str | None) -> str | None:
    """A trimmed nickname, or ``None`` for blank (reset). Raises ``ValueError`` when too long."""
    name = (name or '').strip()
    if not name:
        return None
    if len(name) > MAX_NICK_LENGTH:
        raise ValueError(f'a nickname is at most {MAX_NICK_LENGTH} characters')
    return name


def is_current(stored_expiry: datetime.datetime | None, timer_expiry: datetime.datetime) -> bool:
    """Whether an expiring timer still owns the stored restriction (both naive UTC or both aware).

    A restriction re-applied with a new duration (or made permanent) leaves the older timer
    behind; that timer must not lift it. ``stored_expiry`` ``None`` means permanent now.
    """
    if stored_expiry is None:
        return False
    return abs((_naive(stored_expiry) - _naive(timer_expiry)).total_seconds()) < 2


def _naive(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value.tzinfo else value
