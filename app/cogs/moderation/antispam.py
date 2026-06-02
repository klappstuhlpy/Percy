from __future__ import annotations

import datetime
from operator import attrgetter
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.utils import ListedRateLimit, RateLimit, cache

from .models import FlaggedMember, MemberJoinType, SpamCheckerResult, SpammerSequence

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from app.database.base import Gatekeeper, GuildConfig


class SpamChecker:
    """This spam checker does a few things.

    1) It checks if a user has spammed more than 10 times in 12 seconds
    2) It checks if the content has been spammed 15 times in 17 seconds.
    3) It checks if new users have spammed 30 times in 35 seconds.
    4) It checks if 'fast joiners' have spammed 10 times in 12 seconds.
    5) It checks if a member spammed `config.mention_count * 2` mentions in 12 seconds.
    6) It checks if a member hits and runs 10 times in 12 seconds.

    The second case is meant to catch alternating spambots while the first one
    just catches regular singular spambots.
    From experience, these values aren't reached unless someone is actively spamming.
    """

    def __init__(self) -> None:
        self.by_content = RateLimit(5, 15.0, key=lambda msg: (msg.channel.id, msg.content))
        self.by_user = RateLimit(10, 12.0, key=lambda msg: msg.author.id)
        self.new_user = RateLimit(30, 35.0, key=lambda msg: msg.channel.id)

        self.last_join: datetime.datetime | None = None
        self.last_member: discord.Member | None = None

        self._by_mentions: commands.CooldownMapping | None = None
        self._by_mentions_rate: int | None = None

        self._join_rate: tuple[int, int] | None = None
        self.auto_gatekeeper: ListedRateLimit | None = None
        # Enabled if alerts are on but gatekeeper isn't
        self._default_join_spam = ListedRateLimit(10, 5, key=attrgetter('joined_at'))

        self.last_created: datetime.datetime | None = None

        self.flagged_users: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        self.hit_and_run = RateLimit(5, 15, key=lambda msg: msg.channel.id, tagger=lambda msg: msg.author)

    def get_flagged_member(self, user_id: int, /) -> FlaggedMember | None:
        """Get a flagged member."""
        return self.flagged_users.get(user_id)

    def is_flagged(self, user_id: int, /) -> bool:
        """Check if a user is flagged."""
        return user_id in self.flagged_users

    def flag_member(self, member: discord.Member, /) -> None:
        """Flag a member."""
        self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())

    def by_mentions(self, config: GuildConfig) -> commands.CooldownMapping | None:
        """Get the cooldown mapping for mentions.

        This will return a cooldown mapping for mentions if the mention count is set, otherwise None.

        Parameters
        ----------
        config: :class:`GuildConfig`
            The guild configuration to check.
        """
        if not config.mention_count:
            return None

        mention_threshold = config.mention_count
        if self._by_mentions_rate != mention_threshold:
            self._by_mentions = commands.CooldownMapping.from_cooldown(
                mention_threshold, 15, commands.BucketType.member)
            self._by_mentions_rate = mention_threshold
        return self._by_mentions

    @staticmethod
    def is_new(member: discord.Member) -> bool:
        """Check if a member is new.

        This checks if a member is new by checking if they were created less than 90 days ago and joined less than 7 days ago.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        now = discord.utils.utcnow()
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago and member.joined_at is not None and member.joined_at > seven_days_ago

    def is_spamming(self, message: discord.Message) -> SpamCheckerResult | None:
        """Check if a message is spamming.

        This will return a :class:`SpamCheckerResult` if the message is spamming, otherwise None.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message to check.
        """
        if message.guild is None:
            return None

        flagged = self.flagged_users.get(message.author.id)
        if flagged is not None:
            flagged.messages += 1
            spammers = self.hit_and_run.is_ratelimited(message)
            if spammers:
                return SpammerSequence(spammers)  # type: ignore[arg-type]

            if (
                    (flagged.messages <= 10
                    and message.raw_mentions)
                    or '@everyone' in message.content
                    or '@here' in message.content
            ):
                return SpamCheckerResult.flagged_mention()

        if self.is_new(message.author) and self.new_user.is_ratelimited(message):  # type: ignore[arg-type]
            return SpamCheckerResult.spammer()

        if self.by_user.is_ratelimited(message):
            return SpamCheckerResult.spammer()

        if self.by_content.is_ratelimited(message):
            return SpamCheckerResult.spammer()

        return None

    def is_fast_join(self, member: discord.Member) -> bool:
        """Check if a member is a fast joiner.

        This will return True if the member is a fast joiner, False otherwise.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        joined = member.joined_at or discord.utils.utcnow()
        if self.last_join is None:
            self.last_join = joined
            return False
        is_fast = (joined - self.last_join).total_seconds() <= 2.0
        self.last_join = joined
        if is_fast:
            self.flagged_users[member.id] = FlaggedMember(member, joined)
        return is_fast

    def is_suspicious_join(self, member: discord.Member) -> bool:
        """Check if a member is suspicious.

        This will return True if the member is suspicious, False otherwise.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        created = member.created_at
        if self.last_created is None:
            self.last_created = created
            return False

        is_suspicious = abs((created - self.last_created).total_seconds()) <= 86400.0
        self.last_created = created
        if is_suspicious:
            self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())
        return is_suspicious

    def get_join_type(self, member: discord.Member) -> MemberJoinType | None:
        """Get the join type of member.

        This will return the join type of member, if any.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        joined = member.joined_at or discord.utils.utcnow()

        if self.last_member is None:
            self.last_member = member
            self.last_join = joined
            return None

        if self.last_join is not None:
            is_fast = (joined - self.last_join).total_seconds() <= 2.0
            self.last_join = joined
            if is_fast:
                self.flagged_users[member.id] = FlaggedMember(member, joined)
                if self.last_member.id not in self.flagged_users:
                    self.flag_member(self.last_member)
                return MemberJoinType.FAST

        is_suspicious = abs((member.created_at - self.last_member.created_at).total_seconds()) <= 86400.0
        if is_suspicious:
            self.flagged_users[member.id] = FlaggedMember(member, joined)
            if self.last_member.id not in self.flagged_users:
                self.flag_member(self.last_member)
            return MemberJoinType.SUSPICOUS

        return None

    def is_mention_spam(self, message: discord.Message, config: GuildConfig) -> bool:
        """Check if a message is mention spam.

        This will return True if the message is mention spam, False otherwise.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message to check.
        config: :class:`GuildConfig`
            The guild configuration to check against.
        """
        mapping = self.by_mentions(config)
        if mapping is None:
            return False

        current = message.created_at.timestamp()
        mention_bucket = mapping.get_bucket(message, current)
        mention_count = sum(not m.bot and m.id != message.author.id for m in message.mentions)
        return mention_bucket is not None and mention_bucket.update_rate_limit(
            current, tokens=mention_count) is not None

    def check_gatekeeper(self, member: discord.Member, gatekeeper: Gatekeeper) -> list[discord.Member]:
        """Check if a member is ratelimited by the gatekeeper.

        This will return a list of members that are ratelimited.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        gatekeeper: :class:`Gatekeeper
            The gatekeeper to check against.
        """
        if gatekeeper.started_at is not None:
            return []

        rate = gatekeeper.rate
        if rate is None:
            self._join_rate = None
            return []

        if rate != self._join_rate:
            # Might be worth considering swapping over the tat/member list? Probably complicated though
            self.auto_gatekeeper = ListedRateLimit(int(rate[0]), int(rate[1]), key=attrgetter('joined_at'))
            self._join_rate = rate  # type: ignore[arg-type]

        if self.auto_gatekeeper is not None:
            return self.auto_gatekeeper.is_ratelimited(member)

        return []

    def is_alertable_join_spam(self, member: discord.Member) -> list[discord.Member]:
        """Check if a member is ratelimited by the join spam checker."""
        if self.auto_gatekeeper is not None:
            return []

        return self._default_join_spam.is_ratelimited(member)

    def remove_member(self, user: discord.abc.User) -> None:
        """Remove a member from the spam checker."""
        self.flagged_users.pop(user.id, None)
