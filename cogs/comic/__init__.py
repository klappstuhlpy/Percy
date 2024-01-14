from bot import Percy
from cogs.comic._cog import ComicPulls


async def setup(bot: Percy):
    await bot.add_cog(ComicPulls(bot))
