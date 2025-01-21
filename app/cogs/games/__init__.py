from app.cogs.games._cog import Games
from app.core import Bot


async def setup(bot: Bot) -> None:
    await bot.add_cog(Games(bot))
