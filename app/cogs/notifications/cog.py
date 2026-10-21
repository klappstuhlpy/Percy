"""Notifications: moderator -> admin action requests in Discord, plus the feed's housekeeping.

The decision logic is in :mod:`.decide` (shared with the internal API); this cog is the controller
around it: the ``request`` commands, the persistent Approve/Deny buttons, the expiry/prune loops and
the owner-only global ``notice`` commands.
"""

from __future__ import annotations

import datetime
import logging
from typing import Literal

import discord
from discord.ext import commands, tasks

from app.core import Bot, Cog, Context, command, describe, group
from app.services.notifications import ACTION_PERMISSIONS, REQUESTABLE_ACTIONS, SEVERITIES, check_request
from app.utils import timetools  # noqa: TC001 -- discord.py evaluates the converter annotation

from .decide import edit_request_message, notify
from .ui import RequestDecisionButton, request_embed, request_view

log = logging.getLogger(__name__)

__all__ = ('Notifications',)

Action = Literal['kick', 'ban', 'unban', 'softban', 'warn', 'mute', 'unmute', 'timeout', 'untimeout']
assert set(Action.__args__) == REQUESTABLE_ACTIONS  # type: ignore[attr-defined]


class Notifications(Cog):
    """Action requests and the dashboard notification feed."""

    emoji = '\N{BELL}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(RequestDecisionButton)
        self.expire_requests.start()
        self.prune_notifications.start()

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(RequestDecisionButton)
        self.expire_requests.cancel()
        self.prune_notifications.cancel()

    # -- Loops -----------------------------------------------------------------------------

    @tasks.loop(minutes=30)
    async def expire_requests(self) -> None:
        try:
            for row in await self.bot.db.notifications.expire_requests():
                guild = self.bot.get_guild(row['guild_id'])
                if guild is not None:
                    await edit_request_message(guild, dict(row), expired=True)
        except Exception:
            log.exception('Expiring action requests failed.')

    @tasks.loop(hours=24)
    async def prune_notifications(self) -> None:
        try:
            await self.bot.db.notifications.prune()
        except Exception:
            log.exception('Pruning notifications failed.')

    @expire_requests.before_loop
    @prune_notifications.before_loop
    async def _before_loops(self) -> None:
        await self.bot.wait_until_ready()

    # -- Requests --------------------------------------------------------------------------

    @group('request', description='Ask a moderator with the right permission to run an action.', guild_only=True, hybrid=True)
    async def request(self, ctx: Context) -> None:
        """Ask for a moderation action you can't run yourself."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @request.command('create', description='Request a moderation action for approval.', guild_only=True)
    @describe(
        action='The action to request.',
        member='The member it applies to.',
        reason='Why it is needed.',
        duration='For timeout and mute: how long, e.g. 1h.',
    )
    async def request_create(
        self,
        ctx: Context,
        action: Action,
        member: discord.Member,
        reason: str,
        duration: timetools.FutureTime | None = None,
    ) -> None:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        config = await self.bot.db.get_guild_config(ctx.guild.id)
        perms = ctx.author.guild_permissions
        if perms.administrator or getattr(perms, ACTION_PERMISSIONS[action], False):
            await ctx.send_info('You can run this yourself.')
            return
        allowed = set(config.request_role_ids)
        if not (perms.manage_messages or any(r.id in allowed for r in ctx.author.roles)):
            await ctx.send_error('You are not allowed to file action requests here.')
            return
        if member.id == ctx.author.id:
            await ctx.send_error("You can't request an action against yourself.")
            return

        seconds = None
        if duration is not None:
            seconds = int((duration.dt - discord.utils.utcnow()).total_seconds())
        if error := check_request(action, seconds):
            await ctx.send_error(error)
            return

        row = await self.bot.db.notifications.create_request(
            ctx.guild.id, ctx.author.id, member.id, action, reason[:500], seconds
        )
        await notify(
            self.bot,
            ctx.guild.id,
            kind='action_request',
            severity='warning',
            title=f'{ctx.author} requested a {action}',
            body=reason[:500],
            link=f'/dashboard/guild/{ctx.guild.id}/notifications#requests',
            meta={'request_id': row['id']},
        )

        channel = ctx.guild.get_channel(config.request_channel_id) if config.request_channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await ctx.send_success(f'Request #{row["id"]} filed. Admins will see it on the dashboard.')
            return
        try:
            message = await channel.send(embed=request_embed(row), view=request_view(row['id']))
        except discord.Forbidden:
            await config.alert_missing_permission('post action requests', channel=channel)
            await ctx.send_success(f'Request #{row["id"]} filed. Admins will see it on the dashboard.')
            return
        await self.bot.db.notifications.set_request_message(row['id'], channel.id, message.id)
        await ctx.send_success(f'Request #{row["id"]} sent to {channel.mention}.')

    @request.command('list', description='Shows your last 10 requests.', guild_only=True)
    async def request_list(self, ctx: Context) -> None:
        assert ctx.guild is not None
        rows = [
            r
            for r in await self.bot.db.notifications.list_requests(ctx.guild.id, limit=200)
            if r['requester_id'] == ctx.author.id
        ][:10]
        if not rows:
            await ctx.send_info('You have not filed any requests.')
            return
        lines = [f'`#{r["id"]}` {r["action"]} <@{r["target_id"]}>: **{r["status"]}**' for r in rows]
        await ctx.send('\n'.join(lines), ephemeral=True)

    @request.command('cancel', description='Cancels one of your pending requests.', guild_only=True)
    @describe(request_id='The request number.')
    async def request_cancel(self, ctx: Context, request_id: int) -> None:
        assert ctx.guild is not None
        repo = self.bot.db.notifications
        row = await repo.get_request(ctx.guild.id, request_id)
        if row is None or row['requester_id'] != ctx.author.id:
            await ctx.send_error('You have no request with that number.')
            return
        claimed = await repo.claim_request(
            ctx.guild.id, request_id, status='cancelled', decided_by=ctx.author.id, note=None
        )
        if claimed is None:
            await ctx.send_error(f'That request is already {row["status"]}.')
            return
        await edit_request_message(ctx.guild, dict(claimed))
        await ctx.send_success(f'Request #{request_id} cancelled.')

    # -- Owner notices -----------------------------------------------------------------------

    @group('notice', description='Post a notice to every server\'s dashboard.', hidden=True, hybrid=False)
    @commands.is_owner()
    async def notice(self, ctx: Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @notice.command('post', description='Posts a global notice: <severity> <title> | <body>.')
    async def notice_post(self, ctx: Context, severity: str, *, text: str) -> None:
        if severity not in SEVERITIES:
            await ctx.send_error(f'Severity must be one of: {", ".join(SEVERITIES)}.')
            return
        title, _, body = text.partition('|')
        if not title.strip():
            await ctx.send_error('Usage: notice post <severity> <title> | <body>')
            return
        await notify(self.bot, None, kind='notice', severity=severity, title=title.strip()[:120], body=body.strip())
        await ctx.send_success('Notice posted.')

    @notice.command('remove', description='Removes a notice by id.')
    async def notice_remove(self, ctx: Context, notice_id: int) -> None:
        if await self.bot.db.notifications.delete(notice_id):
            await ctx.send_success('Notice removed.')
        else:
            await ctx.send_error('No notification with that id.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(Notifications(bot))
