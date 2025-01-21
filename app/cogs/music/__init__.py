import logging

import wavelink

from app.cogs.music._music import Music
from app.cogs.music._player import Player
from app.cogs.music._playlist import PlaylistTools

log = logging.getLogger(__name__)


async def setup(bot) -> None:
    try:
        wavelink.Pool.get_node()
    except wavelink.InvalidNodeException:
        log.warning('Music Cog not being initialized as no nodes are available.')
        return

    await bot.add_cog(Music(bot))
    await bot.add_cog(PlaylistTools(bot))
