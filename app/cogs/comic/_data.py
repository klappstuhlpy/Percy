from __future__ import annotations

import datetime
import json
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import discord
from discord.ext import commands

from app.database import BaseRecord
from app.utils import fnumb
from app.utils.helpers import NotCaseSensitiveEnum

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

    from app.core import CogT

MARVEL_ICON_URL = 'https://klappstuhl.me/gallery/IoXwuZkPas.png'
DC_ICON_URL = 'https://klappstuhl.me/gallery/JFhdZbBozA.png'
VIZ_ICON_URL = 'https://klappstuhl.me/gallery/WbpikEeKxf.png'

MANGA_POSITIONS = ['Story', 'Art', 'Story and Art', 'Original Conecept', 'Written', 'Drawn']


def alpha_surnames(names: list[str]) -> list[str]:
    return sorted(names, key=lambda x: x.split(' ')[-1])


class Brand(NotCaseSensitiveEnum):
    MARVEL = 'Marvel'
    DC = 'DC'
    MANGA = 'Manga'

    def __str__(self) -> str:
        return self.name

    @property
    def icon_url(self) -> str:
        if self == self.MARVEL:
            return MARVEL_ICON_URL
        elif self == self.DC:
            return DC_ICON_URL
        elif self == self.MANGA:
            return VIZ_ICON_URL
        else:
            return ""

    @property
    def link(self) -> str:
        if self == self.MARVEL:
            return 'Marvel.com'
        elif self == self.DC:
            return 'DC.com'
        elif self == self.MANGA:
            return 'Viz.com'
        else:
            return 'Unknown'

    @property
    def colour(self) -> int:
        if self == self.MARVEL:
            return 0xEC1D24
        elif self == self.DC:
            return 0x0074E8
        elif self == self.MANGA:
            return 0xFFFFFF
        else:
            return 0x000000

    @property
    def default_day(self) -> int:
        if self == self.DC:
            return 3
        else:
            return 1  # Marvel and Manga

    @property
    def copyright(self) -> str:
        year = datetime.datetime.now().year
        if self == self.DC:
            return '© & ™ DC. ALL RIGHTS RESERVED'
        elif self == self.MARVEL:
            return f'Data provided by Marvel. © {year} MARVEL'
        elif self == self.MANGA:
            return f'© {year} VIZ Media, LLC. All rights reserved.'
        else:
            return ""


class Format(NotCaseSensitiveEnum):
    FULL = 'Full'
    COMPACT = 'Compact'
    SUMMARY = 'Summary'

    def __str__(self) -> str:
        return self.name


class GenericComic:
    """A wrapped Comic Object that Supports a Marvel and DC Comic or Manga Source

    Parameters
    ----------
    brand: Brand
        The Brand of the Comic
    id: int | str
        The ID of the Comic
    title: str
        The Title of the Comic
    description: str
        The Description of the Comic
    creators: Dict[str, List[str]]
        The Creators of the Comic
    image_url: str
        The Image URL of the Comic
    url: str
        The URL of the Comic
    page_count: int
        The Page Count of the Comic
    price: float
        The Price of the Comic
    date: datetime
        The ReleaseDate of the Comic
    **kwargs
        Any other Keyword Arguments
    """

    def __init__(
            self,
            *,
            brand: Brand = None,
            _id: int | str | None = None,
            title: str | None = None,
            description: str | None = None,
            creators: dict[str, list[str]] | None = None,
            image_url: str | None = None,
            url: str | None = None,
            page_count: int | None = None,
            price: float | None = None,
            _copyright: str | None = None,
            date: datetime.datetime | None = None,
            **kwargs: Any
    ) -> None:
        self.brand: Brand = brand
        self.id: int = _id
        self.title: str = title
        self.description: str = description
        self.creators: dict[str, list[str]] = creators

        self.image_url: str = image_url
        self.url: str = url or ""

        self.date: datetime.datetime = date
        self.page_count: int = page_count
        self.price: float = price

        self.copyright: str = _copyright
        self.kwargs = kwargs

    def __str__(self) -> str:
        return self.title

    def __repr__(self) -> str:
        return f'<GenericComic id={self.id} title={self.title} brand={self.brand.name}>'

    @property
    def writer(self) -> str:
        next_key = next((a for a in ['Writer', 'Creator', *MANGA_POSITIONS] if a in self.creators), None)
        return ', '.join(alpha_surnames(self.creators[next_key] if next_key else []))

    @property
    def price_format(self) -> str:
        return f'${fnumb(self.price)} USD' if self.price else 'Unknown'

    def format_creators(self, *, cover: bool = False, compact: bool = False) -> str:
        priority = ['Writer', 'Artist', 'Penciler', 'Inker', 'Colorist', 'Letterer', 'Editor', *MANGA_POSITIONS]

        def sorting_key(person: str) -> int:
            try:
                return priority.index(person)
            except ValueError:
                return len(priority)

        compact_positions = {'Writer', 'Penciler', 'Artist', *MANGA_POSITIONS}
        keys = sorted(self.creators.keys(), key=lambda k: (sorting_key(k), k))
        return '\n'.join(
            f'**{k}**: {', '.join(alpha_surnames(self.creators[k]))}'
            for k in keys
            if (not compact or k in compact_positions) and (cover or not k.endswith('(Cover)'))
        )

    def to_embed(self, full_img: bool = True) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            colour=self.brand.colour,
            description=self.description,
            url=self.url,
        )

        if self.brand == Brand.MANGA:
            embed.add_field(name='General Info',
                            value=f'Price: {self.price_format}\n'
                                  f'Pages: {self.page_count}\n'
                                  f'Release Date: {discord.utils.format_dt(self.date, 'D')}\n'
                                  f'Category: {self.kwargs.get('category')}\n'
                                  f'Age Rating: {self.kwargs.get('age_rating')}')

            if self.creators:
                embed.add_field(name='Creators', value=self.format_creators())
        else:
            if self.creators:
                embed.add_field(name='Creators', value=self.format_creators())

            embed.add_field(name='General Info',
                            value=f'Price: {self.price_format}\n'
                                  f'Pages: {self.page_count}')

        embed.set_footer(text=f'{self.title} • {self.copyright}', icon_url=self.brand.icon_url)

        if full_img:
            embed.set_image(url=self.image_url)
        else:
            embed.set_thumbnail(url=self.image_url)

        return embed

    def to_instance(self, message: discord.Message) -> GenericComicMessage:
        return GenericComicMessage(self, message)


class GenericComicMessage(GenericComic):
    def __init__(self, comic: GenericComic, message: discord.Message) -> None:
        super().__init__(**comic.__dict__)
        self.message = message

    def more(self) -> str:
        return self.message.jump_url


B = TypeVar('B', bound=Brand)


class ComicFeed(BaseRecord, Generic[B]):

    cog: CogT
    id: int
    guild_id: int
    channel_id: int
    format: Format
    brand: Brand
    day: int
    ping: bool
    pin: bool
    next_pull: datetime.datetime

    __slots__ = ('cog', 'id', 'guild_id', 'channel_id', 'format', 'brand', 'day', 'ping', 'pin', 'next_pull')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.brand: B = Brand[str(self.brand)]
        self.format = Format[str(self.format)]

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> ComicFeed[B]:
        query = f"""
            UPDATE comic_config
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.cog.bot).fetchrow(query, self.id, *values.values())
        return self.__class__(cog=self.cog, record=record)

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

    def to_embed(self) -> discord.Embed:
        """Returns an Embed Representation of the Comic Feed Configuration.

        Returns
        -------
        discord.Embed
            The Embed Representation of the Comic Feed Configuration
        """
        embed = discord.Embed(
            title=f'{self.brand.value} Feed Configuration',
            color=self.brand.colour
        )
        if self.brand == Brand.MANGA:
            embed.description = 'Mangas are only published once in the first week of a month.'

        embed.add_field(name='Publish Channel', value=f'<#{self.channel_id}>')
        embed.add_field(name='Format', value=f'{self.format.value}')
        embed.add_field(name='Next Scheduled', value=discord.utils.format_dt(self.next_pull, 'D'))
        embed.add_field(name='Ping Role', value=f'<@&{self.ping}>' if self.ping else None)
        embed.add_field(name='Message Pin', value='Enabled' if self.pin else 'Disabled')
        embed.set_footer(text=f'[{self.guild_id}] • {self.brand.name}')
        embed.set_thumbnail(url=self.brand.icon_url)
        return embed

    async def delete(self) -> None:
        """|coro|

        Deletes the Comic Feed Configuration from the Database
        """
        query = "DELETE FROM comic_config WHERE guild_id = $1 AND brand = $2;"
        await self.cog.bot.db.execute(query, self.guild_id, self.brand.name)
        self.cog.get_comic_config.invalidate_containing(str(self.guild_id))

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
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.name
        elif isinstance(obj, commands.Cog):
            return f"<class '{obj.__module__}'>"
        elif isinstance(obj, datetime.datetime):
            return obj.isoformat()
        elif isinstance(obj, object):
            return f"<class '{obj.__class__.__module__}.{obj.__class__.__name__}'>"
        return super().default(obj)
