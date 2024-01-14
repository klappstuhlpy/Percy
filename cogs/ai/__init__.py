from bot import Percy
from cogs.ai._cog import AITools


async def setup(bot: Percy) -> None:
    await bot.add_cog(AITools(bot))
