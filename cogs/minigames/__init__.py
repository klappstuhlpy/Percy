from bot import Percy
from cogs.minigames._cog import Minigame


async def setup(bot: Percy) -> None:
    await bot.add_cog(Minigame(bot))
