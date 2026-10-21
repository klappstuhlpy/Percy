"""Guild notification feed and moderator -> admin action requests, for the dashboard.

Permission checks for a decision stay in :func:`app.cogs.notifications.decide.decide_request`.
"""
from __future__ import annotations

from typing import Literal

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.cogs.notifications.decide import decide_request
from app.services.notifications import notification_payload, request_payload

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import resolve_channel, resolve_role

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Notifications"], dependencies=[Depends(verify_token)])


class MarkReadBody(BaseModel):
    user_id: int
    up_to_id: int


class DecideBody(BaseModel):
    moderator_id: int
    decision: Literal['approve', 'deny']
    note: str | None = None


class RequestSettingsBody(BaseModel):
    channel_id: int | None = None
    role_ids: list[int] = []


def _name(guild, user_id: int | None) -> str | None:
    member = guild.get_member(user_id) if user_id is not None else None
    return member.display_name if member else None


def _enriched(guild, row) -> dict:
    item = request_payload(row)
    item["requester_name"] = _name(guild, row["requester_id"])
    item["target_name"] = _name(guild, row["target_id"])
    item["decided_by_name"] = _name(guild, row.get("decided_by"))
    return item


@router.get("/notifications")
async def list_notifications(
    guild: GuildDep, bot: BotDep, user_id: int,
    limit: int = Query(30, ge=1, le=100), before: int | None = None,
) -> dict:
    repo = bot.db.notifications
    last_read = await repo.last_read(guild.id, user_id)
    rows = await repo.list(guild.id, limit=limit, before_id=before)
    return {
        "items": [notification_payload(r, last_read) for r in rows],
        "unread": await repo.unread_count(guild.id, user_id),
        "last_read_id": last_read,
    }


@router.get("/notifications/summary")
async def notifications_summary(guild: GuildDep, bot: BotDep, user_id: int) -> dict:
    repo = bot.db.notifications
    return {
        "unread": await repo.unread_count(guild.id, user_id),
        "pending_requests": await repo.pending_count(guild.id),
    }


@router.post("/notifications/read")
async def mark_notifications_read(guild: GuildDep, bot: BotDep, body: MarkReadBody) -> dict:
    repo = bot.db.notifications
    await repo.mark_read(guild.id, body.user_id, body.up_to_id)
    return {"ok": True, "unread": await repo.unread_count(guild.id, body.user_id)}


@router.get("/requests")
async def list_requests(
    guild: GuildDep, bot: BotDep,
    status_filter: Literal['pending', 'all'] = Query('pending', alias='status'),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    rows = await bot.db.notifications.list_requests(
        guild.id, status=None if status_filter == 'all' else 'pending', limit=limit
    )
    return {"items": [_enriched(guild, row) for row in rows]}


async def _settings(guild, bot) -> dict:
    config = await bot.db.get_guild_config(guild.id)
    role_ids = list(config.request_role_ids)
    return {
        "channel": resolve_channel(guild, config.request_channel_id),
        "role_ids": [str(r) for r in role_ids],
        "roles": [resolve_role(guild, r) for r in role_ids],
    }


# Declared before /requests/{request_id}/... so "settings" is never read as an id.
@router.get("/requests/settings")
async def get_request_settings(guild: GuildDep, bot: BotDep) -> dict:
    return await _settings(guild, bot)


@router.patch("/requests/settings")
async def patch_request_settings(guild: GuildDep, bot: BotDep, body: RequestSettingsBody) -> dict:
    if body.channel_id is not None:
        channel = guild.get_channel(body.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="channel must be a text channel")
    if any(guild.get_role(r) is None for r in body.role_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown role")
    # The repository fires the guild_config cache invalidation.
    await bot.db.notifications.update_request_settings(guild.id, body.channel_id, sorted(set(body.role_ids)))
    return await _settings(guild, bot)


@router.post("/requests/{request_id}/decide")
async def decide(guild: GuildDep, bot: BotDep, request_id: int, body: DecideBody) -> JSONResponse:
    moderator = guild.get_member(body.moderator_id)
    if moderator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="moderator not in server")
    decision = await decide_request(
        bot, guild, request_id, moderator, approve=body.decision == 'approve', note=body.note
    )
    request = _enriched(guild, decision.request) if decision.request else None
    return JSONResponse(
        {"ok": decision.ok, "status": decision.status, "message": decision.message, "request": request},
        status_code=decision.http_status,
    )
