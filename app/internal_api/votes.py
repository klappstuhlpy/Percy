"""Inbound vote webhooks from bot lists (top.gg, discordbotlist.com).

Unlike the rest of the internal API these endpoints are reached by external services,
so they are exempt from the internal bearer-token middleware (see :mod:`.auth`) and
instead validate the per-service secret each list sends in the ``Authorization`` header.
A successful vote grants the user a global, renewable XP boost via the votes repository.
"""
from __future__ import annotations

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

    async def _vote_topgg(self, request: web.Request) -> web.Response:
        """top.gg webhook: body ``{"user", "type": "upvote"|"test", ...}``."""
        secret = config.topgg_webhook_secret
        print(secret, request.headers.get('Authorization'))
        if not secret or request.headers.get('Authorization') != secret:
            raise web.HTTPUnauthorized(text='invalid webhook secret')

        try:
            body = await request.json()
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
