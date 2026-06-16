from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord.ext import commands  # noqa: TC002  (runtime-resolved command annotation)

from app.cogs.starboard import ui
from app.cogs.starboard.engine import StarboardAction, decide_action, is_too_old
from app.cogs.starboard.models import StarboardConfig
from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, describe, group
from app.core.pagination import LinePaginator
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
            if not self._eligible_to_create(message, config, starboard):
                return
            content = ui.build_starboard_content(config.emoji, star_count, message.channel)
            embed, view = ui.build_starboard_embed(message, star_count=star_count, threshold=config.threshold)
            try:
                posted = await starboard.send(content, embed=embed, view=view)
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
                embed, _ = ui.build_starboard_embed(message, star_count=star_count, threshold=config.threshold)
                try:
                    # Re-send the embed too so its colour warms with the rising star count.
                    await star_message.edit(content=content, embed=embed)
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

    @staticmethod
    def _eligible_to_create(
        message: discord.Message, config: StarboardConfig, starboard: discord.TextChannel | discord.Thread
    ) -> bool:
        """Gate the *first* post of a message: too-old and NSFW-spillover messages are skipped.

        Only enforced on creation — once a message is on the board it keeps receiving
        count/colour updates even if it later ages past the limit.
        """
        if is_too_old(message.created_at, discord.utils.utcnow(), config.max_age_hours):
            return False
        source = message.channel
        source_nsfw = isinstance(source, (discord.TextChannel, discord.Thread, discord.VoiceChannel)) and source.is_nsfw()
        # NSFW spillover: a message from an NSFW channel may only land on a non-NSFW board if allowed.
        return not (source_nsfw and not config.allow_nsfw and not starboard.is_nsfw())

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
        await ctx.send(embed=ui.build_config_embed(config, ctx.guild))

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

    @starboard.command(
        'maxage',
        description='Ignore messages older than a number of hours (0 disables the limit).',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(hours='Maximum message age in hours, or 0 for no limit.')
    async def starboard_maxage(self, ctx: Context, hours: commands.Range[int, 0, 8760]) -> None:
        """Set the maximum age a message can be to reach the starboard."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, max_age_hours=hours)
        if hours == 0:
            await ctx.send_success('Messages of any age can now reach the starboard.')
        else:
            await ctx.send_success(f'Only messages newer than **{hours}h** can reach the starboard.')

    @starboard.command(
        'nsfw',
        description='Allow or disallow NSFW-channel messages on a non-NSFW starboard.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(allowed='Whether messages from NSFW channels may be mirrored.')
    async def starboard_nsfw(self, ctx: Context, allowed: bool) -> None:
        """Toggle whether NSFW-channel messages may reach the starboard."""
        assert ctx.guild is not None
        await self._update_config(ctx.guild.id, allow_nsfw=allowed)
        verb = 'can now' if allowed else 'can no longer'
        await ctx.send_success(f'Messages from NSFW channels {verb} reach the starboard.')

    @starboard.command(
        'config',
        description='Open the interactive starboard settings panel.',
        user_permissions=PermissionTemplate.manager,
    )
    async def starboard_config(self, ctx: Context) -> None:
        """Open the interactive configuration panel."""
        assert ctx.guild is not None
        config = await self.get_config(ctx.guild.id)
        view = ui.StarboardConfigView(self, ctx, config)
        view.message = await ctx.send(embed=view.embed(), view=view)

    @starboard.command(
        'top',
        description='Show the most-starred messages in this server.',
        guild_only=True,
    )
    async def starboard_top(self, ctx: Context) -> None:
        """Show the server's most-starred messages."""
        assert ctx.guild is not None
        entries = await self.bot.db.starboard.top_entries(ctx.guild.id, limit=100)
        if not entries:
            await ctx.send_info('No messages have reached the starboard yet.')
            return

        guild_id = ctx.guild.id
        lines = [
            f"**{record['star_count']}** \N{WHITE MEDIUM STAR} — <@{record['author_id']}> · "
            f"[jump](https://discord.com/channels/{guild_id}/{record['channel_id']}/{record['message_id']})"
            for record in entries
        ]
        embed = discord.Embed(title='\N{GLOWING STAR} Starboard Leaderboard', colour=helpers.Colour.energy_yellow())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        await LinePaginator.start(
            ctx, entries=lines, per_page=10, location='description', numerate=True, embed=embed
        )

    @starboard.command(
        'stats',
        description='Show starboard statistics for the server or a member.',
        guild_only=True,
    )
    @describe(member='The member to show stats for (defaults to server-wide stats).')
    async def starboard_stats(self, ctx: Context, member: discord.Member | None = None) -> None:
        """Show starboard statistics for the server or a specific member."""
        assert ctx.guild is not None
        repo = self.bot.db.starboard

        if member is not None:
            stats = await repo.author_stats(ctx.guild.id, member.id)
            embed = discord.Embed(
                title=f'Starboard stats for {member.display_name}',
                colour=helpers.Colour.energy_yellow(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            if stats is None:
                embed.description = 'This member has no messages on the starboard yet.'
            else:
                embed.add_field(name='Stars received', value=f"{stats['stars']} \N{WHITE MEDIUM STAR}")
                embed.add_field(name='Messages on board', value=str(stats['posts']))
                embed.add_field(name='Best post', value=f"{stats['best']} \N{WHITE MEDIUM STAR}")
            await ctx.send(embed=embed)
            return

        totals = await repo.guild_totals(ctx.guild.id)
        top_authors = await repo.top_authors(ctx.guild.id, limit=5)
        embed = discord.Embed(title='\N{GLOWING STAR} Server Starboard Stats', colour=helpers.Colour.energy_yellow())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.add_field(name='Starred messages', value=str(totals['posts']))
        embed.add_field(name='Total stars', value=f"{totals['stars']} \N{WHITE MEDIUM STAR}")
        embed.add_field(name='Starred authors', value=str(totals['authors']))

        if top_authors:
            medals = ('\N{FIRST PLACE MEDAL}', '\N{SECOND PLACE MEDAL}', '\N{THIRD PLACE MEDAL}')
            leaderboard = '\n'.join(
                f"{medals[i] if i < len(medals) else f'`{i + 1}.`'} <@{record['author_id']}> — "
                f"**{record['stars']}** \N{WHITE MEDIUM STAR} ({record['posts']} posts)"
                for i, record in enumerate(top_authors)
            )
            embed.add_field(name='Top authors', value=leaderboard, inline=False)
        await ctx.send(embed=embed)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Starboard(bot))
