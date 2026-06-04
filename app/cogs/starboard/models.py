from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

__all__ = ('DEFAULT_EMOJI', 'DEFAULT_THRESHOLD', 'StarboardConfig')

DEFAULT_EMOJI = '\N{WHITE MEDIUM STAR}'
DEFAULT_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class StarboardConfig:
    """A snapshot of a guild's starboard settings."""

    guild_id: int
    channel_id: int | None
    threshold: int
    emoji: str
    self_star: bool
    enabled: bool
    ignored_channel_ids: frozenset[int]

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> StarboardConfig:
        return cls(
            guild_id=record['guild_id'],
            channel_id=record['channel_id'],
            threshold=record['threshold'],
            emoji=record['emoji'],
            self_star=record['self_star'],
            enabled=record['enabled'],
            ignored_channel_ids=frozenset(record['ignored_channel_ids'] or ()),
        )

    @property
    def is_active(self) -> bool:
        """Whether the starboard is enabled *and* has a destination channel configured."""
        return self.enabled and self.channel_id is not None
