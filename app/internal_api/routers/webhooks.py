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
import time

from fastapi import APIRouter, HTTPException, Request, status

import config

from ..dependencies import BotDep  # noqa: TC001 -- FastAPI evaluates handler param annotations at runtime

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])

#: XP multiplier and window granted (or renewed) per vote.
VOTE_MULTIPLIER = 1.10
VOTE_DURATION_HOURS = 12
#: How old a signed top.gg delivery may be before it is refused as a replay.
SIGNATURE_TOLERANCE_SECONDS = 300


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
    # Without a freshness check a captured delivery could be replayed forever.
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if sent > 10**11:  # tolerate a millisecond timestamp rather than refusing every vote
        sent //= 1000
    if abs(time.time() - sent) > SIGNATURE_TOLERANCE_SECONDS:
        return False

    # Sign the raw bytes: decoding first would turn a non-UTF-8 body into a 500.
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def _secret_matches(secret: str, header: str | None) -> bool:
    """Constant-time comparison of a shared-secret ``Authorization`` header."""
    return header is not None and hmac.compare_digest(header.encode(), secret.encode())


def _user_id(value: object) -> int:
    """A vote's Discord user id; anything else is a 400, not a 500 from ``int()``."""
    try:
        user_id = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        user_id = 0
    if isinstance(value, bool) or not 0 < user_id < 2**63:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing or invalid user id")
    return user_id


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
    elif not _secret_matches(secret, request.headers.get("authorization")):  # legacy v0 fallback
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON body must be an object")

    # v1 wraps the vote in an envelope ({"type": "vote.create", "data": {...}}); the
    # user's Discord id is data.user.platform_id (data.user.id is the top.gg id).
    # v0 is flat ({"user": "<discord id>", "type": "upvote"|"test"}).
    data = body.get("data")
    if isinstance(data, dict):
        event_type = body.get("type", "")
        if event_type != "vote.create":
            log.info("Received top.gg v1 webhook event %r (ignored)", event_type)
            return {"ok": True}
        user = data.get("user")
        user_id = user.get("platform_id") if isinstance(user, dict) else None
    else:
        # top.gg sends {"type": "test"} when you press "Test" in the v0 dashboard.
        if body.get("type") == "test":
            log.info("Received top.gg webhook test ping")
            return {"ok": True}
        user_id = body.get("user")

    await bot.db.votes.record_vote(_user_id(user_id), "top.gg", multiplier=VOTE_MULTIPLIER, duration_hours=VOTE_DURATION_HOURS)
    log.info("Recorded top.gg vote from user %s", user_id)
    return {"ok": True}


@router.post("/discordbotlist")
async def vote_discordbotlist(request: Request, bot: BotDep) -> dict:
    """discordbotlist.com webhook: body ``{"id", "username", "admin", ...}``."""
    secret = config.discordbotlist_webhook_secret
    if not secret or not _secret_matches(secret, request.headers.get("authorization")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON body must be an object")

    user_id = _user_id(body.get("id"))
    await bot.db.votes.record_vote(
        user_id, "discordbotlist.com", multiplier=VOTE_MULTIPLIER, duration_hours=VOTE_DURATION_HOURS
    )
    log.info("Recorded discordbotlist.com vote from user %s", user_id)
    return {"ok": True}
