from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import Cog

from .dstatus import DiscordStatusMixin
from .gimmicks import GimmicksMixin
from .meta import MetaMixin
from .stat_counters import StatCountersMixin
from .temp_channels import TempChannelsMixin
from .tools import KlappstuhlToolsMixin
from .translator import TranslatorMixin

if TYPE_CHECKING:
    from app.core import Bot

__all__ = ('Misc', 'setup')


class Misc(
    GimmicksMixin,
    KlappstuhlToolsMixin,
    TranslatorMixin,
    DiscordStatusMixin,
    StatCountersMixin,
    TempChannelsMixin,
    MetaMixin,
    Cog,
    name='Misc',
):
    """Utility, info, and fun commands that don't belong to a dedicated feature.

    Composed from domain mixins (mirroring the ``InternalAPI`` pattern) so each area's
    code stays in its own module while presenting a single help category. discord.py's
    ``CogMeta`` collects commands and listeners across the full MRO, so every mixin's
    commands/listeners land on this one cog.
    """

    emoji = '<a:staff_animated:1322337965774602313>'

    def __init__(self, bot: Bot) -> None:
        # Kicks off the cooperative ``__init__`` chain: each mixin calls
        # ``super().__init__(bot)``, threading ``bot`` down to ``Cog.__init__``.
        super().__init__(bot)

    async def cog_load(self) -> None:
        await self._setup_status()

    async def cog_unload(self) -> None:
        await self._teardown_status()
        await self._teardown_counters()
        await self._teardown_meta()


async def setup(bot: Bot) -> None:
    await bot.add_cog(Misc(bot))
