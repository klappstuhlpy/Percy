"""Inbound vote webhooks from bot lists (top.gg, discordbotlist.com).

Unlike the rest of the internal API these endpoints are reached by external services,
so the router has NO auth dependency -- each handler validates its own per-service secret.
A successful vote grants the user a global, renewable XP boost via the votes repository.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request, status

import config

from ..dependencies import BotDep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])

#: XP multiplier and window granted (or renewed) per vote.
VOTE_MULTIPLIER = 1.10
VOTE_DURATION_HOURS = 12


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_topgg_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """Validate a top.gg v1 ``x-topgg-signature`` HMAC header.

    The header is ``t={unix ts},v1={hex hmac}``; the signed message is
    ``{timestamp}.{rawBody}`` keyed with the webhook secret (SHA-256).
    """
    parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
    timestamp, received = parts.get("t"), parts.get("v1")
    if not timestamp or not received:
        return False

    expected = hmac.new(
        secret.encode(), f"{timestamp}.{raw_body.decode('utf-8')}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/topgg")
async def vote_topgg(request: Request, bot: BotDep) -> dict:
    """top.gg webhook: supports v1 HMAC signature and v0 legacy header auth."""
    secret = config.topgg_webhook_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="webhook secret not configured")

    # Read the raw body once: v1 signing is computed over the exact bytes.
    raw_body = await request.body()
    signature = request.headers.get("x-topgg-signature")

    if signature is not None:
        if not _verify_topgg_signature(secret, raw_body, signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook signature")
    elif request.headers.get("authorization") != secret:  # legacy v0 fallback
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body")

    # v1 wraps the vote in an envelope ({"type": "vote.create", "data": {...}}); the
    # user's Discord id is data.user.platform_id (data.user.id is the top.gg id).
    # v0 is flat ({"user": "<discord id>", "type": "upvote"|"test"}).
    data = body.get("data")
    if isinstance(data, dict):
        event_type = body.get("type", "")
        if event_type != "vote.create":
            log.info("Received top.gg v1 webhook event %r (ignored)", event_type)
            return {"ok": True}
        user_id = (data.get("user") or {}).get("platform_id")
    else:
        # top.gg sends {"type": "test"} when you press "Test" in the v0 dashboard.
        if body.get("type") == "test":
            log.info("Received top.gg webhook test ping")
            return {"ok": True}
        user_id = body.get("user")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing user")

    await bot.db.votes.record_vote(int(user_id), "top.gg", multiplier=VOTE_MULTIPLIER, duration_hours=VOTE_DURATION_HOURS)
    log.info("Recorded top.gg vote from user %s", user_id)
    return {"ok": True}


@router.post("/discordbotlist")
async def vote_discordbotlist(request: Request, bot: BotDep) -> dict:
    """discordbotlist.com webhook: body ``{"id", "username", "admin", ...}``."""
    secret = config.discordbotlist_webhook_secret
    if not secret or request.headers.get("authorization") != secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body")

    user_id = body.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing id")

    await bot.db.votes.record_vote(
        int(user_id), "discordbotlist.com", multiplier=VOTE_MULTIPLIER, duration_hours=VOTE_DURATION_HOURS
    )
    log.info("Recorded discordbotlist.com vote from user %s", user_id)
    return {"ok": True}
