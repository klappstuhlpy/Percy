"""Notifications cog package; lazy so importing ``decide`` does not load ``cog``/``ui`` (see moderation)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.cogs.notifications.cog import Notifications
    from app.core import Bot

__all__ = (
    'Notifications',
    'setup',
)


def __getattr__(name: str) -> Any:
    if name == 'Notifications':
        from app.cogs.notifications.cog import Notifications

        return Notifications
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


async def setup(bot: Bot) -> None:
    from app.cogs.notifications.cog import setup as _setup

    await _setup(bot)
