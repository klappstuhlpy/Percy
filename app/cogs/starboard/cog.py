from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord.ext import commands  # noqa: TC002  (runtime-resolved command annotation)

from app.cogs.starboard import ui
from app.cogs.starboard.engine import StarboardAction, decide_action
from app.cogs.starboard.models import DEFAULT_EMOJI, DEFAULT_THRESHOLD, StarboardConfig
from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, describe, group
from app.utils import helpers

if TYPE_CHECKING:
    import asyncpg


class Starboard(Cog):
    """Repost messages that gather enough star reactions into a dedicated channel."""

    emoji = '\N{WHITE MEDIUM STAR}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        # Per-guild config is read on every star reaction, so cache it in memory and
        # invalidate on writes rather than hitting the database each time.
        self._config_cache: dict[int, StarboardConfig | None] = {}
        # Serialize processing per message so concurrent reactions can't double-post.
        self._locks: dict[int, asyncio.Lock] = {}

    # -- config access ----------------------------------------------------

    async def get_config(self, guild_id: int) -> StarboardConfig | None:
        """Returns a guild's cached starboard config, loading it on first use."""
        if guild_id in self._config_cache:
            return self._config_cache[guild_id]

        record = await self.bot.db.starboard.get_config(guild_id)
        config = StarboardConfig.from_record(record) if record else None
        self._config_cache[guild_id] = config
        return config

    async def _update_config(self, guild_id: int, **columns: object) -> StarboardConfig:
        record = await self.bot.db.starboard.upsert_config(guild_id, **columns)
        config = StarboardConfig.from_record(record)
        self._config_cache[guild_id] = config
        return config

    # -- star processing --------------------------------------------------

    def _lock_for(self, message_id: int) -> asyncio.Lock:
        return self._locks.setdefault(message_id, asyncio.Lock())

    async def _count_stars(self, message: discord.Message, config: StarboardConfig) -> int:
        """Counts qualifying star reactions, excluding the author's own when disallowed."""
        reaction = discord.utils.find(lambda r: str(r.emoji) == config.emoji, message.reactions)
        if reaction is None:
            return 0

        count = reaction.count
        if not config.self_star:
            async for user in reaction.users():
                if user.id == message.author.id:
                    count -= 1
                    break
        return max(count, 0)

    async def _process(
        self, guild_id: int, channel_id: int, message_id: int, emoji: discord.PartialEmoji | None
    ) -> None:
        """Re-evaluate a message's starboard state after a reaction change."""
        config = await self.get_config(guild_id)
        if config is None or not config.is_active:
            return
        if emoji is not None and str(emoji) != config.emoji:
            return
        if channel_id == config.channel_id or channel_id in config.ignored_channel_ids:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        source = guild.get_channel_or_thread(channel_id)
        if source is None:
            try:
                source = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                return
        if not isinstance(source, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return

        async with self._lock_for(message_id):
            try:
                message = await source.fetch_message(message_id)
            except discord.HTTPException:
                return
            if message.author.bot:
                return

            star_count = await self._count_stars(message, config)
            entry = await self.bot.db.starboard.get_entry(message_id)
            action = decide_action(
                star_count=star_count, threshold=config.threshold, has_entry=entry is not None
            )
            await self._apply(action, config, message, entry, star_count)

    async def _apply(
        self,
        action: StarboardAction,
        config: StarboardConfig,
        message: discord.Message,
        entry: asyncpg.Record | None,
        star_count: int,
    ) -> None:
        if action is StarboardAction.IGNORE:
            return

        assert config.channel_id is not None  # guaranteed by config.is_active
        starboard = self.bot.get_channel(config.channel_id)
        if not isinstance(starboard, (discord.TextChannel, discord.Thread)):
            return

        if action is StarboardAction.CREATE:
            content = ui.build_starboard_content(config.emoji, star_count, message.channel)
            embed = ui.build_starboard_embed(message)
            try:
                posted = await starboard.send(content, embed=embed)
            except discord.HTTPException:
                return
            await self.bot.db.starboard.create_entry(
                message.id, config.guild_id, message.channel.id, message.author.id, posted.id, star_count
            )
            return

        assert entry is not None
        star_message = await self._fetch_starboard_message(starboard, entry['starboard_message_id'])

        if action is StarboardAction.UPDATE:
            await self.bot.db.starboard.update_star_count(message.id, star_count)
            if star_message is not None:
                content = ui.build_starboard_content(config.emoji, star_count, message.channel)
                try:
                    await star_message.edit(content=content)
                except discord.HTTPException:
                    pass
        elif action is StarboardAction.DELETE:
            await self.bot.db.starboard.delete_entry(message.id)
            if star_message is not None:
                try:
                    await star_message.delete()
                except discord.HTTPException:
                    pass

    @staticmethod
    async def _fetch_starboard_message(
        starboard: discord.TextChannel | discord.Thread, message_id: int | None
    ) -> discord.Message | None:
        if message_id is None:
            return None
        try:
            return await starboard.fetch_message(message_id)
        except discord.HTTPException:
            return None

    # -- listeners --------------------------------------------------------

    @Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is not None:
            await self._process(payload.guild_id, payload.channel_id, payload.message_id, payload.emoji)

    @Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is not None:
            await self._process(payload.guild_id, payload.channel_id, payload.message_id, payload.emoji)

    @Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        if payload.guild_id is not None:
            await self._process(payload.guild_id, payload.channel_id, payload.message_id, None)

    @Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        if payload.guild_id is not None:
            await self._process(payload.guild_id, payload.channel_id, payload.message_id, payload.emoji)

    @Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """Tidy up entries when either the source message or its starboard post is deleted."""
        if payload.guild_id is None:
            return

        entry = await self.bot.db.starboard.get_entry(payload.message_id)
        if entry is not None:
            await self.bot.db.starboard.delete_entry(payload.message_id)
            config = await self.get_config(payload.guild_id)
            if config is not None and config.channel_id is not None:
                starboard = self.bot.get_channel(config.channel_id)
                if isinstance(starboard, (discord.TextChannel, discord.Thread)):
                    star_message = await self._fetch_starboard_message(starboard, entry['starboard_message_id'])
                    if star_message is not None:
                        try:
                            await star_message.delete()
                        except discord.HTTPException:
                            pass
            return

        # The deleted message might itself be a starboard post — drop the orphaned entry.
        orphan = await self.bot.db.starboard.get_entry_by_starboard_message(payload.message_id)
        if orphan is not None:
            await self.bot.db.starboard.delete_entry(orphan['message_id'])

    # -- configuration commands -------------------------------------------

    @group(
        'starboard',
        fallback='show',
        description='Show or configure the server starboard.',
        guild_only=True,
        hybrid=True,
    )
    async def starboard(self, ctx: Context) -> None:
        """Show the current starboard configuration."""
        assert ctx.guild is not None
        config = await self.get_config(ctx.guild.id)

        embed = discord.Embed(title='Starboard Configuration', colour=helpers.Colour.energy_yellow())
        if config is None:
            embed.description = 'The starboard is **not configured** yet. Set a channel with `starboard channel`.'
            embed.add_field(name='Threshold', value=str(DEFAULT_THRESHOLD))
            embed.add_field(name='Emoji', value=DEFAULT_EMOJI)
        else:
            channel = f'<#{config.channel_id}>' if config.channel_id else '*not set*'
            state = 'Enabled' if config.enabled else 'Disabled'
            ignored = ', '.join(f'<#{cid}>' for cid in config.ignored_channel_ids) or '*none*'
            embed.add_field(name='Status', value=state)
            embed.add_field(name='Channel', value=channel)
            embed.add_field(name='Threshold', value=str(config.threshold))
            embed.add_field(name='Emoji', value=config.emoji)
            embed.add_field(name='Allow self-star', value='Yes' if config.self_star else 'No')
            embed.add_field(name='Ignored channels', value=ignored, inline=False)
        await ctx.send(embed=embed)

    @starboard.command(
        'channel',
        description='Set the channel where starred messages are posted.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(channel='The channel to post starred messages in.')
    async def starboard_channel(self, ctx: Context, channel: discord.TextChannel) -> None:
        """Set the starboard destination channel."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, channel_id=channel.id)
        await ctx.send_success(f'Starboard channel set to {channel.mention}.')

    @starboard.command(
        'threshold',
        description='Set how many stars a message needs to be posted.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(amount='The number of stars required (1-100).')
    async def starboard_threshold(self, ctx: Context, amount: commands.Range[int, 1, 100]) -> None:
        """Set the star threshold."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, threshold=amount)
        await ctx.send_success(f'Messages now need **{amount}** stars to reach the starboard.')

    @starboard.command(
        'emoji',
        description='Set the reaction emoji that counts as a star.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(emoji='The emoji to use (default ⭐).')
    async def starboard_emoji(self, ctx: Context, emoji: str) -> None:
        """Set the star emoji."""
        assert ctx.guild is not None
        emoji = emoji.strip()
        if not emoji or len(emoji) > 64:
            await ctx.send_error('That does not look like a valid emoji.')
            return
        await self._update_config(ctx.guild.id, emoji=emoji)
        await ctx.send_success(f'Star emoji set to {emoji}.')

    @starboard.command(
        'selfstar',
        description='Allow or disallow members starring their own messages.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(allowed='Whether authors may star their own messages.')
    async def starboard_selfstar(self, ctx: Context, allowed: bool) -> None:
        """Toggle self-starring."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, self_star=allowed)
        verb = 'now' if allowed else 'no longer'
        await ctx.send_success(f'Members can {verb} star their own messages.')

    @starboard.command(
        'toggle',
        description='Enable or disable the starboard.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(enabled='Whether the starboard should be active.')
    async def starboard_toggle(self, ctx: Context, enabled: bool) -> None:
        """Enable or disable the starboard."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, enabled=enabled)
        await ctx.send_success(f"Starboard {'enabled' if enabled else 'disabled'}.")

    @starboard.command(
        'ignore',
        description='Stop a channel from contributing to the starboard.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(channel='The channel to ignore.')
    async def starboard_ignore(self, ctx: Context, channel: discord.TextChannel) -> None:
        """Ignore a channel."""
        assert ctx.guild is not None
        config = await self.get_config(ctx.guild.id)
        ignored = set(config.ignored_channel_ids) if config else set()
        if channel.id in ignored:
            await ctx.send_error(f'{channel.mention} is already ignored.')
            return
        ignored.add(channel.id)
        await self._update_config(ctx.guild.id, ignored_channel_ids=list(ignored))
        await ctx.send_success(f'{channel.mention} will no longer contribute to the starboard.')

    @starboard.command(
        'unignore',
        description='Let a previously ignored channel contribute again.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(channel='The channel to stop ignoring.')
    async def starboard_unignore(self, ctx: Context, channel: discord.TextChannel) -> None:
        """Stop ignoring a channel."""
        assert ctx.guild is not None
        config = await self.get_config(ctx.guild.id)
        ignored = set(config.ignored_channel_ids) if config else set()
        if channel.id not in ignored:
            await ctx.send_error(f'{channel.mention} is not currently ignored.')
            return
        ignored.discard(channel.id)
        await self._update_config(ctx.guild.id, ignored_channel_ids=list(ignored))
        await ctx.send_success(f'{channel.mention} can contribute to the starboard again.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(Starboard(bot))
