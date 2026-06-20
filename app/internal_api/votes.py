"""Inbound vote webhooks from bot lists (top.gg, discordbotlist.com).

Unlike the rest of the internal API these endpoints are reached by external services,
so they are exempt from the internal bearer-token middleware (see :mod:`.auth`) and
instead validate the per-service secret each list sends in the ``Authorization`` header.
A successful vote grants the user a global, renewable XP boost via the votes repository.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from aiohttp import web

import config

from .models import InternalAPIHandlers

log = logging.getLogger(__name__)

#: XP multiplier and window granted (or renewed) per vote.
VOTE_MULTIPLIER = 1.10
VOTE_DURATION_HOURS = 12


class VoteHandlers(InternalAPIHandlers):
    """Webhook receivers that turn bot-list upvotes into renewable XP boosts."""

    async def _grant_vote(self, user_id: int, source: str) -> None:
        expires = await self.bot.db.votes.record_vote(
            user_id, source, multiplier=VOTE_MULTIPLIER, duration_hours=VOTE_DURATION_HOURS
        )
        log.info('Recorded %s vote from user %d (XP boost until %s UTC)', source, user_id, expires)

    @staticmethod
    def _verify_topgg_signature(secret: str, raw_body: bytes, signature: str) -> bool:
        """Validate a top.gg v1 ``x-topgg-signature`` HMAC header.

        The header is ``t={unix ts},v1={hex hmac}``; the signed message is
        ``{timestamp}.{rawBody}`` keyed with the webhook secret (SHA-256).
        """
        parts = dict(p.split('=', 1) for p in signature.split(',') if '=' in p)
        timestamp, received = parts.get('t'), parts.get('v1')
        if not timestamp or not received:
            return False

        expected = hmac.new(
            secret.encode(), f'{timestamp}.{raw_body.decode("utf-8")}'.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, received)

    async def _vote_topgg(self, request: web.Request) -> web.Response:
        """top.gg webhook: body ``{"user", "type": "upvote"|"test", ...}``.

        Supports v1 (``x-topgg-signature`` HMAC verification) and falls back to the
        legacy v0 scheme (raw secret in the ``Authorization`` header). A failed check
        returns 4xx (top.gg does not retry those); processing errors surface as 5xx so
        delivery is retried.
        """
        secret = config.topgg_webhook_secret
        if not secret:
            raise web.HTTPUnauthorized(text='webhook secret not configured')

        # Read the raw body once: v1 signing is computed over the exact bytes.
        raw_body = await request.read()
        signature = request.headers.get('x-topgg-signature')
        if signature is not None:
            if not self._verify_topgg_signature(secret, raw_body, signature):
                raise web.HTTPUnauthorized(text='invalid webhook signature')
        elif request.headers.get('Authorization') != secret:  # legacy v0 fallback
            raise web.HTTPUnauthorized(text='invalid webhook secret')

        try:
            body = json.loads(raw_body)
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        # top.gg sends {"type": "test"} when you press "Test" in the webhook dashboard.
        if body.get('type') == 'test':
            log.info('Received top.gg webhook test ping')
            return web.json_response({'ok': True})

        user_id = body.get('user')
        if not user_id:
            raise web.HTTPBadRequest(text='missing user')

        await self._grant_vote(int(user_id), 'top.gg')
        return web.json_response({'ok': True})

    async def _vote_discordbotlist(self, request: web.Request) -> web.Response:
        """discordbotlist.com webhook: body ``{"id", "username", "admin", ...}``."""
        secret = config.discordbotlist_webhook_secret
        if not secret or request.headers.get('Authorization') != secret:
            raise web.HTTPUnauthorized(text='invalid webhook secret')

        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        user_id = body.get('id')
        if not user_id:
            raise web.HTTPBadRequest(text='missing id')

        await self._grant_vote(int(user_id), 'discordbotlist.com')
        return web.json_response({'ok': True})
