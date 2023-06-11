from __future__ import annotations

import dataclasses
import datetime
import re
from dataclasses import dataclass
from typing import NamedTuple, Dict, Optional

import discord
import feedparser
from bs4 import BeautifulSoup
from dateutil.parser import parse
from discord.ext import commands, tasks
from discord.utils import MISSING
from typing import TypeVar

from bot import Percy
from cogs.utils.async_utils import executor
from cogs.utils.constants import DSTATUS_CHANNEL_ID, PH_HEAD_DEV_ROLE_ID

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

    async def update(self, cog: DiscordStatus, state: State) -> None:
        self.updates.insert(0, state)
        self.last_updated_at = state.started_at

        message = await cog.channel.fetch_message(self.message_id)
        embed = message.embeds[0]
        embed.add_field(name=f"{STATE_EMOJI.get(state.state.lower())} {state.state} "
                             f"({discord.utils.format_dt(state.started_at, 'R')})",
                        value=state.text, inline=False)
        embed.colour = EMBED_COLOR.get(state.state.lower())
        await message.edit(embed=embed)

    def build_embed(self) -> discord.Embed:
        updates = self.updates.copy()
        updates.reverse()

        embed = discord.Embed(title=self.title, timestamp=self.published_at, url=self.link,
                              colour=EMBED_COLOR.get(updates[0].state.lower()))
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

    def as_dict(self) -> Dict:
        return dataclasses.asdict(self)


class DiscordStatus(commands.Cog):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._last_incident: Incident = MISSING
        # self.config: config_file = config_file("dstatus")

    async def cog_load(self) -> None:
        record = self.bot.data_storage.get("last_incident")
        if record:
            self._last_incident = Incident(**record)
        self.check_new_incident.start()

    async def cog_unload(self) -> None:
        self.check_new_incident.stop()

    @discord.utils.cached_property
    def channel(self) -> discord.TextChannel:
        return self.bot.get_channel(DSTATUS_CHANNEL_ID)

    @executor
    def parse_feed(self, feed: str) -> Optional[feedparser.FeedParserDict]:
        parsed = feedparser.parse(feed)
        if parsed:
            return parsed
        return None

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

            cache = []
            for p in find:
                TIMESTAMP_REGEX = re.compile(
                    r"(?P<month>\w+)\s+(?P<day>\d+),\s+(?P<hour>\d+):(?P<minute>\d+)\s+(?P<tzinfo>\w{3})")
                match = TIMESTAMP_REGEX.search(p.text).expand(
                    fr"{datetime.datetime.now().year} \g<month> \g<day> \g<hour>:\g<minute> -0700")
                text = re.sub(TIMESTAMP_REGEX, '', p.text)

                state, text = text.split(" - ", 1)
                dt = parse(match).astimezone(datetime.timezone.utc)
                cache.append(State(started_at=dt, state=state, text=text))

            return cache

        incidents: list[State] = await bs4(entry.summary)

        if self._last_incident is not MISSING and entry.title == self._last_incident.title:
            if len(incidents) == len(self._last_incident.updates):
                return

            await self._last_incident.update(self, incidents[0])
            await self.bot.data_storage.put("last_incident", self._last_incident.as_dict())
            return

        incident = Incident.temporary(
            status=incidents[0].state,
            link=entry.link,
            published_at=parse(entry.published).astimezone(datetime.timezone.utc),
            last_updated_at=incidents[0].started_at,
            title=entry.title,
            updates=incidents
        )

        message = await self.channel.send(content=f"<@&{PH_HEAD_DEV_ROLE_ID}>", embed=incident.build_embed(),
                                          allowed_mentions=discord.AllowedMentions(roles=True))

        incident.message_id = message.id
        self._last_incident = incident

        await self.bot.data_storage.put("last_incident", self._last_incident.as_dict())


async def setup(bot: Percy) -> None:
    await bot.add_cog(DiscordStatus(bot))
