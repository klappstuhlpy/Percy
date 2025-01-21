import json
import logging

from app.core import Cog
from config import dbots_key, top_gg_key

log = logging.getLogger(__name__)

DISCORD_BOTS_API = 'https://discord.bots.gg/api/v1'
TOP_GG_API = 'https://top.gg/api/'


class WebUtils(Cog):
    """This cog is responsible for updating the bot's statistics on discord.bots.gg"""
    __hidden__ = True

    # https://discord.bots.gg/:

    async def update_dbots(self) -> None:
        """Updates the server count on discord.bots.gg"""
        payload = json.dumps({'guildCount': len(self.bot.guilds)})
        headers = {'authorization': dbots_key,
                   'content-type': 'application/json'}

        async with self.bot.session.post(
                f'{DISCORD_BOTS_API}/bots/{self.bot.user.id}/stats', data=payload, headers=headers
        ) as resp:
            if resp.status != 200:
                log.warning('DBots statistics returned %d for %s', resp.status, payload)
                return

            log.info('DBots statistics returned %d for %s', resp.status, payload)

    @Cog.listener("on_guild_join")
    @Cog.listener("on_guild_remove")
    async def on_guild_update(self, _) -> None:
        await self.update_dbots()
        await self.update_top_gg()

    @Cog.listener()
    async def on_ready(self) -> None:
        await self.update_dbots()
        await self.update_top_gg()

    # https://top.gg/:

    async def update_top_gg(self) -> None:
        """Updates the server count on top.gg"""
        payload = json.dumps({'server_count': len(self.bot.guilds)})
        headers = {'authorization': top_gg_key,
                   'content-type': 'application/json'}

        async with self.bot.session.post(
                f'{TOP_GG_API}bots/{self.bot.user.id}/stats', data=payload, headers=headers
        ) as resp:
            if resp.status != 200:
                log.warning('Top.gg statistics returned %d for %s', resp.status, payload)
                return

            log.info('Top.gg statistics returned %d for %s', resp.status, payload)

    async def check_user_voted(self, user_id: int, /) -> bool:
        payload = {'userId': user_id}
        async with self.bot.session.get(
            f'{TOP_GG_API}bots/{self.bot.user.id}/check', headers={'Authorization': top_gg_key}, params=payload
        ) as resp:
            if resp.status != 200:
                log.warning('Top.gg check returned %d for %s', resp.status, payload)
                return False
            return (await resp.json())['voted'] != 0


async def setup(bot) -> None:
    await bot.add_cog(WebUtils(bot))
