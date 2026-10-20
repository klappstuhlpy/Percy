from __future__ import annotations

import datetime
import logging
import time
from operator import attrgetter
from typing import TYPE_CHECKING

import discord

from app.core.pagination import TextSource
from app.utils import ListedRateLimit, RateLimit, cache
from app.utils.checks import hierarchy_error
from config import Emojis

from .models import FlaggedMember, SpamCheckerResult, SpammerSequence

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence

    from app.database.base import GuildConfig, Sentinel

log = logging.getLogger(__name__)


#: Identical-content tracking ignores messages shorter than this (after stripping): attachment- and
#: sticker-only posts have no text at all, and "gg"/"F"/"lol" waves are conversation, not spam.
MIN_CONTENT_LENGTH = 5

#: How long a member stays "already handled" after an automatic ban (or a failed one), so one raid
#: gives one ban and one alert per member however many limiters trip on them.
RECENTLY_ACTIONED_SECONDS = 300.0

#: The lowest mention-spam threshold: the command and the dashboard API refuse less, and a
#: lower value stored before that check is raised to it (a reply ping must not ban anyone).
MIN_MENTION_COUNT = 3

#: Mentions a member sends within this many seconds add up towards the threshold.
MENTION_WINDOW_SECONDS = 15.0


def mention_threshold(config: GuildConfig) -> int | None:
    """The guild's mention-spam threshold, or ``None`` when it is off (unset, 0 or negative)."""
    count = config.mention_count
    if not count or count <= 0:
        return None
    return max(count, MIN_MENTION_COUNT)


class SpamChecker:
    """Per-guild raid detection state.

    Message limiters (checked in :meth:`is_spamming`, in this order):

    1) Hit-and-run: 5 messages per 15 s in one channel from members flagged as raiders
       (see :meth:`flag_member`); every flagged member who posted in that burst is returned.
    2) New users: 30 messages per 35 s in one channel from accounts younger than 90 days
       that joined in the last 7 days. This only says a raid is happening in the channel;
       just the new members who sent 3 or more of those messages are returned.
    3) Per user: 10 messages per 12 s from one member.
    4) Identical content: 5 copies of the same text (at least :data:`MIN_CONTENT_LENGTH`
       characters) per 15 s in one channel. Again a raid signal; just the members who
       sent 2 or more of the copies are returned.

    Mentions: more than :func:`mention_threshold` mentions in one message, or over
    :data:`MENTION_WINDOW_SECONDS` from one member (:meth:`mention_spam`). Joins:
    ``sentinel.rate`` joins (auto-Sentinel, flags the cohort) or 10 joins per 5 s (alert
    only, used when no auto-trigger is configured) (:meth:`check_sentinel`,
    :meth:`is_alertable_join_spam`).

    Every limiter fires once per trip and then starts a fresh window. :meth:`claim` filters
    out members already handled in the last :data:`RECENTLY_ACTIONED_SECONDS`.
    """

    def __init__(self) -> None:
        self.by_content = RateLimit(
            5, 15.0, key=lambda msg: (msg.channel.id, msg.content), tagger=attrgetter("author"), min_hits=2
        )
        self.by_user = RateLimit(10, 12.0, key=lambda msg: msg.author.id)
        self.new_user = RateLimit(30, 35.0, key=lambda msg: msg.channel.id, tagger=attrgetter("author"), min_hits=3)

        #: author id -> recent ``(timestamp, mentions)`` pairs, for :meth:`mention_spam`.
        self._mentions: MutableMapping[int, list[tuple[float, int]]] = cache.ExpiringCache(
            seconds=MENTION_WINDOW_SECONDS
        )

        self._join_rate: tuple[int, int] | None = None
        self.auto_sentinel: ListedRateLimit | None = None
        # The alert-only join check, used when no Sentinel auto-trigger is configured.
        self._default_join_spam = ListedRateLimit(10, 5, key=attrgetter("joined_at"))

        #: Members the Sentinel auto-trigger held. Being flagged has consequences (the
        #: hit-and-run and flagged-mention bans, no commands), so only that path flags.
        self.flagged_users: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        #: Members named in an alert-only join-spam alert: offered to its Massban button, nothing more.
        self.join_spammers: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        self.hit_and_run = RateLimit(5, 15, key=lambda msg: msg.channel.id, tagger=lambda msg: msg.author)
        self.recently_actioned: MutableMapping[int, bool] = cache.ExpiringCache(seconds=RECENTLY_ACTIONED_SECONDS)
        self._ban_permission_alerted_at: float | None = None

    def claim[U: discord.abc.Snowflake](self, users: Sequence[U], /) -> list[U]:
        """Return the users not automatically actioned recently, and mark them as actioned.

        Call this right before banning, so a member who keeps tripping limiters during one
        raid is banned (and reported) once.
        """
        fresh = [user for user in users if user.id not in self.recently_actioned]
        for user in fresh:
            self.recently_actioned[user.id] = True
        return fresh

    async def alert_missing_ban_permission(self, config: GuildConfig, feature: str) -> None:
        """Tell the moderators Percy cannot ban, at most once per :data:`RECENTLY_ACTIONED_SECONDS`."""
        now = time.monotonic()
        if self._ban_permission_alerted_at is not None and now - self._ban_permission_alerted_at < RECENTLY_ACTIONED_SECONDS:
            return
        self._ban_permission_alerted_at = now
        await config.alert_missing_permission(feature)

    def get_flagged_member(self, user_id: int, /) -> FlaggedMember | None:
        """Get a flagged member."""
        return self.flagged_users.get(user_id)

    def is_flagged(self, user_id: int, /) -> bool:
        """Check if a user is flagged."""
        return user_id in self.flagged_users

    def flag_member(self, member: discord.Member, /) -> None:
        """Flag a member."""
        self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())

    def raid_suspects(self) -> list[FlaggedMember]:
        """Everyone a raid alert's Massban button offers: auto-Sentinel holds and join-spam alert cohorts."""
        suspects = dict(self.join_spammers.items())
        suspects.update(self.flagged_users.items())
        return list(suspects.values())

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
                (flagged.messages <= 10 and message.raw_mentions)
                or "@everyone" in message.content
                or "@here" in message.content
            ):
                return SpamCheckerResult.flagged_mention()

        # The shared limiters only signal a raid; they ban the members who sent the burst, never the
        # bystander whose message happened to cross the line.
        if self.is_new(message.author):  # type: ignore[arg-type]
            spammers = self.new_user.is_ratelimited(message)
            if spammers:
                return SpammerSequence(spammers)  # type: ignore[arg-type]

        if self.by_user.is_ratelimited(message):
            return SpammerSequence([message.author])

        if len(message.content.strip()) >= MIN_CONTENT_LENGTH:
            spammers = self.by_content.is_ratelimited(message)
            if spammers:
                return SpammerSequence(spammers)  # type: ignore[arg-type]

        return None

    def mention_spam(self, message: discord.Message, threshold: int) -> tuple[int, bool] | None:
        """Whether ``message`` takes its author past ``threshold`` mentions.

        One message with more than ``threshold`` mentions is checked first; otherwise the
        member's mentions over the last :data:`MENTION_WINDOW_SECONDS` are summed. Bots and
        the author themself never count.

        Returns ``(mentions, over_multiple_messages)`` on a trip, else ``None``. A trip
        clears the member's history, so it fires once.
        """
        author_id = message.author.id
        count = sum(not m.bot and m.id != author_id for m in message.mentions)
        if count > threshold:
            self._mentions.pop(author_id, None)
            return count, False
        if not count:
            return None

        now = message.created_at.timestamp()
        recent = [(t, n) for t, n in self._mentions.get(author_id, []) if now - t < MENTION_WINDOW_SECONDS]
        recent.append((now, count))
        total = sum(n for _, n in recent)
        if total > threshold:
            self._mentions.pop(author_id, None)
            return total, True
        self._mentions[author_id] = recent
        return None

    def check_sentinel(self, member: discord.Member, sentinel: Sentinel) -> list[discord.Member]:
        """Check if a member is ratelimited by the sentinel.

        This will return a list of members that are ratelimited.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        sentinel: :class:`Sentinel
            The sentinel to check against.
        """
        if sentinel.started_at is not None:
            return []

        rate = sentinel.rate
        if rate is None:
            self._join_rate = None
            self.auto_sentinel = None
            return []

        if rate != self._join_rate:
            # Might be worth considering swapping over the tat/member list? Probably complicated though
            self.auto_sentinel = ListedRateLimit(int(rate[0]), int(rate[1]), key=attrgetter("joined_at"))
            self._join_rate = rate  # type: ignore[arg-type]

        if self.auto_sentinel is not None:
            return self.auto_sentinel.is_ratelimited(member)

        return []

    def is_alertable_join_spam(self, member: discord.Member) -> list[discord.Member]:
        """The members to alert about when ``member`` completes a burst of joins, else ``[]``.

        They are kept for the alert's Massban button (:meth:`raid_suspects`) but not flagged:
        the alert asks a moderator to review, it does not decide they are raiders.
        """
        spammers = self._default_join_spam.is_ratelimited(member)
        for spammer in spammers:
            self.join_spammers[spammer.id] = FlaggedMember(spammer, spammer.joined_at or discord.utils.utcnow())
        return spammers

    def remove_member(self, user: discord.abc.User) -> None:
        """Remove a member from the spam checker."""
        self.flagged_users.pop(user.id, None)
        self.join_spammers.pop(user.id, None)


async def check_raid(
    checker: SpamChecker,
    config: GuildConfig,
    guild: discord.Guild,
    member: discord.Member,
    message: discord.Message,
) -> bool:
    """|coro|

    Check if a member is raiding the server and ban them if they are.

    Returns whether ``member`` (the message's author) was banned, so the caller can stop
    handling the message.

    Parameters
    ----------
    checker: :class:`SpamChecker`
        The guild's spam checker holding the live detection state.
    config: :class:`GuildConfig`
        The guild configuration to check.
    guild: :class:`discord.Guild`
        The guild to check.
    member: :class:`discord.Member`
        The member to check.
    message: :class:`discord.Message`
        The message to check.
    """
    if not config.flags.raid:
        return False

    result = checker.is_spamming(message)
    if result is None:
        return False

    members = checker.claim(result.members if isinstance(result, SpammerSequence) else [member])
    if not members:
        return False

    me = guild.me
    if not me.guild_permissions.ban_members:
        await checker.alert_missing_ban_permission(config, "ban members for raid protection")
        return False

    banned: list[discord.abc.Snowflake] = []
    failed: list[discord.abc.Snowflake] = []
    for user in members:
        if outranks(user, me):
            failed.append(user)
            continue
        try:
            await guild.ban(user, reason=result.reason)
        except discord.HTTPException:
            failed.append(user)
            log.info("[Moderation] Failed to ban %s (ID: %s) from server %s.", user, user.id, guild)
        else:
            banned.append(user)
            config.bot.dispatch("mod_action", guild.id, "ban", user.id, me.id, result.reason)
            log.info("[Moderation] Banned %s (ID: %s) from server %s.", user, user.id, guild)

    # Tell the moderators what happened. Never quote the message itself: the alert
    # names who was banned, where, and why — the spam stays where it was posted.
    if banned:
        for page in raid_ban_alert(banned, result.reason, message.channel.id):
            await config.send_alert(page)
    if failed:
        for page in raid_ban_failed_alert(failed, result.reason, message.channel.id):
            await config.send_alert(page, force=True)
    return any(user.id == member.id for user in banned)


def outranks(user: object, me: discord.Member) -> bool:
    """Whether the shared hierarchy rule keeps Percy (``me``) from banning ``user``."""
    return hierarchy_error(None, user, me, action="ban") is not None  # type: ignore[arg-type]


def _alert_pages(header: str, users: Sequence[discord.abc.Snowflake], footer: str) -> list[str]:
    # send_alert prefixes an emoji, so leave headroom under Discord's 2000-character limit.
    source = TextSource(prefix=None, suffix=None, max_size=1900)
    source.add_lines([header, *(f"- **{user}** (ID: `{user.id}`)" for user in users), footer])
    return source.pages


def raid_ban_alert(users: Sequence[discord.abc.Snowflake], reason: str, channel_id: int) -> list[str]:
    """The mod-alert pages for an automatic raid ban (no message content, by design)."""
    return _alert_pages(f"Raid protection banned these members in <#{channel_id}>:", users, f"Reason: *{reason}*.")


def raid_ban_failed_alert(users: Sequence[discord.abc.Snowflake], reason: str, channel_id: int) -> list[str]:
    """The mod-alert pages for raiders Percy could not ban."""
    return _alert_pages(
        f"{Emojis.warning} Raid protection could **not** ban these members in <#{channel_id}> ({reason}):",
        users,
        "Check that I have the **Ban Members** permission and that my highest role is above theirs.",
    )


async def mention_spam_ban(
    checker: SpamChecker,
    config: GuildConfig,
    member: discord.Member,
    mention_count: int,
    *,
    multiple: bool = False,
) -> None:
    """|coro|

    Ban a member for mention spamming and tell the moderators how it went.

    A member already handled in the last :data:`RECENTLY_ACTIONED_SECONDS` is skipped, so an
    unbannable member who keeps pinging produces one alert, not one per message.

    Parameters
    ----------
    checker: :class:`SpamChecker`
        The guild's spam checker (dedupes repeat actions on one member).
    config: :class:`GuildConfig`
        The guild configuration the alert goes through.
    member: :class:`discord.Member`
        The member to ban.
    mention_count: :class:`int`
        The number of mentions the member has made.
    multiple: :class:`bool`
        Whether the member has spammed over multiple messages.
    """
    if not checker.claim([member]):
        return

    if multiple:
        reason = f"Spamming mentions over multiple messages ({mention_count} mentions)"
    else:
        reason = f"Spamming mentions ({mention_count} mentions)"

    me = member.guild.me
    if not me.guild_permissions.ban_members:
        await checker.alert_missing_ban_permission(config, "ban members for mention-spam protection")
        return

    failure = (
        f"{Emojis.warning} Could not ban **{member}** (ID: `{member.id}`) for spamming `{mention_count}` mentions. "
        "Check that my highest role is above theirs."
    )
    if outranks(member, me):
        await config.send_alert(failure, force=True)
        return

    try:
        await member.ban(reason=reason)
    except discord.HTTPException:
        log.info("[Mention Spam] Failed to ban member %s (ID: %s) in guild ID %s", member, member.id, member.guild.id)
        await config.send_alert(failure, force=True)
    else:
        config.bot.dispatch("mod_action", member.guild.id, "ban", member.id, me.id, reason)
        log.info("[Mention Spam] Member %s (ID: %s) has been banned from guild ID %s", member, member.id, member.guild.id)
        await config.send_alert(
            f"{Emojis.info} Banned **{member}** (ID: `{member.id}`) for spamming `{mention_count}` mentions."
        )
