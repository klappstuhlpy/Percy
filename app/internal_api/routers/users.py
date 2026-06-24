"""User-scoped endpoints (not guild-scoped) -- settings, history, data export."""
from __future__ import annotations

import base64
import json
import zoneinfo

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from ..dependencies import BotDep, verify_token

router = APIRouter(prefix="/users/{discord_id}", tags=["Users"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchUserSettingsBody(BaseModel):
    timezone: str | None = None
    track_presence: bool | None = None
    track_history: bool | None = None

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/guilds")
async def get_user_guilds(discord_id: int, bot: BotDep) -> list[dict]:
    """Every guild the user shares with Percy, each with a ``manageable`` flag."""
    guilds = []
    for guild in bot.guilds:
        member = guild.get_member(discord_id)
        if member is None:
            continue
        perms = member.guild_permissions
        guilds.append({
            "id": str(guild.id),
            "name": guild.name,
            "icon_url": guild.icon.url if guild.icon else None,
            "member_count": guild.member_count,
            "owner": guild.owner_id == discord_id,
            "manageable": perms.administrator or perms.manage_guild,
        })

    return guilds


@router.get("/avatar")
async def get_user_avatar(discord_id: int, bot: BotDep) -> dict:
    """Resolve the user's current avatar URL from Discord (fetch if not cached)."""
    user = bot.get_user(discord_id)
    if user is None:
        try:
            user = await bot.fetch_user(discord_id)
        except discord.HTTPException:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    return {
        "avatar_url": user.display_avatar.url,
        "username": user.name,
    }


@router.get("/settings")
async def get_user_settings(discord_id: int, bot: BotDep) -> dict:
    """User's personal bot settings (timezone, track_presence, track_history)."""
    config = await bot.db.get_user_config(discord_id)

    return {
        "timezone": config.timezone,
        "track_presence": config.track_presence,
        "track_history": config.track_history,
    }


@router.patch("/settings")
async def patch_user_settings(discord_id: int, bot: BotDep, body: PatchUserSettingsBody) -> dict:
    """Update user's personal bot settings."""
    config = await bot.db.get_user_config(discord_id)

    if body.timezone is not None:
        tz = body.timezone
        if tz == "":
            await bot.db.users.clear_timezone(discord_id)
        else:
            if tz not in zoneinfo.available_timezones():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"invalid timezone: {tz}",
                )
            await bot.db.users.set_timezone(discord_id, tz)

    if body.track_presence is not None:
        await config.update(track_presence=body.track_presence)

    if body.track_history is not None:
        await config.update(track_history=body.track_history)

    return {"ok": True}


@router.get("/history")
async def get_user_history(
    discord_id: int,
    bot: BotDep,
    avatar_limit: int = Query(default=24, le=50),
) -> dict:
    """Consent-tracked history: usernames, nicknames, avatars (base64), presence over 30 days."""
    usernames = await bot.db.stats.get_item_history(discord_id, "name")
    nicknames = await bot.db.stats.get_item_history(discord_id, "nickname")
    avatars = await bot.db.stats.get_avatar_history(discord_id, limit=avatar_limit)
    presence = await bot.db.stats.get_presence_history(discord_id, days=30)

    def _ts(record: object) -> str | None:
        value = record["changed_at"]  # type: ignore[index]
        return value.isoformat() if value else None

    return {
        "usernames": [{"name": r["item_value"], "changed_at": _ts(r)} for r in usernames],
        "nicknames": [{"name": r["item_value"], "changed_at": _ts(r)} for r in nicknames],
        "avatars": [
            {"image": base64.b64encode(r["avatar"]).decode(), "changed_at": _ts(r)}
            for r in avatars
        ],
        "presence": [
            {"status": r["status"], "status_before": r["status_before"], "changed_at": _ts(r)}
            for r in presence
        ],
    }


@router.get("/data-export")
async def get_data_export(discord_id: int, bot: BotDep) -> Response:
    """GDPR full export of all user-keyed data Percy stores."""
    data = await bot.db.users.export_all_user_data(discord_id)
    content = json.dumps(data, default=str, ensure_ascii=False)
    return Response(content=content, media_type="application/json")


@router.delete("/personal-data")
async def delete_personal_data(discord_id: int, bot: BotDep) -> dict:
    """Erase tracked history (presence, avatar, name/nickname changes)."""
    await bot.db.users.delete_personal_data(discord_id)
    return {"ok": True}
