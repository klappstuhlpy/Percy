"""User-scoped endpoints (not guild-scoped) -- settings, history, data export."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import zoneinfo
from typing import TYPE_CHECKING, Any

import discord
import wavelink
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.rendering.card_style import clean_style, resolve_style, style_options
from app.services.lyrics import display_title

from ..dependencies import BotDep, verify_token
from .music import serialize_play

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
            # The music app's server picker marks where Percy has a track loaded (paused counts).
            "music_playing": isinstance(guild.voice_client, wavelink.Player) and guild.voice_client.current is not None,
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
    avatar_limit: int = Query(default=24, ge=1, le=50),
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
# A burst (the page's card plus the editor's preview) queues for its slot instead of failing;
# only a backlog longer than this is refused.
_PREVIEW_MAX_WAIT = 2.0
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
    slot = max(now, _last_preview.get(discord_id, 0.0) + _PREVIEW_INTERVAL)
    if slot - now > _PREVIEW_MAX_WAIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="previewing too fast")
    _last_preview[discord_id] = slot
    if slot > now:
        await asyncio.sleep(slot - now)

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


# ---------------------------------------------------------------------------
# Music library (top tracks, recents, liked songs, playlists)
# ---------------------------------------------------------------------------

LIKED = "Liked Songs"
PLAYLIST_NAME_MAX = 100
PLAYLIST_MAX_TRACKS = 500


class TrackBody(BaseModel):
    name: str
    url: str
    author: str | None = None
    artwork: str | None = None


class PlaylistCreateBody(BaseModel):
    name: str


class PlaylistPatchBody(BaseModel):
    name: str | None = None
    # "" clears the custom cover; omitted leaves it alone.
    thumbnail: str | None = None


_YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)|youtu\.be/)([\w-]{11})")


def _artwork(url: str, played: dict[str, str]) -> str | None:
    """A cover for a saved track that has none stored: its last play's, else YouTube's thumbnail.

    Rows saved before V61 carry only name + url, so their cover is recovered rather than kept.
    """
    # ponytail: music_plays is pruned after 90 days, so an old non-YouTube row can stay coverless.
    if url in played:
        return played[url]
    m = _YOUTUBE_ID.search(url)
    return f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else None


def _http_or_none(value: str | None, field: str) -> str | None:
    """Only http(s) links reach the browser as ``<img src>``; anything else is a 400."""
    if not value or not value.strip():
        return None
    value = value.strip()
    if len(value) > 2000 or not value.lower().startswith(("https://", "http://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field} must be an http(s) link")
    return value


def _clean_track(body: TrackBody) -> tuple[str, str, str | None, str | None]:
    url = _http_or_none(body.url, "url")
    if url is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url is required")
    name = body.name.strip()[:200] or url
    author = (body.author or "").strip()[:200] or None
    return name, url, author, _http_or_none(body.artwork, "artwork")


async def _tracks_payload(bot: Any, rows: list[Any]) -> list[dict]:
    """Saved tracks with artist and cover, recovering covers for rows saved before V61."""
    missing = [t["url"] for t in rows if t["url"] and not t["artwork"]]
    played = await bot.db.music_plays.artwork_for(missing) if missing else {}
    return [
        {
            "name": display_title(t["name"], t["author"]),
            "url": t["url"],
            "author": t["author"],
            "artwork": t["artwork"] or _artwork(t["url"] or "", played),
        }
        for t in rows
    ]


async def _own_playlist(bot: Any, discord_id: int, playlist_id: int) -> Any:
    row = await bot.db.playlists.get_playlist_by_id(playlist_id)
    if row is None or row["user_id"] != discord_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="playlist not found")
    return row


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if len(name) > PLAYLIST_NAME_MAX:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"name is longer than {PLAYLIST_NAME_MAX}")
    if name.casefold() == LIKED.casefold():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'"{LIKED}" is reserved')
    return name


async def _check_name_free(bot: Any, discord_id: int, name: str, *, keep: int | None = None) -> None:
    row = await bot.db.playlists.get_playlist_by_name(discord_id, name)
    if row is not None and row["id"] != keep:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'you already have a playlist called "{name}"')


def _invalidate_playlists(bot: Any, user_id: int) -> None:
    """The Music cog memoizes ``get_playlists``; drop it so Discord sees dashboard writes."""
    cog: Any = bot.get_cog("Music")
    if cog is not None:
        cog.get_playlists.invalidate(user_id)


@router.get("/music")
async def get_user_music(discord_id: int, bot: BotDep) -> dict:
    plays = bot.db.music_plays
    top = await plays.user_top_tracks(discord_id)
    recent = await plays.user_recent(discord_id)
    liked: list[dict] = []
    playlists: list[dict] = []
    for p in await bot.db.playlists.get_user_playlists(discord_id):
        tracks = await bot.db.playlists.get_playlist_tracks(p["id"])
        if p["name"] == LIKED:
            liked = await _tracks_payload(bot, tracks)
        else:
            # A playlist without a custom cover shows its first saved song's.
            first = await _tracks_payload(bot, tracks[:1])
            playlists.append({
                "id": str(p["id"]),
                "name": p["name"],
                "track_count": len(tracks),
                "thumbnail": p["thumbnail"],
                "cover": p["thumbnail"] or (first[0]["artwork"] if first else None),
            })
    return {
        "top_tracks": [
            {**{k: r[k] for k in ("author", "uri", "artwork", "length_ms", "plays")},
             "title": display_title(r["title"], r["author"])}
            for r in top
        ],
        "recent": [serialize_play(r) for r in recent],
        "liked": liked,
        "playlists": playlists,
    }


@router.post("/music/liked")
async def like_track(discord_id: int, body: TrackBody, bot: BotDep) -> dict:
    name, url, author, artwork = _clean_track(body)
    repo = bot.db.playlists
    row = await repo.get_liked_songs(discord_id)
    if row is None:
        pid = await repo.create_playlist(discord_id, LIKED, discord.utils.utcnow())
    else:
        pid = row["id"]
    if not any(t["url"] == url for t in await repo.get_playlist_tracks(pid)):
        await repo.add_track(pid, name, url, author=author, artwork=artwork)
    _invalidate_playlists(bot, discord_id)
    return {"liked": True}


@router.delete("/music/liked")
async def unlike_track(discord_id: int, bot: BotDep, url: str = Query(...)) -> dict:
    row = await bot.db.playlists.get_liked_songs(discord_id)
    if row is not None:
        await bot.db.playlists.remove_track_by_url(row["id"], url)
        _invalidate_playlists(bot, discord_id)
    return {"liked": False}


@router.post("/music/playlists", status_code=status.HTTP_201_CREATED)
async def create_user_playlist(discord_id: int, body: PlaylistCreateBody, bot: BotDep) -> dict:
    name = _clean_name(body.name)
    await _check_name_free(bot, discord_id, name)
    pid = await bot.db.playlists.create_playlist(discord_id, name, discord.utils.utcnow())
    _invalidate_playlists(bot, discord_id)
    return {"id": str(pid), "name": name, "track_count": 0, "thumbnail": None, "cover": None}


@router.get("/music/playlists/{playlist_id}")
async def get_user_playlist(discord_id: int, playlist_id: int, bot: BotDep) -> dict:
    row = await _own_playlist(bot, discord_id, playlist_id)
    tracks = await _tracks_payload(bot, await bot.db.playlists.get_playlist_tracks(playlist_id))
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "thumbnail": row["thumbnail"],
        "cover": row["thumbnail"] or (tracks[0]["artwork"] if tracks else None),
        "tracks": tracks,
    }


@router.patch("/music/playlists/{playlist_id}")
async def patch_user_playlist(discord_id: int, playlist_id: int, body: PlaylistPatchBody, bot: BotDep) -> dict:
    row = await _own_playlist(bot, discord_id, playlist_id)
    repo = bot.db.playlists
    if body.name is not None:
        if row["name"] == LIKED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'"{LIKED}" cannot be renamed')
        name = _clean_name(body.name)
        await _check_name_free(bot, discord_id, name, keep=playlist_id)
        await repo.rename_playlist(playlist_id, name)
    if body.thumbnail is not None:
        await repo.set_playlist_thumbnail(playlist_id, _http_or_none(body.thumbnail, "thumbnail"))
    _invalidate_playlists(bot, discord_id)
    return await get_user_playlist(discord_id, playlist_id, bot)


@router.delete("/music/playlists/{playlist_id}")
async def delete_user_playlist(discord_id: int, playlist_id: int, bot: BotDep) -> dict:
    row = await _own_playlist(bot, discord_id, playlist_id)
    if row["name"] == LIKED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'"{LIKED}" cannot be deleted')
    await bot.db.playlists.delete_playlist(playlist_id)
    _invalidate_playlists(bot, discord_id)
    return {"deleted": True}


@router.post("/music/playlists/{playlist_id}/tracks")
async def add_user_playlist_track(discord_id: int, playlist_id: int, body: TrackBody, bot: BotDep) -> dict:
    await _own_playlist(bot, discord_id, playlist_id)
    name, url, author, artwork = _clean_track(body)
    repo = bot.db.playlists
    tracks = await repo.get_playlist_tracks(playlist_id)
    if any(t["url"] == url for t in tracks):
        return {"added": False, "track_count": len(tracks)}
    if len(tracks) >= PLAYLIST_MAX_TRACKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"a playlist holds at most {PLAYLIST_MAX_TRACKS} songs"
        )
    await repo.add_track(playlist_id, name, url, author=author, artwork=artwork)
    _invalidate_playlists(bot, discord_id)
    return {"added": True, "track_count": len(tracks) + 1}


@router.delete("/music/playlists/{playlist_id}/tracks")
async def remove_user_playlist_track(discord_id: int, playlist_id: int, bot: BotDep, url: str = Query(...)) -> dict:
    await _own_playlist(bot, discord_id, playlist_id)
    await bot.db.playlists.remove_track_by_url(playlist_id, url)
    _invalidate_playlists(bot, discord_id)
    return {"removed": True}
