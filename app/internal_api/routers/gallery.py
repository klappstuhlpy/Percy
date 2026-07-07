"""Guild image gallery endpoints.

Thin proxy over the klappstuhl.me guild-scoped image API (``/guilds/{id}/images``).
The dashboard reaches these so a server owner can view / upload / delete the same
per-guild gallery that Percy uploads poll banners into. The client provisions a
narrow, per-guild ``images:guild`` key from the host on demand (Percy never needs
a personal key); nothing here touches Percy's own database, so there is no cache
to invalidate.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

import fastapi
from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import BotDep, GuildDep, verify_token

if TYPE_CHECKING:
    from app.services.klappstuhl_me import KlappstuhlMeClient

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Gallery"], dependencies=[Depends(verify_token)])


def _client_or_503(bot) -> KlappstuhlMeClient:
    """Return the configured klappstuhl.me client, or 503 when the hoster is off."""
    client = getattr(bot, "klappstuhlme_client", None)
    if client is None or not client.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the image hoster is not configured "
            "(set KLAPPSTUHL_ME_PROVISION_TOKEN, or the legacy KLAPPSTUHL_ME_API_TOKEN)",
        )
    return client


@router.get("/gallery")
async def list_gallery(guild: GuildDep, bot: BotDep) -> dict:
    """List the guild's image gallery (newest first, expired images omitted)."""
    client = _client_or_503(bot)
    try:
        return asdict(await client.list_guild_images(guild.id))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"gallery unavailable: {exc}")


@router.post("/gallery")
async def upload_gallery(
    guild: GuildDep,
    bot: BotDep,
    files: list[fastapi.UploadFile] = fastapi.File(...),
) -> dict:
    """Upload one or more images into the guild's gallery."""
    client = _client_or_503(bot)

    payload: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        if content:
            payload.append((upload.filename or "image.png", content))

    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided")

    try:
        result = await client.upload_guild_images(guild.id, *payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"upload failed: {exc}")

    if result.errors or result.infected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="one or more images were rejected (unsupported type, too large, or failed a malware scan)",
        )
    return asdict(result)


@router.delete("/gallery/{image_id}")
async def delete_gallery_image(guild: GuildDep, bot: BotDep, image_id: str) -> dict:
    """Delete an image from the guild's gallery."""
    client = _client_or_503(bot)
    try:
        return asdict(await client.delete_guild_image(guild.id, image_id))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"delete failed: {exc}")
