from __future__ import annotations

import datetime
from contextlib import suppress

import discord

from app.cogs.modlog.models import CaseType, ModerationCase
from app.cogs.modlog.ui import CaseHistoryView, build_case_view, build_warn_result_view, send_warn_dm
from app.cogs.modlog.warns import (
    MAX_EXPIRY_DAYS,
    WarnThreshold,
    active_warn_count,
    select_threshold,
    threshold_error,
    warn_expiry,
)
from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, command, describe, group
from app.utils.checks import hierarchy_error
from app.utils.timetools import ShortTime, humanize_duration

#: How many cases ``cases`` loads. Enough for any real history while keeping the summary honest.
HISTORY_LIMIT = 500


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
        expires_at: datetime.datetime | None = None,
    ) -> ModerationCase:
        """Persists a moderation case and announces it in the modlog channel.

        ``expires_at`` is when a temporary action ends; for a warn it comes from the guild's
        warn-expiry setting.
        """
        if action == CaseType.WARN.value and expires_at is None:
            days = await self.bot.db.cases.get_warn_expiry_days(guild_id)
            expires_at = warn_expiry(datetime.datetime.now(datetime.UTC), days)
        record = await self.bot.db.cases.create_case(guild_id, action, target_id, moderator_id, reason, expires_at)
        case = ModerationCase.from_record(record)
        await self._post_to_modlog(case)
        return case

    async def _get_modlog_channel(self, guild_id: int) -> discord.TextChannel | discord.Thread | None:
        channel_id = await self.bot.db.cases.get_modlog_channel(guild_id)
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, (discord.TextChannel, discord.Thread)) else None

    async def _resolve_user(self, user_id: int | None) -> discord.User | None:
        if user_id is None:
            return None
        if (user := self.bot.get_user(user_id)) is not None:
            return user
        with suppress(discord.HTTPException):
            return await self.bot.fetch_user(user_id)
        return None

    async def _case_expiry(self, case: ModerationCase) -> datetime.datetime | None:
        """Expiry of a temporary action: the stored one, else a best-effort lookup (older cases)."""
        if case.expires_at is not None:
            return case.expires_at
        if case.action in ('tempban', 'tempmute'):
            timer = await self.bot.timers.fetch_member_timer(case.action, case.guild_id, case.target_id)
            return timer.expires if timer is not None else None
        if case.action == 'timeout':
            guild = self.bot.get_guild(case.guild_id)
            member = guild.get_member(case.target_id) if guild else None
            until = member.timed_out_until if member else None
            return until if until and until > discord.utils.utcnow() else None
        return None

    async def build_case_view(self, case: ModerationCase) -> discord.ui.LayoutView:
        """The CV2 card for ``case`` with its users and expiry resolved."""
        return build_case_view(
            case,
            target=await self._resolve_user(case.target_id),
            moderator=await self._resolve_user(case.moderator_id),
            expires=await self._case_expiry(case),
        )

    async def _post_to_modlog(self, case: ModerationCase) -> None:
        channel = await self._get_modlog_channel(case.guild_id)
        if channel is None:
            return
        try:
            message = await channel.send(
                view=await self.build_case_view(case), allowed_mentions=discord.AllowedMentions.none()
            )
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
            # Posts from before the CV2 cards are embeds; a CV2 edit must clear content/embeds.
            await message.edit(
                content=None,
                embeds=[],
                view=await self.build_case_view(case),
                allowed_mentions=discord.AllowedMentions.none(),
            )
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
        self,
        guild_id: int,
        action: str,
        target_id: int,
        moderator_id: int | None,
        reason: str | None,
        expires_at: datetime.datetime | None = None,
    ) -> None:
        """Records a case for a moderation action dispatched by another cog.

        Fired via ``bot.dispatch('mod_action', ...)`` so producers (e.g. the moderation
        cog) stay decoupled from the case log.
        """
        case = await self.record_case(guild_id, action, target_id, moderator_id, reason, expires_at)
        if action == CaseType.WARN.value:
            # Dashboard/AI-alert warns arrive here; the ``warn`` command escalates itself.
            await self.escalate(case)

    # -- warn policy ------------------------------------------------------

    async def active_warns(self, guild_id: int, target_id: int, *, up_to_index: int | None = None) -> int:
        """Warns that are neither pardoned nor expired."""
        rows = await self.bot.db.cases.get_warn_rows(guild_id, target_id, up_to_index=up_to_index)
        return active_warn_count(row['expires_at'] for row in rows)

    async def escalate(self, case: ModerationCase) -> str | None:
        """Applies the guild's threshold for the target's new active-warn count, if any.

        Goes through ``apply_member_action`` as Percy, so permissions and hierarchy are exactly
        those of a manual action. Returns a one-line note for the moderator, or ``None``.
        """
        from app.cogs.moderation.member_actions import MemberActionError, apply_member_action

        guild = self.bot.get_guild(case.guild_id)
        if guild is None:
            return None
        rows = await self.bot.db.cases.get_warn_thresholds(case.guild_id)
        count = await self.active_warns(case.guild_id, case.target_id, up_to_index=case.index)
        rule = select_threshold(
            (WarnThreshold(r['warn_count'], r['action'], r['duration_seconds']) for r in rows), count
        )
        if rule is None:
            return None
        try:
            await apply_member_action(
                self.bot, guild, guild.me, case.target_id, action=rule.action,
                reason=f'Automatic: {count} active warnings', duration=rule.duration,
            )
        except MemberActionError as e:
            return f'Threshold at {count} warnings ({rule.action}) could not be applied: {e.detail}'
        return f'Threshold reached at {count} warnings: **{rule.action}** applied.'

    # -- commands ---------------------------------------------------------

    @command(
        'warn',
        description='Warn a member and record it in their moderation history.',
        extras={'example': 'warn @member spamming in #general'},
        guild_only=True,
        hybrid=True,
        user_permissions=PermissionTemplate.kick,
    )
    @describe(member='The member to warn.', reason='Why they are being warned.')
    async def warn(self, ctx: Context, member: discord.Member, *, reason: str | None = None) -> None:
        """Warn a member and log a case."""
        assert ctx.guild is not None
        if member.bot:
            await ctx.send_error('You cannot warn a bot.')
            return
        # A warning never touches the member's roles, so only the moderator has to outrank them.
        if error := hierarchy_error(ctx.author, member, ctx.guild.me, action='warn', check_bot=False):  # type: ignore[arg-type]
            await ctx.send_error(error)
            return

        case = await self.record_case(ctx.guild.id, CaseType.WARN.value, member.id, ctx.author.id, reason)
        warn_count = await self.active_warns(ctx.guild.id, member.id)
        delivered = await send_warn_dm(member, reason, case_index=case.index, warn_count=warn_count)

        await ctx.send(
            view=build_warn_result_view(member, case.index, warn_count=warn_count, dm_delivered=delivered),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if note := await self.escalate(case):
            await ctx.send_info(note)

    @command(
        'cases',
        aliases=['history', 'modlogs'],
        description="Show a member's moderation history.",
        guild_only=True,
        user_permissions=PermissionTemplate.messages,
    )
    @describe(user='The member to look up.')
    async def cases(self, ctx: Context, user: discord.User) -> None:
        """List a member's moderation cases."""
        assert ctx.guild is not None
        # The repository defaults to 25 rows, which silently cut both the list and the summary.
        records = await self.bot.db.cases.get_user_cases(ctx.guild.id, user.id, limit=HISTORY_LIMIT)
        if not records:
            await ctx.send_info(f'**{user}** has a clean record — no cases.')
            return

        total = len(records)
        if total == HISTORY_LIMIT:
            total = await self.bot.db.cases.count_user_cases(ctx.guild.id, user.id)
        await CaseHistoryView.start(
            ctx, entries=[ModerationCase.from_record(record) for record in records], user=user, total=total
        )

    @command(
        'warnings',
        description="Show a member's warnings only.",
        guild_only=True,
        user_permissions=PermissionTemplate.messages,
    )
    @describe(user='The member to look up.')
    async def warnings(self, ctx: Context, user: discord.User) -> None:
        """List a member's warnings (pardoned and expired ones included)."""
        assert ctx.guild is not None
        records = await self.bot.db.cases.get_user_cases(ctx.guild.id, user.id, limit=HISTORY_LIMIT, action='warn')
        if not records:
            await ctx.send_info(f'**{user}** has no warnings.')
            return
        active = await self.active_warns(ctx.guild.id, user.id)
        await ctx.send_info(f'**{user}** has **{active}** active warning(s).')
        await CaseHistoryView.start(
            ctx, entries=[ModerationCase.from_record(r) for r in records], user=user, total=len(records)
        )

    @command(
        'pardon',
        description='Pardon a warning so it no longer counts (kept in the history).',
        guild_only=True,
        user_permissions=PermissionTemplate.kick,
    )
    @describe(index='The warn case to pardon.')
    async def pardon(self, ctx: Context, index: int) -> None:
        """Mark one warn inactive."""
        assert ctx.guild is not None
        record = await self.bot.db.cases.pardon_case(ctx.guild.id, index, ctx.author.id)
        if record is None:
            await ctx.send_error(f'Case #{index} is not an active warning.')
            return
        await self._edit_modlog_message(ModerationCase.from_record(record))
        await ctx.send_success(f'Pardoned **Case #{index}**; it no longer counts.')

    @command(
        'clearwarns',
        description="Pardon all of a member's warnings (kept in the history).",
        guild_only=True,
        user_permissions=PermissionTemplate.kick,
    )
    @describe(member='The member whose warnings to clear.')
    async def clearwarns(self, ctx: Context, member: discord.User) -> None:
        """Mark all of a member's warns inactive."""
        assert ctx.guild is not None
        records = await self.bot.db.cases.pardon_user_warns(ctx.guild.id, member.id, ctx.author.id)
        for record in records:
            await self._edit_modlog_message(ModerationCase.from_record(record))
        await ctx.send_success(f'Pardoned {len(records)} warning(s) for **{member}**.')

    @group(
        'warnpolicy',
        fallback='show',
        description='Show the warn expiry and escalation rules.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    async def warnpolicy(self, ctx: Context) -> None:
        """Show the warn policy."""
        assert ctx.guild is not None
        days = await self.bot.db.cases.get_warn_expiry_days(ctx.guild.id)
        rows = await self.bot.db.cases.get_warn_thresholds(ctx.guild.id)
        lines = [f'Warns expire after: **{f"{days} days" if days else "never"}**']
        for r in rows:
            dur = f' for {humanize_duration(r["duration_seconds"])}' if r['duration_seconds'] else ''
            lines.append(f'{r["warn_count"]} active warns -> **{r["action"]}**{dur}')
        if not rows:
            lines.append('No escalation rules. Add one with `warnpolicy set`.')
        await ctx.send_info('\n'.join(lines))

    @warnpolicy.command(
        'set',
        description='Escalate at N active warns: timeout, mute, kick or ban.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(
        count='Active warn count that triggers it.',
        action='timeout, mute, kick or ban.',
        duration='For timeout (required, max 28d) and mute (optional), e.g. 1h.',
    )
    async def warnpolicy_set(self, ctx: Context, count: int, action: str, duration: str | None = None) -> None:
        """Add or replace the rule for a warn count."""
        assert ctx.guild is not None
        action = action.lower()
        seconds = int(ShortTime(duration, as_timedelta=True).dt.total_seconds()) if duration is not None else None  # type: ignore[attr-defined]
        if error := threshold_error(count, action, seconds):
            await ctx.send_error(error)
            return
        await self.bot.db.cases.set_warn_threshold(ctx.guild.id, count, action, seconds)
        await ctx.send_success(f'At **{count}** active warns: **{action}**.')

    @warnpolicy.command(
        'remove', description='Remove the rule for a warn count.', user_permissions=PermissionTemplate.manager
    )
    @describe(count='The warn count whose rule to remove.')
    async def warnpolicy_remove(self, ctx: Context, count: int) -> None:
        """Remove a rule."""
        assert ctx.guild is not None
        if await self.bot.db.cases.remove_warn_threshold(ctx.guild.id, count):
            await ctx.send_success(f'Removed the rule for {count} warns.')
        else:
            await ctx.send_error(f'No rule for {count} warns.')

    @warnpolicy.command(
        'expiry',
        description='Set after how many days new warns stop counting (0 = never).',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(days='Days until a new warn expires; 0 for never. Existing warns keep their expiry.')
    async def warnpolicy_expiry(self, ctx: Context, days: int) -> None:
        """Set warn expiry."""
        assert ctx.guild is not None
        if not 0 <= days <= MAX_EXPIRY_DAYS:
            await ctx.send_error(f'Days must be between 0 and {MAX_EXPIRY_DAYS}.')
            return
        await self.bot.db.cases.set_warn_expiry_days(ctx.guild.id, days or None)
        await ctx.send_success(f'New warns expire after **{days} days**.' if days else 'Warns no longer expire.')

    @command(
        'case',
        description='Show a single moderation case.',
        guild_only=True,
        user_permissions=PermissionTemplate.messages,
    )
    @describe(index='The case number to show.')
    async def case(self, ctx: Context, index: int) -> None:
        """Show one moderation case by number."""
        assert ctx.guild is not None
        record = await self.bot.db.cases.get_case(ctx.guild.id, index)
        if record is None:
            await ctx.send_error(f'Case #{index} does not exist.')
            return
        await ctx.send(
            view=await self.build_case_view(ModerationCase.from_record(record)),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @command(
        'reason',
        description='Update the reason of an existing case.',
        guild_only=True,
        user_permissions=PermissionTemplate.messages,
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
        user_permissions=PermissionTemplate.ban,
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
        user_permissions=PermissionTemplate.manager,
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
        user_permissions=PermissionTemplate.manager,
    )
    async def modlog_disable(self, ctx: Context) -> None:
        """Disable modlog channel posting."""
        assert ctx.guild is not None
        await self.bot.db.cases.set_modlog_channel(ctx.guild.id, None)
        await ctx.send_success('Moderation cases will no longer be posted to a channel (they are still recorded).')


async def setup(bot: Bot) -> None:
    await bot.add_cog(ModLog(bot))
