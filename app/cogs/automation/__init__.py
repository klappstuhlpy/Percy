from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import Cog

from .assistant import AssistantMixin
from .autoresponder import AutoResponderMixin

if TYPE_CHECKING:
    from app.core import Bot

__all__ = ('Automation', 'setup')


class Automation(
    AssistantMixin,
    AutoResponderMixin,
    Cog,
    name='Automation',
):
    """The AI assistant, automod presets, and automatic trigger-phrase replies.

    Composed from domain mixins (mirroring the ``Misc`` / ``InternalAPI`` pattern) so
    each feature keeps its own module while presenting a single help category.
    """

    emoji = '\N{ROBOT FACE}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Automation(bot))
