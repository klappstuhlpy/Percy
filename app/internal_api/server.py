"""FastAPI server lifecycle for the internal API."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

import config

if TYPE_CHECKING:
    from app.core import Bot

log = logging.getLogger(__name__)

__all__ = ('InternalAPI',)

API_VERSION = '1'

SCALAR_CDN = 'https://cdn.jsdelivr.net/npm/@scalar/api-reference'

SCALAR_HTML = f"""<!doctype html>
<html>
<head>
    <title>Percy Internal API</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
</head>
<body>
    <script id="api-reference" data-url="/openapi.json" data-configuration='{{"theme":"kepler"}}'></script>
    <script src="{SCALAR_CDN}"></script>
</body>
</html>
"""


def _create_app(bot: Bot) -> FastAPI:
    app = FastAPI(
        title='Percy Internal API',
        description='Internal API for the Percy Discord bot, consumed by the klappstuhl.me BFF dashboard.',
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
    )
    app.state.bot = bot

    @app.get('/docs', include_in_schema=False)
    async def scalar_docs() -> HTMLResponse:
        return HTMLResponse(SCALAR_HTML)

    @app.middleware('http')
    async def add_api_version_header(request, call_next):
        response: Response = await call_next(request)
        response.headers['X-API-Version'] = API_VERSION
        return response

    from .routers import ALL_ROUTERS

    prefix = '/api/v1'
    for router in ALL_ROUTERS:
        if router.prefix and router.prefix.startswith('/api/webhooks'):
            app.include_router(router)
        else:
            app.include_router(router, prefix=prefix)

    return app


class InternalAPI:
    """Manages the internal HTTP API server lifecycle."""

    __hidden__ = True

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not config.internal_api_token:
            log.warning('Internal API disabled (INTERNAL_API_TOKEN not set)')
            return

        app = _create_app(self.bot)

        uv_config = uvicorn.Config(
            app,
            host=config.internal_api_host,
            port=config.internal_api_port,
            log_level='warning',
            access_log=False,
        )
        self._server = uvicorn.Server(uv_config)
        self._task = asyncio.create_task(self._server.serve())
        log.info('Internal API listening on %s:%d', config.internal_api_host, config.internal_api_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task
