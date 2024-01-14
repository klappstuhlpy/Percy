from cogs.anisearch._cog import AniListSearch
from bot import Percy


async def setup(bot: Percy) -> None:
    await bot.add_cog(AniListSearch(bot))
