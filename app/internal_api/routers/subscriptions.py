"""Internal API endpoints for outbound webhooks (event subscriptions).

A guild registers a URL and a set of events; Percy POSTs a signed JSON envelope there when
a matching event fires (delivery + event listening live in the ``Webhooks`` cog). The secret
used to sign deliveries is generated server-side and returned exactly once, on creation.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.services import WEBHOOK_EVENTS, valid_events

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(prefix="/guilds/{guild_id}/webhooks", tags=["Webhooks (Outgoing)"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSubscriptionBody(BaseModel):
    url: str
    events: list[str]
    label: str | None = None


class PatchSubscriptionBody(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    label: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url must be an http(s) URL")
    if len(url) > 2000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url is too long")
    return url


def _serialize(record, *, reveal_secret: bool = False) -> dict:
    """Shape a subscription row for the API. The secret is masked unless just created."""
    return {
        "id": record["id"],
        "url": record["url"],
        "events": list(record["events"]),
        "label": record["label"],
        "enabled": record["enabled"],
        "secret": record["secret"] if reveal_secret else None,
        "created_at": record["created_at"].isoformat() if record["created_at"] else None,
        "last_delivery_at": record["last_delivery_at"].isoformat() if record["last_delivery_at"] else None,
        "last_status": record["last_status"],
        "failure_count": record["failure_count"],
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/events")
async def list_events() -> dict:
    """The catalogue of events a subscription can listen for."""
    return {"events": sorted(WEBHOOK_EVENTS)}


@router.get("")
async def list_subscriptions(guild: GuildDep, bot: BotDep) -> dict:
    """Every outbound webhook configured for the guild (secrets masked)."""
    records = await bot.db.event_webhooks.list_for_guild(guild.id)
    return {"subscriptions": [_serialize(r) for r in records]}


@router.post("")
async def create_subscription(guild: GuildDep, bot: BotDep, body: CreateSubscriptionBody) -> dict:
    """Register a new outbound webhook. Returns the signing secret once — store it now."""
    url = _validate_url(body.url)
    events = valid_events(body.events)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"no valid events; expected some of {sorted(WEBHOOK_EVENTS)}",
        )
    label = body.label.strip() if body.label else None
    secret = secrets.token_hex(32)

    record = await bot.db.event_webhooks.create(guild.id, url, secret, events, label)
    return {"ok": True, "subscription": _serialize(record, reveal_secret=True)}


@router.patch("/{sub_id}")
async def patch_subscription(guild: GuildDep, bot: BotDep, sub_id: int, body: PatchSubscriptionBody) -> dict:
    """Update a subscription's url, events, label, or enabled state."""
    existing = await bot.db.event_webhooks.get(sub_id, guild.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    updates: dict[str, object] = {}
    if body.url is not None:
        updates["url"] = _validate_url(body.url)
    if body.events is not None:
        events = valid_events(body.events)
        if not events:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid events supplied")
        updates["events"] = events
    if body.label is not None:
        updates["label"] = body.label.strip() or None
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no fields to update")

    record = await bot.db.event_webhooks.update(sub_id, guild.id, updates)
    return {"ok": True, "subscription": _serialize(record)}


@router.delete("/{sub_id}")
async def delete_subscription(guild: GuildDep, bot: BotDep, sub_id: int) -> dict:
    """Delete a subscription and its delivery log."""
    existing = await bot.db.event_webhooks.get(sub_id, guild.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")
    await bot.db.event_webhooks.delete(sub_id, guild.id)
    return {"ok": True}


@router.post("/{sub_id}/test")
async def test_subscription(guild: GuildDep, bot: BotDep, sub_id: int) -> dict:
    """Send a sample ``ping`` payload to the endpoint and report the result synchronously."""
    record = await bot.db.event_webhooks.get(sub_id, guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    cog = bot.get_cog("Webhooks")
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="webhook dispatcher not loaded")

    result = await cog.deliver_test(record)
    return {"ok": result["success"], "result": result}


@router.get("/{sub_id}/deliveries")
async def get_deliveries(
    guild: GuildDep,
    bot: BotDep,
    sub_id: int,
    limit: int = Query(default=25, le=100),
) -> dict:
    """Recent delivery attempts for a subscription (its health log)."""
    record = await bot.db.event_webhooks.get(sub_id, guild.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription not found")

    rows = await bot.db.event_webhooks.get_deliveries(sub_id, limit=limit)
    return {
        "deliveries": [
            {
                "event": r["event"],
                "success": r["success"],
                "status_code": r["status_code"],
                "attempts": r["attempts"],
                "error": r["error"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }
