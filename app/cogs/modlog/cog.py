from __future__ import annotations

import textwrap
from contextlib import suppress

import discord

from app.cogs.modlog.models import CaseType, ModerationCase, summarize_case_counts
from app.cogs.modlog.ui import build_case_embed
from app.core import Bot, Cog
from app.core.models import Context, command, describe, group
from app.core.pagination import LinePaginator
from app.utils import get_asset_url, helpers
from config import Emojis


class ModLog(Cog):
    """A queryable moderation case log: warnings plus an audit trail of mod actions."""

    emoji = '\N{CLIPBOARD}'

    # -- case persistence -------------------------------------------------

    async def record_case(
        self,
        guild_id: int,
        action: str,
        target_id: int,
        moderator_id: int | None,
        reason: str | None,
    ) -> ModerationCase:
        """Persists a moderation case and announces it in the modlog channel."""
        record = await self.bot.db.cases.create_case(guild_id, action, target_id, moderator_id, reason)
        case = ModerationCase.from_record(record)
        await self._post_to_modlog(case)
        return case

    async def _get_modlog_channel(self, guild_id: int) -> discord.TextChannel | discord.Thread | None:
        channel_id = await self.bot.db.cases.get_modlog_channel(guild_id)
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    async def _post_to_modlog(self, case: ModerationCase) -> None:
        channel = await self._get_modlog_channel(case.guild_id)
        if channel is None:
            return
        try:
            message = await channel.send(embed=build_case_embed(self.bot, case))
        except discord.HTTPException:
            return
        await self.bot.db.cases.set_log_message(case.id, message.id)

    async def _edit_modlog_message(self, case: ModerationCase) -> None:
        if case.log_message_id is None:
            return
        channel = await self._get_modlog_channel(case.guild_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(case.log_message_id)
            await message.edit(embed=build_case_embed(self.bot, case))
        except discord.HTTPException:
            pass

    async def _delete_modlog_message(self, case: ModerationCase) -> None:
        if case.log_message_id is None:
            return
        channel = await self._get_modlog_channel(case.guild_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(case.log_message_id)
            await message.delete()
        except discord.HTTPException:
            pass

    async def update_case_reason(self, guild_id: int, case_index: int, reason: str) -> ModerationCase | None:
        """Updates a case's reason and syncs its modlog post. Returns ``None`` if the case is missing."""
        record = await self.bot.db.cases.update_reason(guild_id, case_index, reason)
        if record is None:
            return None
        case = ModerationCase.from_record(record)
        await self._edit_modlog_message(case)
        return case

    async def delete_case(self, guild_id: int, case_index: int) -> ModerationCase | None:
        """Deletes a case and removes its modlog post. Returns the deleted case, or ``None`` if missing."""
        record = await self.bot.db.cases.delete_case(guild_id, case_index)
        if record is None:
            return None
        case = ModerationCase.from_record(record)
        await self._delete_modlog_message(case)
        return case

    # -- event hook -------------------------------------------------------

    @Cog.listener()
    async def on_mod_action(
        self, guild_id: int, action: str, target_id: int, moderator_id: int | None, reason: str | None
    ) -> None:
        """Records a case for a moderation action dispatched by another cog.

        Fired via ``bot.dispatch('mod_action', ...)`` so producers (e.g. the moderation
        cog) stay decoupled from the case log.
        """
        await self.record_case(guild_id, action, target_id, moderator_id, reason)

    # -- commands ---------------------------------------------------------

    @command(
        'warn',
        description='Warn a member and record it in their moderation history.',
        guild_only=True,
        hybrid=True,
        user_permissions=['kick_members'],
    )
    @describe(member='The member to warn.', reason='Why they are being warned.')
    async def warn(self, ctx: Context, member: discord.Member, *, reason: str | None = None) -> None:
        """Warn a member and log a case."""
        assert ctx.guild is not None
        if member.bot:
            await ctx.send_error('You cannot warn a bot.')
            return
        if member.id == ctx.author.id:
            await ctx.send_error('You cannot warn yourself.')
            return

        case = await self.record_case(ctx.guild.id, CaseType.WARN.value, member.id, ctx.author.id, reason)

        with suppress(discord.HTTPException):
            note = f': {reason}' if reason else '.'
            await member.send(f'{Emojis.warning} You were warned in **{ctx.guild.name}**{note}')

        await ctx.send_success(f'Warned {member.mention} • **Case #{case.index}**.')

    @command(
        'cases',
        aliases=['warnings', 'history', 'modlogs'],
        description="Show a member's moderation history.",
        guild_only=True,
        hybrid=True,
        user_permissions=['manage_messages'],
    )
    @describe(user='The member to look up.')
    async def cases(self, ctx: Context, user: discord.User) -> None:
        """List a member's moderation cases."""
        assert ctx.guild is not None
        records = await self.bot.db.cases.get_user_cases(ctx.guild.id, user.id)
        if not records:
            await ctx.send_info(f'**{user}** has a clean record — no cases.')
            return

        cases = [ModerationCase.from_record(record) for record in records]
        summary = summarize_case_counts([case.action for case in cases])

        entries = []
        for case in cases:
            case_type = case.type
            label = case_type.label if case_type else case.action.title()
            emoji = case_type.emoji if case_type else '\N{MEMO}'
            reason = textwrap.shorten(case.reason or 'No reason provided.', width=100)
            entries.append(
                f'{emoji} **#{case.index}** • {label} • {discord.utils.format_dt(case.created_at, "R")}\n{reason}'
            )

        embed = discord.Embed(
            title=f'Moderation history for {user}',
            description=f'**Summary:** {summary}\n\n',
            colour=helpers.Colour.white(),
        )
        embed.set_thumbnail(url=get_asset_url(user))
        await LinePaginator.start(ctx, entries=entries, embed=embed, location='description')

    @command(
        'case',
        description='Show a single moderation case.',
        guild_only=True,
        hybrid=True,
        user_permissions=['manage_messages'],
    )
    @describe(index='The case number to show.')
    async def case(self, ctx: Context, index: int) -> None:
        """Show one moderation case by number."""
        assert ctx.guild is not None
        record = await self.bot.db.cases.get_case(ctx.guild.id, index)
        if record is None:
            await ctx.send_error(f'Case #{index} does not exist.')
            return
        await ctx.send(embed=build_case_embed(self.bot, ModerationCase.from_record(record)))

    @command(
        'reason',
        description='Update the reason of an existing case.',
        guild_only=True,
        hybrid=True,
        user_permissions=['manage_messages'],
    )
    @describe(index='The case number to edit.', reason='The new reason.')
    async def reason(self, ctx: Context, index: int, *, reason: str) -> None:
        """Edit a case's reason (and its modlog post)."""
        assert ctx.guild is not None
        case = await self.update_case_reason(ctx.guild.id, index, reason)
        if case is None:
            await ctx.send_error(f'Case #{index} does not exist.')
            return
        await ctx.send_success(f'Updated the reason for **Case #{index}**.')

    @command(
        'delcase',
        aliases=['deletecase'],
        description='Delete a moderation case.',
        guild_only=True,
        hybrid=True,
        user_permissions=['ban_members'],
    )
    @describe(index='The case number to delete.')
    async def delcase(self, ctx: Context, index: int) -> None:
        """Delete a moderation case (and its modlog post)."""
        assert ctx.guild is not None
        case = await self.delete_case(ctx.guild.id, index)
        if case is None:
            await ctx.send_error(f'Case #{index} does not exist.')
            return
        await ctx.send_success(f'Deleted **Case #{index}**.')

    @group(
        'modlog',
        fallback='show',
        description='Show or configure the moderation log channel.',
        guild_only=True,
        hybrid=True,
    )
    async def modlog(self, ctx: Context) -> None:
        """Show the current modlog channel."""
        assert ctx.guild is not None
        channel = await self._get_modlog_channel(ctx.guild.id)
        if channel is None:
            await ctx.send_info('No modlog channel is set. Use `modlog set` to choose one.')
        else:
            await ctx.send_info(f'Moderation cases are logged to {channel.mention}.')

    @modlog.command(
        'set',
        description='Set the channel where moderation cases are logged.',
        user_permissions=['manage_guild'],
    )
    @describe(channel='The channel to log cases in.')
    async def modlog_set(self, ctx: Context, channel: discord.TextChannel) -> None:
        """Set the modlog channel."""
        assert ctx.guild is not None
        await self.bot.db.cases.set_modlog_channel(ctx.guild.id, channel.id)
        await ctx.send_success(f'Moderation cases will be logged to {channel.mention}.')

    @modlog.command(
        'disable',
        description='Stop logging moderation cases to a channel.',
        user_permissions=['manage_guild'],
    )
    async def modlog_disable(self, ctx: Context) -> None:
        """Disable modlog channel posting."""
        assert ctx.guild is not None
        await self.bot.db.cases.set_modlog_channel(ctx.guild.id, None)
        await ctx.send_success('Moderation cases will no longer be posted to a channel (they are still recorded).')


async def setup(bot: Bot) -> None:
    await bot.add_cog(ModLog(bot))
