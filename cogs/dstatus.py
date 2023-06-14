from __future__ import annotations

import dataclasses
import datetime
import json
import re
from dataclasses import dataclass
from typing import NamedTuple, Optional

import asyncpg
import discord
import feedparser
from bs4 import BeautifulSoup
from dateutil.parser import parse
from discord.ext import commands, tasks
from discord.utils import MISSING
from typing import TypeVar

from bot import Percy
from cogs import command, command_permissions, PermissionTemplate
from cogs.utils import cache
from cogs.utils.context import GuildContext
from cogs.utils.helpers import PostgresItem, BasicJSONEncoder
from cogs.utils.tasks import executor

DS_RSS_FEED = "https://discordstatus.com/history.rss"
DISCORD_ICON_URL = "https://images-ext-2.discordapp.net/external/6jW0q_egONj8FelyNsUt_ighZ6obXn0TTFuxLNJf1v4/https/discord.com/assets/f9bb9c4af2b9c32a2c5ee0014661546d.png"

STATE_EMOJI = {
    "resolved": "<:online:1101531229188272279>",
    "investigating": "<:idle:1101530975151849522>",
    "monitoring": "<:idle:1101530975151849522>",
    "identified": "<:dnd:1101531066600259685>",
    "update": "<:offline:1105801866312417331>"
}

EMBED_COLOR = {
    "resolved": 0x7BCBA7,
    "investigating": 0xFCC25E,
    "monitoring": 0xFCC25E,
    "identified": 0xF57E7E,
    "update": 0xFCC25E
}


T = TypeVar('T')


class State(NamedTuple):
    started_at: datetime
    state: str
    text: str


@dataclass()
class Incident:
    status: str
    link: str
    published_at: datetime.datetime
    last_updated_at: datetime.datetime
    title: str
    updates: list[State]
    message_id: Optional[int]

    @classmethod
    def temporary(cls, **kwargs) -> 'Incident':
        """Creates a temporary instance of this class."""
        if not kwargs.get('message_id'):
            kwargs |= {'message_id': MISSING}
        return cls(**kwargs)

    async def update_message(self, config: ShortStatusConfig):
        new = set(config.dstatus_last_incident.updates) - set(self.updates)
        if new:
            for state in new:
                self.updates.insert(0, state)

        if len(self.updates) == len(config.dstatus_last_incident.updates):
            return

        message = await config.get_message()
        await message.edit(embed=self.build_embed())

    def build_embed(self) -> discord.Embed:
        updates = self.updates.copy()
        updates.reverse()

        embed = discord.Embed(title=self.title, timestamp=self.published_at, url=self.link,
                              colour=EMBED_COLOR.get(updates[-1].state.lower()))
        embed.set_author(name="Discord Status", url="https://discordstatus.com/", icon_url=DISCORD_ICON_URL)
        embed.set_footer(text="Started at")

        for update in updates:
            embed.add_field(
                name=f"{STATE_EMOJI.get(update.state.lower())} {update.state} "
                     f"({discord.utils.format_dt(update.started_at, 'R')})",
                value=update.text,
                inline=False
            )

        return embed

    def as_dict(self) -> str:
        return json.dumps(dataclasses.asdict(self), cls=BasicJSONEncoder)


class ShortStatusConfig(PostgresItem):
    bot: Percy

    id: int
    dstatus_notification_channel: Optional[int]
    dstatus_last_incident: Optional[Incident | dict]

    def __init__(self, bot: Percy, **kwargs):
        self.bot: Percy = bot

        super().__init__(**kwargs)
        self.dstatus_last_incident = Incident(**self.dstatus_last_incident) if self.dstatus_last_incident else None

    def get_channel(self) -> Optional[discord.TextChannel]:
        if not self.dstatus_notification_channel:
            return None
        return self.bot.get_channel(self.dstatus_notification_channel)

    async def get_message(self) -> Optional[discord.Message]:
        channel = self.get_channel()
        if not channel:
            return None
        return await channel.fetch_message(self.dstatus_last_incident.message_id)


class DiscordStatus(commands.Cog):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    async def cog_load(self) -> None:
        self.check_new_incident.start()

    async def cog_unload(self) -> None:
        self.check_new_incident.stop()

    @executor
    def parse_feed(self, feed: str) -> Optional[feedparser.FeedParserDict]:
        parsed = feedparser.parse(feed)
        if parsed:
            return parsed
        return None

    @command(commands.hybrid_group, name="discord-status", fallback="subscribe",
             aliases=["dstatus"], description="Subscribe/Unsubscribe to Discord Status updates.")
    @command_permissions(user=PermissionTemplate.mod)
    @commands.guild_only()
    async def dstatus(self, ctx: GuildContext, channel: Optional[discord.TextChannel] = None):
        """Subscribes to Discord Status updates.

        Leave the channel empty to unsubscribe.
        """

        if not channel:
            query = """
                INSERT INTO guild_config (id) VALUES($1) ON CONFLICT (id) 
                DO UPDATE SET 
                    dstatus_notification_channel = NULL, 
                    dstatus_last_incident = DEFAULT;
            """
            result = await ctx.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.send(f"{ctx.tick(False)} You are not subscribed to Discord Status updates.")

            self.bot.moderation.get_guild_config.invalidate(self.bot.moderation, ctx.guild.id)
            return await ctx.send(f"{ctx.tick(True)} Successfully unsubscribed from Discord Status updates.")

        query = """
            INSERT INTO guild_config (id, dstatus_notification_channel) VALUES($1, $2) ON CONFLICT (id) 
            DO UPDATE SET 
                dstatus_notification_channel = $2;
        """
        await ctx.db.execute(query, ctx.guild.id, channel.id)
        await ctx.send(f"{ctx.tick(True)} Successfully subscribed to Discord Status updates in {channel.mention}.")

        self.bot.moderation.get_guild_config.invalidate(self.bot.moderation, ctx.guild.id)

    @cache.cache()
    async def get_all_subscribers(self, *, connection: Optional[asyncpg.Connection] = None) -> list[ShortStatusConfig]:
        """Gets all channels subscribed to Discord Status updates."""
        conn = connection or self.bot.pool

        query = """
            SELECT id, dstatus_notification_channel, dstatus_last_incident
            FROM guild_config
            WHERE dstatus_notification_channel IS NOT NULL;
        """
        records = await conn.fetch(query)

        return [ShortStatusConfig(self.bot, record=record) for record in records]

    async def bulk_update_last_incident(self, subscribers: list[ShortStatusConfig]) -> None:
        """Updates the last incident for the given subscribers."""
        query = """
            UPDATE guild_config
            SET dstatus_last_incident = $1::TEXT::JSONB
            WHERE id = $2;
        """
        for subscriber in subscribers:
            await subscriber.dstatus_last_incident.update_message(subscriber)
            await self.bot.pool.execute(query, subscriber.dstatus_last_incident.as_dict(), subscriber.id)

    @tasks.loop(minutes=5)
    async def check_new_incident(self):
        await self.bot.wait_until_ready()

        feed = await self.parse_feed(DS_RSS_FEED)
        if not feed or (feed and not feed.entries):
            return

        entry = feed.entries[0]

        @executor
        def bs4(string: str) -> list[State]:
            soup = BeautifulSoup(string, "html.parser")
            find = soup.find_all("p")

            states = []
            for p in find:
                TIMESTAMP_REGEX = re.compile(
                    r"(?P<month>\w+)\s+(?P<day>\d+),\s+(?P<hour>\d+):(?P<minute>\d+)\s+(?P<tzinfo>\w{3})")
                match = TIMESTAMP_REGEX.search(p.text).expand(
                    fr"{datetime.datetime.now().year} \g<month> \g<day> \g<hour>:\g<minute> -0700")
                text = re.sub(TIMESTAMP_REGEX, '', p.text)

                state, text = text.split(" - ", 1)
                dt = parse(match).astimezone(datetime.timezone.utc)
                states.append(State(started_at=dt, state=state, text=text))

            return states

        incidents: list[State] = await bs4(entry.summary)

        to_update = []
        to_insert = []

        for config in await self.get_all_subscribers():
            if config.dstatus_last_incident:
                if config.dstatus_last_incident.title == entry.title:
                    if len(incidents) == len(config.dstatus_last_incident.updates):
                        continue

                    config.dstatus_last_incident.update(incidents)
                    to_update.append(config)
                else:
                    to_insert.append(config)
            else:
                to_insert.append(config)

        if to_update:
            await self.bulk_update_last_incident(to_update)

        if to_insert:
            incident = Incident.temporary(
                status=incidents[0].state,
                link=entry.link,
                published_at=parse(entry.published).astimezone(datetime.timezone.utc),
                last_updated_at=incidents[0].started_at,
                title=entry.title,
                updates=incidents
            )
            for config in to_insert:
                channel = config.get_channel()
                if channel:
                    message = await channel.send(embed=incident.build_embed())
                    incident.message_id = message.id
                config.dstatus_last_incident = incident

            await self.bulk_update_last_incident(to_insert)

        if to_insert or to_update:
            self.get_all_subscribers.invalidate()


async def setup(bot: Percy) -> None:
    await bot.add_cog(DiscordStatus(bot))
