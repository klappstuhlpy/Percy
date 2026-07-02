"""Internal API moderation endpoints: case CRUD, bulk actions, activity heatmap."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token

if TYPE_CHECKING:
    import discord

    from app.cogs.modlog.cog import ModLog
    from app.cogs.modlog.models import ModerationCase

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Moderation"], dependencies=[Depends(verify_token)])


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


class BulkActionBody(BaseModel):
    user_ids: list[str | int]
    action: str
    reason: str | None = None
    role_ids: list[str | int] = []
    moderator_id: str | int | None = None


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
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0),
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
        'after': datetime.datetime.fromisoformat(after) if after else None,
        'before': datetime.datetime.fromisoformat(before) if before else None,
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
    """Cases created since a timestamp (for event polling/streaming)."""
    from app.cogs.modlog.models import ModerationCase

    since_dt = datetime.datetime.fromisoformat(since)
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

    try:
        target_id = int(body.target_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_id must be a user id')
    if not target_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_id is required')

    moderator_id = int(body.moderator_id) if body.moderator_id else None
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
) -> dict:
    """Close (delete) a case and remove its modlog channel post."""
    from app.cogs.modlog.models import ModerationCase

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
    """Apply a moderation action to multiple members at once."""
    if not body.user_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='user_ids is required')
    if body.action not in ('kick', 'ban', 'unban', 'add_roles', 'remove_roles'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='action must be kick, ban, unban, add_roles, or remove_roles',
        )
    if body.action in ('add_roles', 'remove_roles') and not body.role_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='role_ids is required for role actions')

    bot_member = guild.me
    moderator_id = int(body.moderator_id) if body.moderator_id else None
    reason = body.reason or None
    role_ids = [str(rid) for rid in body.role_ids]

    roles = [r for r in guild.roles if str(r.id) in role_ids] if role_ids else []

    successes: list[str | int] = []
    failures: list[dict] = []

    for uid in body.user_ids:
        uid_int = int(uid)
        try:
            if body.action == 'unban':
                user = await bot.fetch_user(uid_int)
                await guild.unban(user, reason=reason)
            else:
                member = guild.get_member(uid_int)
                if member is None:
                    failures.append({'user_id': uid, 'error': 'member not found'})
                    continue
                if body.action in ('kick', 'ban') and member.top_role >= bot_member.top_role:
                    failures.append({'user_id': uid, 'error': 'higher role hierarchy'})
                    continue

                if body.action == 'kick':
                    await member.kick(reason=reason)
                elif body.action == 'ban':
                    await member.ban(reason=reason)
                elif body.action == 'add_roles':
                    await member.add_roles(*roles, reason=reason or 'Dashboard bulk role update')
                elif body.action == 'remove_roles':
                    await member.remove_roles(*roles, reason=reason or 'Dashboard bulk role update')

            if body.action in ('kick', 'ban', 'unban'):
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
    days: int = Query(default=365, le=365),
) -> dict:
    """Daily command counts for a member (activity heatmap data)."""
    records = await bot.db.stats.get_member_daily_activity(guild.id, user_id, days=days)

    activity = [
        {'day': record['day'].isoformat(), 'count': record['count']}
        for record in records
    ]
    return {'activity': activity, 'days': days}
