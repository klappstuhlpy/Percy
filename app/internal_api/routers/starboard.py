"""Starboard settings and the most-starred messages, for the dashboard.

Writes go through the ``Starboard`` cog's ``update_config`` so its in-memory config cache is
replaced in the same step -- the next reaction already sees the new threshold or channel.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import resolve_channel

if TYPE_CHECKING:
    from app.cogs.starboard.cog import Starboard
    from app.core import Bot

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Starboard"], dependencies=[Depends(verify_token)])


class PatchStarboardBody(BaseModel):
    channel_id: int | None = None
    threshold: int | None = Field(default=None, ge=1, le=100)
    emoji: str | None = Field(default=None, min_length=1, max_length=64)
    self_star: bool | None = None
    enabled: bool | None = None
    ignored_channel_ids: list[int] | None = None
    max_age_hours: int | None = Field(default=None, ge=0, le=8760)
    allow_nsfw: bool | None = None


def _cog(bot: Bot) -> Starboard:
    cog = bot.get_cog('Starboard')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="the starboard is not loaded")
    return cog  # type: ignore[return-value]


def _serialize(guild: discord.Guild, config) -> dict:
    from app.cogs.starboard.models import DEFAULT_EMOJI, DEFAULT_THRESHOLD

    if config is None:
        return {
            "configured": False, "enabled": False, "channel": None, "threshold": DEFAULT_THRESHOLD,
            "emoji": DEFAULT_EMOJI, "self_star": False, "ignored_channels": [], "max_age_hours": 0, "allow_nsfw": False,
        }
    return {
        "configured": True,
        "enabled": config.enabled,
        "channel": resolve_channel(guild, config.channel_id),
        "threshold": config.threshold,
        "emoji": config.emoji,
        "self_star": config.self_star,
        "ignored_channels": [resolve_channel(guild, cid) for cid in sorted(config.ignored_channel_ids)],
        "max_age_hours": config.max_age_hours,
        "allow_nsfw": config.allow_nsfw,
    }


@router.get("/starboard")
async def get_starboard(guild: GuildDep, bot: BotDep) -> dict:
    """Settings plus the ten most-starred messages (with jump links)."""
    config = await _cog(bot).get_config(guild.id)
    top = []
    for row in await bot.db.starboard.top_entries(guild.id, limit=10):
        author = guild.get_member(row['author_id'])
        top.append({
            "stars": row['star_count'],
            "author": author.display_name if author else None,
            "channel": resolve_channel(guild, row['channel_id']),
            "jump_url": f"https://discord.com/channels/{guild.id}/{row['channel_id']}/{row['message_id']}",
        })
    return {**_serialize(guild, config), "top": top}


@router.patch("/starboard")
async def patch_starboard(guild: GuildDep, bot: BotDep, body: PatchStarboardBody) -> dict:
    cog = _cog(bot)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    if updates.get('channel_id') is not None:
        channel = guild.get_channel(updates['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a text channel for the starboard.")
    if 'ignored_channel_ids' in updates:
        updates['ignored_channel_ids'] = sorted(set(updates['ignored_channel_ids'] or ()))
    if 'emoji' in updates:
        updates['emoji'] = updates['emoji'].strip()

    current = await cog.get_config(guild.id)
    channel_after = updates.get('channel_id', current.channel_id if current else None)
    if updates.get('enabled') and channel_after is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a starboard channel before turning it on.")

    config = await cog.update_config(guild.id, **updates)
    return {"ok": True, **_serialize(guild, config)}
