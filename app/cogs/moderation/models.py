from __future__ import annotations

import enum
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence


class FlaggedMember:
    __slots__ = ("display_name", "id", "joined_at", "messages")

    def __init__(self, user: discord.abc.User | discord.Member, joined_at: datetime.datetime) -> None:
        self.id = user.id
        self.display_name = str(user)
        self.joined_at = joined_at
        self.messages: int = 0

    @property
    def created_at(self) -> datetime.datetime:
        return discord.utils.snowflake_time(self.id)

    def __str__(self) -> str:
        return self.display_name


class SpamCheckerResult:
    def __init__(self, reason: str) -> None:
        self.reason: str = reason

    def __str__(self) -> str:
        return self.reason

    @classmethod
    def spammer(cls) -> SpamCheckerResult:
        return cls("Auto-ban for spamming")

    @classmethod
    def flagged_mention(cls) -> SpamCheckerResult:
        return cls("Auto-ban for suspicious mentions")


class SpammerSequence(SpamCheckerResult):
    """A sequence of spammers."""

    def __init__(self, members: Sequence[discord.abc.Snowflake], *, reason: str = "Auto-ban for spamming") -> None:
        super().__init__(reason)
        self.members: Sequence[discord.abc.Snowflake] = members


class MemberJoinType(enum.Enum):
    FAST = 1
    SUSPICOUS = 2
