"""Bearer-token authentication for the internal API."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

import config


def _check_auth(request: web.Request) -> bool:
    token = request.headers.get('Authorization')
    return token == f'Bearer {config.internal_api_token}'


@web.middleware
async def auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if not _check_auth(request):
        raise web.HTTPUnauthorized(text='invalid or missing token')
    return await handler(request)

