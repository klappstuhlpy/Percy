"""Data model + pure helpers for the moderation case log (no ``discord`` imports)."""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

    import asyncpg

__all__ = ('CaseType', 'ModerationCase', 'summarize_case_counts')


class CaseType(enum.Enum):
    """A moderation action type, with its display metadata."""

    WARN = 'warn'
    BAN = 'ban'
    TEMPBAN = 'tempban'
    KICK = 'kick'
    SOFTBAN = 'softban'
    UNBAN = 'unban'
    MUTE = 'mute'
    TEMPMUTE = 'tempmute'
    UNMUTE = 'unmute'

    @classmethod
    def from_action(cls, action: str) -> CaseType | None:
        """Returns the matching type for an action string, or ``None`` if unknown."""
        try:
            return cls(action)
        except ValueError:
            return None

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def colour(self) -> int:
        return _COLOURS[self]

    @property
    def emoji(self) -> str:
        return _EMOJIS[self]


_LABELS: dict[CaseType, str] = {
    CaseType.WARN: 'Warn',
    CaseType.BAN: 'Ban',
    CaseType.TEMPBAN: 'Tempban',
    CaseType.KICK: 'Kick',
    CaseType.SOFTBAN: 'Softban',
    CaseType.UNBAN: 'Unban',
    CaseType.MUTE: 'Mute',
    CaseType.TEMPMUTE: 'Tempmute',
    CaseType.UNMUTE: 'Unmute',
}

# Reds for removals, oranges for kicks, yellow for warns, greens for reversals, grey for mutes.
_COLOURS: dict[CaseType, int] = {
    CaseType.WARN: 0xF1C40F,
    CaseType.BAN: 0xE74C3C,
    CaseType.TEMPBAN: 0xE67E22,
    CaseType.KICK: 0xE67E22,
    CaseType.SOFTBAN: 0xE67E22,
    CaseType.UNBAN: 0x2ECC71,
    CaseType.MUTE: 0x95A5A6,
    CaseType.TEMPMUTE: 0x95A5A6,
    CaseType.UNMUTE: 0x2ECC71,
}

_EMOJIS: dict[CaseType, str] = {
    CaseType.WARN: '\N{WARNING SIGN}',
    CaseType.BAN: '\N{HAMMER}',
    CaseType.TEMPBAN: '\N{HAMMER}',
    CaseType.KICK: '\N{WOMANS BOOTS}',
    CaseType.SOFTBAN: '\N{HAMMER}',
    CaseType.UNBAN: '\N{DOVE OF PEACE}',
    CaseType.MUTE: '\N{SPEAKER WITH CANCELLATION STROKE}',
    CaseType.TEMPMUTE: '\N{SPEAKER WITH CANCELLATION STROKE}',
    CaseType.UNMUTE: '\N{SPEAKER}',
}


@dataclass(frozen=True, slots=True)
class ModerationCase:
    """A single moderation log entry."""

    id: int
    guild_id: int
    index: int
    action: str
    target_id: int
    moderator_id: int | None
    reason: str | None
    log_message_id: int | None
    created_at: datetime.datetime

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> ModerationCase:
        return cls(
            id=record['id'],
            guild_id=record['guild_id'],
            index=record['case_index'],
            action=record['action'],
            target_id=record['target_id'],
            moderator_id=record['moderator_id'],
            reason=record['reason'],
            log_message_id=record['log_message_id'],
            created_at=record['created_at'],
        )

    @property
    def type(self) -> CaseType | None:
        return CaseType.from_action(self.action)


def summarize_case_counts(actions: Iterable[str]) -> str:
    """Summarize a target's case history as e.g. ``"3 warns, 1 ban"``.

    Counts are reported in :class:`CaseType` declaration order; unknown actions are
    ignored. Returns ``"no cases"`` when empty.
    """
    counts = Counter(actions)
    parts = []
    for case_type in CaseType:
        count = counts.get(case_type.value, 0)
        if count:
            suffix = '' if count == 1 else 's'
            parts.append(f'{count} {case_type.label.lower()}{suffix}')
    return ', '.join(parts) or 'no cases'
