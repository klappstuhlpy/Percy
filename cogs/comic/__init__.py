from bot import Percy
from cogs.comic._cog import Comics


async def setup(bot: Percy):
    await bot.add_cog(Comics(bot))
