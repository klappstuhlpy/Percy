"""Bearer-token authentication for the internal API."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

import config

#: Public webhook routes are reached by external bot lists, not the BFF, so they are
#: exempt from the internal bearer token and validate their own per-service secret.
WEBHOOK_PREFIX = '/api/webhooks/'


def _check_auth(request: web.Request) -> bool:
    token = request.headers.get('Authorization')
    return token == f'Bearer {config.internal_api_token}'


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if request.path.startswith(WEBHOOK_PREFIX):
        return await handler(request)
    if not _check_auth(request):
        raise web.HTTPUnauthorized(text='invalid or missing token')
    response = await handler(request)
    response.headers['X-API-Version'] = '1'
    return response

