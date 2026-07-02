from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord

from config import Emojis

if TYPE_CHECKING:
    from app.core.context import Context

__all__ = ("ProgressTracker",)


class ProgressTracker:
    """Provides intermediate status updates during long-running commands.

    Usage::

        async with ctx.progress("Fetching data...") as progress:
            data = await fetch_page_1()
            await progress.update("Processing page 1/5...")
            ...
            await progress.update("Processing page 5/5...")

    For interactions the message is edited in place; for text commands
    the bot shows typing and edits a status message.
    """

    __slots__ = ("_ctx", "_done", "_ephemeral", "_message", "_status")

    def __init__(self, ctx: Context, initial_status: str, *, ephemeral: bool = False) -> None:
        self._ctx = ctx
        self._status = initial_status
        self._ephemeral = ephemeral
        self._message: discord.Message | None = None
        self._done = False

    async def __aenter__(self) -> ProgressTracker:
        content = f"{Emojis.loading} {self._status}"
        if self._ctx.is_interaction and self._ctx.interaction and not self._ctx.interaction.response.is_done():
            await self._ctx.interaction.response.defer(ephemeral=self._ephemeral)
            self._message = await self._ctx.interaction.followup.send(content, ephemeral=self._ephemeral, wait=True)
        else:
            self._message = await self._ctx.send(content)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._done = True
        if exc_type is not None:
            with suppress(discord.HTTPException):
                if self._message:
                    await self._message.delete()
            return

        with suppress(discord.HTTPException):
            if self._message:
                await self._message.delete()

    async def update(self, status: str) -> None:
        """Update the progress message with a new status string."""
        if self._done:
            return
        self._status = status
        content = f"{Emojis.loading} {self._status}"
        with suppress(discord.HTTPException):
            if self._message:
                await self._message.edit(content=content)

    async def tick(self, current: int, total: int, label: str = "Processing") -> None:
        """Convenience for updating with a fraction, e.g. 'Processing 3/10...'"""
        await self.update(f"{label} {current}/{total}...")
