from bot import Percy
from cogs.snekbox._cog import Snekbox

# Code obtained from: https://github.com/python-discord/bot/tree/main


async def setup(bot: Percy) -> None:
    await bot.add_cog(Snekbox(bot))
