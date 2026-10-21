"""User-scoped endpoints (not guild-scoped) -- settings, history, data export."""
from __future__ import annotations

import base64
import json
import time
import zoneinfo
from typing import TYPE_CHECKING, Any

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.rendering.card_style import clean_style, resolve_style, style_options

from ..dependencies import BotDep, verify_token

if TYPE_CHECKING:
    from app.cogs.leveling.cog import Leveling
    from app.core import Bot

router = APIRouter(prefix="/users/{discord_id}", tags=["Users"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchUserSettingsBody(BaseModel):
    timezone: str | None = None
    track_presence: bool | None = None
    track_history: bool | None = None

    model_config = {"extra": "ignore"}


class RankCardBody(BaseModel):
    style: dict[str, Any]


class RankCardPreviewBody(BaseModel):
    guild_id: int
    style: dict[str, Any] = {}


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


# ---------------------------------------------------------------------------
# Rank card style (the same store `/level card` writes)
# ---------------------------------------------------------------------------

# ponytail: in-process per-user throttle on previews (a render is ~50 ms of CPU); move to a
# shared limiter if the API ever runs as more than one process.
_PREVIEW_INTERVAL = 0.5
_last_preview: dict[int, float] = {}


def _leveling(bot: Bot) -> Leveling:
    cog = bot.get_cog('Leveling')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="leveling is not loaded")
    return cog  # type: ignore[return-value]


def _member(bot: Bot, guild_id: int, discord_id: int) -> discord.Member:
    guild = bot.get_guild(guild_id)
    member = guild.get_member(discord_id) if guild else None
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found on that server")
    return member


def _clean(style: dict[str, Any]) -> dict[str, str]:
    try:
        return clean_style(style)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/rank-card")
async def get_rank_card(discord_id: int, bot: BotDep, guild_id: int = Query(...)) -> dict:
    """The member's own style, the server default, the effective result and every preset.

    ``locked`` means the server fixes the style: clients hide the editor entirely.
    """
    guild_default, user_style, locked = await _leveling(bot).card_layers(_member(bot, guild_id, discord_id))
    return {
        "style": user_style,
        "guild_default": guild_default,
        "locked": locked,
        "effective": resolve_style(guild_default, user_style, locked=locked).to_dict(),
        "options": style_options(),
    }


@router.put("/rank-card")
async def put_rank_card(discord_id: int, bot: BotDep, body: RankCardBody) -> dict:
    """Replace the member's style with a validated partial style (``{}`` = follow the server)."""
    style = _clean(body.style)
    config = await bot.db.get_user_config(discord_id)
    await config.update(rank_card=style)
    return {"ok": True, "style": style}


@router.post("/rank-card/preview")
async def preview_rank_card(discord_id: int, bot: BotDep, body: RankCardPreviewBody) -> Response:
    """Render the member's real card in a draft style, as PNG. Nothing is stored."""
    now = time.monotonic()
    if now - _last_preview.get(discord_id, 0.0) < _PREVIEW_INTERVAL:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="previewing too fast")
    _last_preview[discord_id] = now

    cog = _leveling(bot)
    member = _member(bot, body.guild_id, discord_id)
    level = await cog.get_level_config(member.id, member.guild.id)
    if level is None or level.xp == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="earn some XP on this server first")

    guild_default, _, locked = await cog.card_layers(member)
    style = resolve_style(guild_default, _clean(body.style), locked=locked)
    file = await bot.render.level_card(member, level, style=style, boosts=await cog.active_boosts(member))
    return Response(content=file.fp.read(), media_type="image/png")


# ---------------------------------------------------------------------------
# Reminders (the member's own; the same rows `reminder list` shows)
# ---------------------------------------------------------------------------


@router.get("/reminders")
async def get_user_reminders(discord_id: int, bot: BotDep) -> dict:
    rows = await bot.db.timers.get_user_reminders(discord_id)
    return {
        "reminders": [
            {
                "id": row[0],
                "expires": row[1].isoformat() + "Z",
                "message": row[2] or "",
                "repeats": row[3],
                "shared_with": row[4],
            }
            for row in rows
        ]
    }


@router.delete("/reminders/{reminder_id}")
async def delete_user_reminder(discord_id: int, reminder_id: int, bot: BotDep) -> dict:
    """Cancel one reminder the member owns; someone else's id is a 404, never a delete."""
    status_tag = await bot.db.timers.delete_reminder(reminder_id, discord_id)
    if status_tag == "DELETE 0":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reminder not found")
    timers = bot.timers
    if timers and timers._loaded_timer and timers._loaded_timer.id == reminder_id:
        timers.reset_task()
    return {"ok": True}
