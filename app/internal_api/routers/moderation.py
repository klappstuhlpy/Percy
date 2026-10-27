"""Internal API moderation endpoints: case CRUD, bulk actions, activity heatmap."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.utils.checks import hierarchy_error

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import parse_id, parse_timestamp, resolve_moderator, role_error, validate_timeout_duration

if TYPE_CHECKING:
    from app.cogs.modlog.cog import ModLog
    from app.cogs.modlog.models import ModerationCase

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Moderation"], dependencies=[Depends(verify_token)])

# Each bulk action and the permission the acting moderator needs, as for the single-member actions.
BULK_PERMISSIONS: dict[str, str] = {
    'kick': 'kick_members',
    'ban': 'ban_members',
    'unban': 'ban_members',
    'softban': 'ban_members',
    'timeout': 'moderate_members',
    'warn': 'kick_members',
    'add_roles': 'manage_roles',
    'remove_roles': 'manage_roles',
}
BULK_ACTIONS = frozenset(BULK_PERMISSIONS)
# Actions refused when the target outranks Percy.
BULK_HIERARCHY_ACTIONS = frozenset({'kick', 'ban', 'softban', 'timeout'})
# Role edits follow Discord's role rule (``role_error``), not the member hierarchy.
BULK_ROLE_ACTIONS = frozenset({'add_roles', 'remove_roles'})
# Actions that leave a modlog case behind (role edits are config, not moderation).
BULK_CASE_ACTIONS = frozenset({'kick', 'ban', 'unban', 'softban', 'timeout', 'warn'})


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateCaseBody(BaseModel):
    action: str
    target_id: str | int
    moderator_id: str | int | None = None
    reason: str | None = None


class PatchCaseBody(BaseModel):
    reason: str
    #: The dashboard user editing the case; when present they need Manage Server.
    moderator_id: str | int | None = None


class BulkActionBody(BaseModel):
    user_ids: list[str | int]
    action: str
    reason: str | None = None
    role_ids: list[str | int] = []
    moderator_id: str | int | None = None
    #: Seconds, required by the ``timeout`` action.
    duration: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _case_payload(guild: discord.Guild, case: ModerationCase, bot) -> dict:
    """Serialize a ModerationCase with moderator/target names resolved against the guild."""
    moderator_name = None
    if case.moderator_id is not None:
        mod = guild.get_member(case.moderator_id) or bot.get_user(case.moderator_id)
        moderator_name = mod.display_name if mod else f'Unknown ({case.moderator_id})'

    target = guild.get_member(case.target_id) or bot.get_user(case.target_id)
    target_name = target.display_name if target else f'Unknown ({case.target_id})'

    return {
        'case_index': case.index,
        'action': case.action,
        'target_id': str(case.target_id),
        'target_name': target_name,
        'moderator_id': str(case.moderator_id) if case.moderator_id is not None else None,
        'moderator_name': moderator_name,
        'reason': case.reason,
        'created_at': case.created_at.isoformat() if case.created_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/cases")
async def get_cases(
    bot: BotDep,
    guild: GuildDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
    moderator_id: int | None = Query(default=None),
    target_id: int | None = Query(default=None),
    after: str | None = Query(default=None),
    before: str | None = Query(default=None),
) -> dict:
    """Paginated, filterable list of moderation cases for a guild."""
    from app.cogs.modlog.models import ModerationCase

    kwargs: dict = {
        'action': action or None,
        'moderator_id': moderator_id,
        'target_id': target_id,
        'after': parse_timestamp(after, 'after'),
        'before': parse_timestamp(before, 'before'),
        'limit': limit,
        'offset': offset,
    }

    records = await bot.db.cases.get_cases(guild.id, **kwargs)
    total = await bot.db.cases.count_cases(
        guild.id,
        action=kwargs['action'],
        moderator_id=kwargs['moderator_id'],
        target_id=kwargs['target_id'],
        after=kwargs['after'],
        before=kwargs['before'],
    )

    cases = [_case_payload(guild, ModerationCase.from_record(record), bot) for record in records]
    return {'cases': cases, 'total': total}


@router.get("/cases/recent")
async def get_recent_cases(
    bot: BotDep,
    guild: GuildDep,
    since: str = Query(...),
) -> dict:
    """Cases created strictly after a timestamp (for event polling/streaming).

    The poller passes the newest ``created_at`` it already has, so that case is not repeated.
    """
    from app.cogs.modlog.models import ModerationCase

    since_dt = parse_timestamp(since, 'since')
    if since_dt is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='since is required')
    records = await bot.db.cases.get_recent_cases(guild.id, since=since_dt)

    cases = [_case_payload(guild, ModerationCase.from_record(record), bot) for record in records]
    return {'cases': cases}


@router.post("/cases")
async def create_case(
    bot: BotDep,
    guild: GuildDep,
    body: CreateCaseBody,
) -> dict:
    """Manually open a moderation case (records only, does not perform the action)."""
    from app.cogs.modlog.models import CaseType, ModerationCase

    if CaseType.from_action(body.action) is None:
        valid = ', '.join(case_type.value for case_type in CaseType)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'action must be one of: {valid}')

    # parse_id also refuses ids past BIGINT, which asyncpg would otherwise answer with a 500.
    target_id = parse_id(body.target_id, 'target_id')

    moderator_id = parse_id(body.moderator_id, 'moderator_id') if body.moderator_id else None
    reason = body.reason or None

    cog: ModLog | None = bot.get_cog('ModLog')  # type: ignore[assignment]
    if cog is not None:
        case = await cog.record_case(guild.id, body.action, target_id, moderator_id, reason)
    else:
        record = await bot.db.cases.create_case(guild.id, body.action, target_id, moderator_id, reason)
        case = ModerationCase.from_record(record)

    return {'ok': True, 'case': _case_payload(guild, case, bot)}


@router.patch("/cases/{case_index}")
async def patch_case(
    case_index: int,
    bot: BotDep,
    guild: GuildDep,
    body: PatchCaseBody,
) -> dict:
    """Update a case's reason (and its modlog channel post)."""
    from app.cogs.modlog.models import ModerationCase

    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='reason is required')
    if body.moderator_id not in (None, ''):
        await resolve_moderator(guild, body.moderator_id, 'manage_guild')

    cog: ModLog | None = bot.get_cog('ModLog')  # type: ignore[assignment]
    if cog is not None:
        case = await cog.update_case_reason(guild.id, case_index, reason)
    else:
        record = await bot.db.cases.update_reason(guild.id, case_index, reason)
        case = ModerationCase.from_record(record) if record is not None else None

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='case not found')
    return {'ok': True, 'case': _case_payload(guild, case, bot)}


@router.delete("/cases/{case_index}")
async def delete_case(
    case_index: int,
    bot: BotDep,
    guild: GuildDep,
    moderator_id: str | None = Query(default=None),
) -> dict:
    """Close (delete) a case and remove its modlog channel post.

    With ``moderator_id`` the deleting dashboard user must hold Manage Server.
    """
    from app.cogs.modlog.models import ModerationCase

    if moderator_id:
        await resolve_moderator(guild, moderator_id, 'manage_guild')

    cog: ModLog | None = bot.get_cog('ModLog')  # type: ignore[assignment]
    if cog is not None:
        case = await cog.delete_case(guild.id, case_index)
    else:
        record = await bot.db.cases.delete_case(guild.id, case_index)
        case = ModerationCase.from_record(record) if record is not None else None

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='case not found')
    return {'ok': True, 'case_index': case.index}


@router.post("/members/bulk-action")
async def bulk_member_action(
    bot: BotDep,
    guild: GuildDep,
    body: BulkActionBody,
) -> dict:
    """Apply a moderation action to multiple members at once.

    ``moderator_id`` is required and checked once (permission) and per member (hierarchy). Every
    id is parsed before anything happens, so a malformed one never leaves the batch half done.
    """
    if not body.user_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='user_ids is required')
    if body.action not in BULK_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'action must be one of: {", ".join(sorted(BULK_ACTIONS))}',
        )
    if body.action in ('add_roles', 'remove_roles') and not body.role_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='role_ids is required for role actions')

    timeout_for = validate_timeout_duration(body.duration) if body.action == 'timeout' else None

    moderator = await resolve_moderator(guild, body.moderator_id, BULK_PERMISSIONS[body.action])
    moderator_id = moderator.id
    user_ids = [parse_id(uid, 'user_ids') for uid in body.user_ids]
    reason = body.reason or None
    role_ids = {parse_id(rid, 'role_ids') for rid in body.role_ids}

    roles = [r for r in guild.roles if r.id in role_ids]
    for role in roles:
        if error := role_error(role, moderator):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)

    successes: list[str | int] = []
    failures: list[dict] = []

    for uid, uid_int in zip(body.user_ids, user_ids, strict=True):
        try:
            if body.action == 'unban':
                if error := hierarchy_error(moderator, discord.Object(id=uid_int), guild.me, action='unban'):
                    failures.append({'user_id': uid, 'error': error})
                    continue
                await guild.unban(discord.Object(id=uid_int), reason=reason)
                # As the unban command does: an early unban ends the tempban, so its timer must not lift a later ban.
                await bot.timers.delete_member_timer('tempban', guild.id, uid_int)
            else:
                member = guild.get_member(uid_int)
                if member is None:
                    failures.append({'user_id': uid, 'error': 'member not found'})
                    continue
                if body.action not in BULK_ROLE_ACTIONS and (
                    error := hierarchy_error(
                        moderator, member, guild.me, action=body.action, check_bot=body.action in BULK_HIERARCHY_ACTIONS
                    )
                ):
                    failures.append({'user_id': uid, 'error': error})
                    continue

                if body.action == 'kick':
                    await member.kick(reason=reason)
                elif body.action == 'ban':
                    await member.ban(reason=reason)
                    await bot.timers.delete_member_timer('tempban', guild.id, uid_int)
                elif body.action == 'softban':
                    from app.cogs.moderation.infractions import softban

                    if not await softban(bot, guild, member, reason=reason):
                        # The ban stands: record it as one and report it apart from a plain failure.
                        bot.dispatch('mod_action', guild.id, 'ban', uid_int, moderator_id, reason)
                        failures.append({'user_id': uid, 'error': 'banned, but lifting the ban failed: still banned'})
                        continue
                elif body.action == 'timeout':
                    await member.timeout(timeout_for, reason=reason)
                elif body.action == 'warn':
                    if member.bot:
                        failures.append({'user_id': uid, 'error': 'cannot warn a bot'})
                        continue
                elif body.action == 'add_roles':
                    await member.add_roles(*roles, reason=reason or 'Dashboard bulk role update')
                elif body.action == 'remove_roles':
                    await member.remove_roles(*roles, reason=reason or 'Dashboard bulk role update')

            if body.action in BULK_CASE_ACTIONS:
                bot.dispatch('mod_action', guild.id, body.action, uid_int, moderator_id, reason)
            successes.append(uid)
        except Exception as e:
            failures.append({'user_id': uid, 'error': str(e)})

    return {
        'ok': True,
        'successes': len(successes),
        'failures': failures,
    }


@router.get("/members/{user_id}/activity")
async def get_member_activity(
    user_id: int,
    bot: BotDep,
    guild: GuildDep,
    days: int = Query(default=365, ge=1, le=365),
) -> dict:
    """Daily command counts for a member (activity heatmap data)."""
    records = await bot.db.stats.get_member_daily_activity(guild.id, user_id, days=days)

    activity = [
        {'day': record['day'].isoformat(), 'count': record['count']}
        for record in records
    ]
    return {'activity': activity, 'days': days}
