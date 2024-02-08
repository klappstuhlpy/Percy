import discord
import json

from bot import Percy
from launcher import get_logger
from .utils import commands

log = get_logger(__name__)

DISCORD_BOTS_API = 'https://discord.bots.gg/api/v1'


class WebAPIManager(commands.Cog):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.config = bot.config

    async def update(self) -> None:
        """Updates the server count on discord.bots.gg"""
        payload = json.dumps({'guildCount': len(self.bot.guilds)})
        headers = {'authorization': self.bot.config.dbots_key,
                   'content-type': 'application/json'}

        async with self.bot.session.post(
                f'{DISCORD_BOTS_API}/bots/{self.bot.user.id}/stats', data=payload, headers=headers
        ) as resp:
            if resp.status != 200:
                log.warning(f'DBots statistics returned {resp.status} for {payload}')
                return

            log.info(f'DBots statistics returned {resp.status} for {payload}')

    @commands.Cog.listener("on_guild_join")
    @commands.Cog.listener("on_guild_remove")
    async def on_guild_update(self, guild: discord.Guild) -> None:
        await self.update()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.update()


async def setup(bot: Percy):
    await bot.add_cog(WebAPIManager(bot))
