"""Internal API custom bot profile endpoints — live from Discord, no DB."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Profile"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchCustomBotBody(BaseModel):
    name: str | None = None
    avatar: str | None = None
    banner: str | None = None
    about_me: str | None = None

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/custom-bot")
async def get_custom_bot(bot: BotDep, guild: GuildDep) -> dict:
    """Bot's live Discord profile in the guild (nick, avatar, banner, accent_color)."""
    await bot.wait_until_ready()

    me = guild.me
    bot_user = await bot.fetch_user(bot.user.id)

    return {
        'name': me.nick or (me.display_name if me else None),
        'avatar_url': str(me.display_avatar.url) if me else bot_user.display_avatar.url,
        'banner_url': str(me.banner.url) if me and me.banner else bot_user.banner.url if bot_user.banner else None,
        'accent_color': str(me.accent_color) if me else None,
        'about_me': None,  # not supported by Discord API
    }


@router.patch("/custom-bot")
async def patch_custom_bot(bot: BotDep, guild: GuildDep, body: PatchCustomBotBody) -> dict:
    """Edit the bot's profile in the guild (nickname, avatar, banner, bio)."""
    await bot.wait_until_ready()

    me = guild.me

    # Guild nickname
    if body.name is not None:
        nick = body.name.strip()[:32] or None
        await me.edit(nick=nick)

    # Global avatar (base64-encoded image bytes)
    if body.avatar:
        avatar_bytes = base64.b64decode(body.avatar)
        await me.edit(avatar=avatar_bytes)

    # Global banner (base64-encoded image bytes)
    if body.banner:
        banner_bytes = base64.b64decode(body.banner)
        await me.edit(banner=banner_bytes)

    # About me / bio
    if body.about_me is not None:
        bio = body.about_me.strip()[:190] or None
        await me.edit(bio=bio)

    return {'ok': True}


@router.post("/custom-bot/reset")
async def reset_custom_bot(bot: BotDep, guild: GuildDep) -> dict:
    """Clear all bot profile customizations in the guild."""
    await bot.wait_until_ready()

    me = guild.me
    await me.edit(nick=None, avatar=None, banner=None, bio=None)
    return {'ok': True}
