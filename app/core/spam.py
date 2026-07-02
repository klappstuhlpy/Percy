from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.services.spam_penalty import LOOKBACK_WINDOW, compute_spam_penalty
from app.utils import helpers

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.core.context import Context

__all__ = ("SpamControl",)


class SpamControl:
    """A class that implements a cooldown for spamming.

    Attributes
    ------------
    bot: Bot
        The bot instance.
    spam_counter: CooldownMapping
        The cooldown mapping.
    spam_details: dict[int, list[float]]
        The details of the spam.
    """

    if TYPE_CHECKING:
        bot: Bot
        spam_counter: commands.CooldownMapping
        _auto_spam_count: Counter[int]
        spam_details: dict[int, list[float]]

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.spam_counter: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            10, 12.0, commands.BucketType.user
        )
        self._auto_spam_count: Counter[int] = Counter()
        self.spam_details: dict[int, list[float]] = defaultdict(list)

    @property
    def current_spammers(self) -> list[int]:
        """Returns a list of spammers."""
        return list(self._auto_spam_count.keys())

    async def log_spammer(
        self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: bool = False
    ) -> None:
        guild_name = getattr(ctx.guild, "name", "No Guild (DMs)")
        guild_id = getattr(ctx.guild, "id", None)
        fmt = "User %s (ID: %s) in guild %r (ID: %s) is spamming | retry_after: %.2fs | autoblock: %s"
        self.bot.log.warning(fmt, message.author, message.author.id, guild_name, guild_id, retry_after, autoblock)

        if not autoblock:
            return

        embed = discord.Embed(title="Auto-Blocked Member", colour=helpers.Colour.di_sierra())
        embed.add_field(name="Member", value=f"{message.author} (ID: {message.author.id})", inline=False)
        embed.add_field(name="Guild Info", value=f"{guild_name} (ID: {guild_id})", inline=False)
        embed.add_field(name="Channel Info", value=f"{message.channel} (ID: {message.channel.id})", inline=False)
        embed.timestamp = discord.utils.utcnow()
        await self.bot.stats_webhook.send(embed=embed, username="Bot Spam Control")

    def _record_offense(self, user_id: int, timestamp: float) -> None:
        """Logs a spam offense, pruning entries outside the lookback window.

        Unlike ``_auto_spam_count`` (which resets after every applied penalty), this
        history persists across penalties so repeat offenders escalate over time.
        """
        offenses = self.spam_details[user_id]
        offenses.append(timestamp)
        cutoff = timestamp - LOOKBACK_WINDOW
        self.spam_details[user_id] = [t for t in offenses if t >= cutoff]

    def calculate_penalty(self, user: discord.abc.Snowflake) -> int | None:
        """Calculate a blacklist duration from the frequency and recency of spamming.

        Escalates from a day up to a week — and ultimately a permanent block — the more
        often and more recently the user has tripped the spam filter. The actual curve
        lives in :func:`~app.services.spam_penalty.compute_spam_penalty`.

        Returns
        --------
        int | None
            The penalty to apply in seconds, or ``None`` for a permanent block.
        """
        return compute_spam_penalty(self.spam_details[user.id], now=time.time())

    async def apply_penalty(self, user: discord.abc.Snowflake) -> None:
        """Apply penalty to the user."""
        penalty = self.calculate_penalty(user)
        await self.bot.add_to_blacklist(user, duration=penalty)

    async def is_spam(self, ctx: Context, message: discord.Message) -> bool:
        """|coro|

        Checks if the message is spam or not.

        Parameters
        -----------
        ctx: Context
            The invocation context.
        message: Message
            The message to check.

        Returns
        --------
        bool
            Whether the message is spam or not.
        """
        bucket = self.spam_counter.get_bucket(message)
        retry_after = bucket and bucket.update_rate_limit(message.created_at.timestamp())
        author_id = message.author.id

        if retry_after and author_id != self.bot.owner_id:
            self._record_offense(author_id, message.created_at.timestamp())
            self._auto_spam_count[author_id] += 1
            if self._auto_spam_count[author_id] >= 5:
                await self.apply_penalty(message.author)
                del self._auto_spam_count[author_id]
                await self.log_spammer(ctx, message, retry_after, autoblock=True)  # type: ignore[arg-type]
            else:
                await self.log_spammer(ctx, message, retry_after)  # type: ignore[arg-type]
            return True
        else:
            self._auto_spam_count.pop(author_id, None)
        return False
