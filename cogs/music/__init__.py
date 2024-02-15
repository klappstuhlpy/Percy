from bot import Percy
from cogs.music._music import Music
from cogs.music._playlist import PlaylistTools


async def setup(bot: Percy) -> None:
    await bot.add_cog(Music(bot))
    await bot.add_cog(PlaylistTools(bot))
