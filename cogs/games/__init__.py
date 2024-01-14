from bot import Percy
from cogs.games._cog import Games


async def setup(bot: Percy) -> None:
    await bot.add_cog(Games(bot))
