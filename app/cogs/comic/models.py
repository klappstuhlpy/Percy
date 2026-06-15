from __future__ import annotations

import datetime
import json
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from app.database import BaseRecord
from app.utils import fnumb, truncate
from app.utils.helpers import NotCaseSensitiveEnum

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

    from app.core import Cog

MARVEL_ICON_URL = 'https://klappstuhl.me/gallery/raw/HTBFL.png'
DC_ICON_URL = 'https://klappstuhl.me/gallery/raw/VmiCY.png'
VIZ_ICON_URL = 'https://klappstuhl.me/gallery/raw/nsTxu.png'

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
            return f'Data provided by [League of Comic Geeks](https://leagueofcomicgeeks.com/). © {year} DC'
        elif self == self.MARVEL:
            return f'Data provided by [League of Comic Geeks](https://leagueofcomicgeeks.com/). © {year} MARVEL'
        elif self == self.MANGA:
            return f'© {year} [VIZ Media](https://www.viz.com/), LLC. All rights reserved.'
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
            brand: Brand | None = None,
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
        self.brand: Brand | None = brand
        self.id: int | str | None = _id
        self.title: str | None = title
        self.description: str | None = description
        self.creators: dict[str, list[str]] | None = creators

        self.image_url: str | None = image_url
        self.url: str = url or ""

        self.date: datetime.datetime | None = date
        self.page_count: int | None = page_count
        self.price: float | None = price

        self.copyright: str | None = _copyright
        self.kwargs = kwargs

    def __str__(self) -> str:
        return self.title or ""

    def __repr__(self) -> str:
        brand = self.brand.name if self.brand is not None else "None"
        return f'<GenericComic id={self.id} title={self.title} brand={brand}>'

    @property
    def writer(self) -> str:
        creators = self.creators or {}
        next_key = next((a for a in ['Writer', 'Creator', *MANGA_POSITIONS] if a in creators), None)
        return ', '.join(alpha_surnames(creators[next_key] if next_key else []))

    @property
    def price_format(self) -> str:
        return f'${fnumb(self.price)} USD' if self.price is not None else 'Unknown'

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
            f"**{k}**: {', '.join(alpha_surnames(self.creators[k]))}"
            for k in keys
            if (not compact or k in compact_positions) and (cover or not k.endswith('(Cover)'))
        )

    def to_container(self, full_img: bool = True) -> discord.ui.Container:
        """Build the Components V2 release card for this comic.

        ``full_img`` shows the cover as a full-width :class:`~discord.ui.MediaGallery`;
        otherwise it is a compact :class:`~discord.ui.Thumbnail` beside the heading.
        """
        colour = self.brand.colour if self.brand is not None else 0
        container = discord.ui.Container(accent_colour=colour)

        heading = f'## [{self.title or "Untitled"}]({self.url})' if self.url else f'## {self.title or "Untitled"}'

        if not full_img and self.image_url:
            body = heading
            if self.description:
                body += f'\n{truncate(self.description, 1200)}'
            container.add_item(discord.ui.Section(body, accessory=discord.ui.Thumbnail(self.image_url)))
        else:
            container.add_item(discord.ui.TextDisplay(heading))
            if self.description:
                container.add_item(discord.ui.TextDisplay(truncate(self.description, 1500)))

        container.add_item(discord.ui.Separator())

        if self.brand == Brand.MANGA:
            container.add_item(discord.ui.TextDisplay(
                f'### General Info\n'
                f'Price: {self.price_format}\n'
                f'Pages: {self.page_count}\n'
                f"Release Date: {discord.utils.format_dt(self.date, 'D') if self.date else 'Unknown'}\n"
                f"Category: {self.kwargs.get('category')}\n"
                f"Age Rating: {self.kwargs.get('age_rating')}"
            ))
        else:
            container.add_item(discord.ui.TextDisplay(
                f'### General Info\n'
                f'Price: {self.price_format}\n'
                f'Pages: {self.page_count}'
            ))

        if self.creators:
            container.add_item(discord.ui.TextDisplay(f'### Creators\n{self.format_creators()}'))

        if full_img and self.image_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(self.image_url)))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'-# {self.title or ""} • {self.copyright or ""}'))
        return container

    def to_instance(self, message: discord.Message) -> GenericComicMessage:
        return GenericComicMessage(self, message)


class GenericComicMessage(GenericComic):
    def __init__(self, comic: GenericComic, message: discord.Message) -> None:
        super().__init__(**comic.__dict__)
        self.message = message

    def more(self) -> str:
        return self.message.jump_url


class ComicFeed(BaseRecord):

    if TYPE_CHECKING:
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

    __slots__ = ('brand', 'channel_id', 'cog', 'day', 'format', 'guild_id', 'id', 'next_pull', 'pin', 'ping')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.brand = Brand.coerce(self.brand)  # type: ignore[assignment]
        self.format = Format.coerce(self.format)  # type: ignore[assignment]

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> ComicFeed:
        record = await self.cog.bot.db.comics.update_config(self.id, key, values, connection=connection)
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
