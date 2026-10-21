"""Deciding an action request -- shared by the Discord buttons and the internal API."""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action
from app.services.notifications import ACTION_PERMISSIONS

from .ui import request_embed

if TYPE_CHECKING:
    from app.core import Bot

log = logging.getLogger(__name__)

__all__ = ('RequestDecision', 'decide_request', 'edit_request_message', 'notify')


@dataclass
class RequestDecision:
    ok: bool
    status: str
    message: str
    request: dict | None
    http_status: int


async def notify(bot: Bot, guild_id: int | None, **fields: Any) -> None:
    """``db.notifications.add`` that never raises -- a feed hiccup must not break the caller."""
    try:
        await bot.db.notifications.add(guild_id, **fields)
    except Exception:
        log.warning('Could not record a notification for guild %s.', guild_id, exc_info=True)


def _fail(status: str, message: str, http_status: int, request: Any = None) -> RequestDecision:
    return RequestDecision(False, status, message, dict(request) if request else None, http_status)


async def decide_request(
    bot: Bot,
    guild: discord.Guild,
    request_id: int,
    decider: discord.Member,
    *,
    approve: bool,
    note: str | None = None,
) -> RequestDecision:
    repo = bot.db.notifications
    row = await repo.get_request(guild.id, request_id)
    if row is None:
        return _fail('unknown', 'Request not found.', 404)
    if row['status'] != 'pending':
        return _fail(row['status'], f'This request was already {row["status"]}.', 409, row)
    if decider.id == row['requester_id']:
        return _fail('pending', "You can't decide your own request.", 400, row)

    permission = ACTION_PERMISSIONS[row['action']]
    perms = decider.guild_permissions
    if not (perms.administrator or getattr(perms, permission, False)):
        label = permission.replace('_', ' ').title()
        return _fail('pending', f'You need the {label} permission to decide a {row["action"]} request.', 403, row)

    claimed = await repo.claim_request(
        guild.id, request_id, status='approved' if approve else 'denied', decided_by=decider.id, note=note
    )
    if claimed is None:
        return _fail('expired', 'This request was already decided or has expired.', 409, row)

    status, result = claimed['status'], None
    if approve:
        try:
            await apply_member_action(
                bot,
                guild,
                decider,
                claimed['target_id'],
                action=claimed['action'],
                reason=f'{claimed["reason"]} (request #{request_id} by {claimed["requester_id"]})',
                duration=claimed['duration'],
            )
            status, result = 'approved', 'done'
        except MemberActionError as exc:
            status, result = 'failed', exc.detail
        await repo.finish_request(request_id, status=status, result=result)

    final = {**dict(claimed), 'status': status, 'result': result}
    message = {
        'approved': 'Approved and carried out.',
        'denied': 'Denied.',
        'failed': f'Approved, but it failed: {result}',
    }[status]
    await _announce(bot, guild, final, decider)
    return RequestDecision(status != 'failed', status, message, final, 200)


async def _announce(bot: Bot, guild: discord.Guild, request: dict, decider: discord.Member) -> None:
    """Edit the approval message, DM the requester, add the feed entry -- each best-effort."""
    with suppress(Exception):
        await edit_request_message(guild, request)
    summary = f'{request["action"].title()} request #{request["id"]} was {request["status"]} by {decider}.'
    if request.get('decision_note'):
        summary += f'\nNote: {request["decision_note"]}'
    with suppress(Exception):
        requester = guild.get_member(request['requester_id']) or await bot.fetch_user(request['requester_id'])
        await requester.send(summary)
    await notify(
        bot,
        guild.id,
        kind='request_decided',
        severity='warning' if request['status'] == 'failed' else 'info',
        title=f'Request #{request["id"]} {request["status"]}',
        body=summary,
        link=f'/dashboard/guild/{guild.id}/notifications#requests',
    )


async def edit_request_message(guild: discord.Guild, request: dict, *, expired: bool = False) -> None:
    """Rewrites the approval message to show the outcome and drops its buttons."""
    if not request.get('channel_id') or not request.get('message_id'):
        return
    channel = guild.get_channel(request['channel_id'])
    if not isinstance(channel, discord.abc.Messageable):
        return
    with suppress(discord.HTTPException):
        await channel.get_partial_message(request['message_id']).edit(
            embed=request_embed(request, expired=expired), view=None
        )
