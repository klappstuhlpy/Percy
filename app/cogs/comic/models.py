from __future__ import annotations

import datetime
import json
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from app.clients.comic import Brand
from app.database import BaseRecord
from app.utils.helpers import NotCaseSensitiveEnum

if TYPE_CHECKING:
    from app.core import Bot, Cog

class Format(NotCaseSensitiveEnum):
    FULL = 'Full'
    COMPACT = 'Compact'
    SUMMARY = 'Summary'

    def __str__(self) -> str:
        return self.name


class ComicFeed(BaseRecord, table="comic_config", pk="id"):

    if TYPE_CHECKING:
        bot: Bot
        cog: Cog
    id: int
    guild_id: int
    channel_id: int
    format: Format
    brand: Brand
    day: int
    ping: bool
    pin: bool
    next_pull: datetime.datetime

    __slots__ = ('bot', 'brand', 'channel_id', 'cog', 'day', 'format', 'guild_id', 'id', 'next_pull', 'pin', 'ping')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bot = self.cog.bot

    def _coerce(self) -> None:
        self.brand = Brand.coerce(self.brand)  # type: ignore[assignment]
        self.format = Format.coerce(self.format)  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            'guild_id': self.guild_id,
            'channel_id': self.channel_id,
            'brand': self.brand.name,
            'format': self.format.name,
            'day': self.day,
            'ping': self.ping,
            'pin': self.pin,
            'next_pull': self.next_pull,
        }

    def to_container(self, *, header: str | None = None) -> discord.ui.Container:
        """Build the Components V2 card for this feed configuration.

        ``header`` prepends an optional line (e.g. a success notice) above the title, so
        the card can replace a ``send_success(..., embed=...)`` call in a single message.
        """
        container = discord.ui.Container(accent_colour=self.brand.colour)

        body = f'## {self.brand.value} Feed Configuration'
        if header:
            body = f'{header}\n{body}'
        if self.brand == Brand.MANGA:
            body += '\nMangas are only published once in the first week of a month.'
        container.add_item(discord.ui.Section(body, accessory=discord.ui.Thumbnail(self.brand.icon_url)))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'**Publish Channel** • <#{self.channel_id}>\n'
            f'**Format** • {self.format.value}\n'
            f'**Next Scheduled** • {discord.utils.format_dt(self.next_pull, "D")}\n'
            f'**Ping Role** • {f"<@&{self.ping}>" if self.ping else "None"}\n'
            f'**Message Pin** • {"Enabled" if self.pin else "Disabled"}'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'-# [{self.guild_id}] • {self.brand.name}'))
        return container

    async def delete(self) -> None:
        """|coro|

        Deletes the Comic Feed Configuration from the Database
        """
        await self.cog.bot.db.comics.delete_config(self.guild_id, self.brand.name)
        self.cog.get_comic_config.invalidate_containing(str(self.guild_id))  # type: ignore[attr-defined]

    def next_scheduled(self, day: int | None = None) -> datetime.datetime:
        """Returns the Next Scheduled Date for the Comic Feed.

        Parameters
        ----------
        day: int
            The Day of the Week to Schedule the Feed

        Returns
        -------
        datetime.datetime
            The Next Scheduled Date
        """
        day = day or self.day
        now = discord.utils.utcnow().date()
        soon = now + datetime.timedelta(days=(day - now.isoweekday()) % 7)
        combined = datetime.datetime.combine(soon, datetime.time(0), tzinfo=datetime.UTC) \
            .astimezone(datetime.UTC)

        if combined < discord.utils.utcnow():
            if self.brand == Brand.MANGA:
                combined = combined.replace(month=combined.month + 1, day=day)
            else:
                combined += datetime.timedelta(days=7)

        return combined.replace(tzinfo=None)

    @property
    def prev_scheduled(self) -> datetime.datetime:
        """Returns the Previous Scheduled Date for the Comic Feed."""
        return self.next_scheduled() - datetime.timedelta(days=7)


class ComicJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Enum):
            return o.name
        elif isinstance(o, commands.Cog):
            return f"<class '{o.__module__}'>"
        elif isinstance(o, datetime.datetime):
            return o.isoformat()
        elif isinstance(o, object):
            return f"<class '{o.__class__.__module__}.{o.__class__.__name__}'>"
        return super().default(o)
