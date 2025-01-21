import logging

from aiohttp import ClientConnectorError

from app.cogs.snekbox._cog import Snekbox

log = logging.getLogger(__name__)

# Code obtained from: https://github.com/python-discord/bot/tree/main


async def setup(bot) -> None:
    try:
        async with bot.session.get('https://snekbox.klappstuhl.me/') as resp:
            if resp.status == 502:
                log.warning('Cannot connect to Snekbox API. Failed to load Snekbox cog...')
            else:
                log.info('Successfully connected to Snekbox API.')
                await bot.add_cog(Snekbox(bot))
    except ClientConnectorError:
        log.warning('Cannot connect to Snekbox API. Failed to load Snekbox cog...')
