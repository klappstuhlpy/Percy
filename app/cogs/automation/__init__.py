from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import Cog

from .assistant import AssistantMixin

if TYPE_CHECKING:
    from app.core import Bot

__all__ = ('Automation', 'setup')


class Automation(
    AssistantMixin,
    Cog,
    name='Automation',
):
    """The AI assistant, and automod presets.

    Composed from domain mixins (mirroring the ``Misc`` / ``InternalAPI`` pattern) so
    each feature keeps its own module while presenting a single help category.
    """

    emoji = '\N{ROBOT FACE}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Automation(bot))
