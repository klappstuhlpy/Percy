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
    from app.core import Cog

MARVEL_ICON_URL = 'https://klappstuhl.me/gallery/raw/HTBFL.png'
DC_ICON_URL = 'https://klappstuhl.me/gallery/raw/VmiCY.png'
VIZ_ICON_URL = 'https://klappstuhl.me/gallery/raw/nsTxu.png'

# Shown when a release has no cover art yet (LOCG serves a no-cover placeholder
# for upcoming issues, which the scraper resolves to ``None``).
COMING_SOON_COVER = 'https://klappstuhl.me/gallery/raw/vKMTn.jpeg'

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
            characters: list[dict[str, Any]] | None = None,
            variants: list[dict[str, Any]] | None = None,
            stories: list[dict[str, Any]] | None = None,
            cover_date: str | None = None,
            upc: str | None = None,
            isbn: str | None = None,
            sku: str | None = None,
            foc: str | None = None,
            comic_format: str | None = None,
            rating: float | None = None,
            pulls: int | None = None,
            setting: str | None = None,
            variant_count: int | None = None,
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

        # Enriched detail-page fields (see LOCGClient.fetch_comics).
        self.characters: list[dict[str, Any]] = characters or []
        self.variants: list[dict[str, Any]] = variants or []
        self.stories: list[dict[str, Any]] = stories or []
        self.cover_date: str | None = cover_date
        self.upc: str | None = upc
        self.isbn: str | None = isbn
        self.sku: str | None = sku
        self.foc: str | None = foc
        self.comic_format: str | None = comic_format
        self.rating: float | None = rating
        self.pulls: int | None = pulls
        self.setting: str | None = setting
        self.variant_count: int | None = variant_count

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

    @property
    def is_coming_soon(self) -> bool:
        """``True`` when no cover art exists yet (an upcoming, not-yet-drawn issue)."""
        return not self.image_url

    @property
    def cover_url(self) -> str:
        """The cover to display, falling back to the coming-soon placeholder."""
        return self.image_url or COMING_SOON_COVER

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

    def format_creators_compact(self) -> tuple[str, int]:
        """Headline creator roles only (writer/penciller/artist/cover).

        Returns the rendered text and a count of the omitted long-tail credits
        (inkers, colorists, letterers, assistant editors, …) for a "+N more" hint.
        """
        creators = self.creators or {}
        primary = ['Writer', 'Story', 'Story and Art', 'Penciller', 'Penciler', 'Artist', *MANGA_POSITIONS]

        lines: list[str] = []
        shown_roles: set[str] = set()

        for role in primary:
            names = creators.get(role)
            if names and role not in shown_roles:
                lines.append(f'**{role}**: {", ".join(alpha_surnames(names))}')
                shown_roles.add(role)

        cover_names: list[str] = []
        for role, names in creators.items():
            if 'Cover' in role:
                cover_names.extend(names)
                shown_roles.add(role)
        if cover_names:
            unique = list(dict.fromkeys(alpha_surnames(cover_names)))
            lines.append(f'**Cover**: {", ".join(unique)}')

        more = sum(len(v) for role, v in creators.items() if role not in shown_roles)
        return '\n'.join(lines), more

    def _general_info(self) -> str:
        """Render the brand-appropriate info block, omitting fields we don't have."""
        if self.brand == Brand.MANGA:
            return (
                f'### General Info\n'
                f'Price: {self.price_format}\n'
                f'Pages: {self.page_count}\n'
                f"Release Date: {discord.utils.format_dt(self.date, 'D') if self.date else 'Unknown'}\n"
                f"Category: {self.kwargs.get('category')}\n"
                f"Age Rating: {self.kwargs.get('age_rating')}"
            )

        # Compact, dot-separated lines. Collector minutiae (UPC/ISBN/SKU/cover date)
        # is intentionally omitted — it's noise in a feed; the title links out for it.
        release = discord.utils.format_dt(self.date, 'D') if self.date else None
        groups: list[list[str | None]] = [
            [v for v in (
                self.comic_format,
                f'{self.page_count} pages' if self.page_count else None,
                self.price_format if self.price is not None else None,
            ) if v],
            [v for v in (
                f'Release: {release}' if release else None,
                f'FOC: {self.foc}' if self.foc else None,
            ) if v],
            [v for v in (
                f'Setting: {self.setting}' if self.setting else None,
                f'Rating: {self.rating:.0f}%' if self.rating is not None else None,
                f'Pulls: {self.pulls:,}' if self.pulls else None,
            ) if v],
        ]
        lines = '\n'.join(' · '.join(g) for g in groups if g)
        return f'### General Info\n{lines}' if lines else ''

    def _format_characters(self) -> str:
        """Show Main/Supporting casts only, deduped and capped, with a "+N more" tail.

        Cameos and bit players are rolled into the overflow count rather than listed.
        """
        if not self.characters:
            return ''

        order = ['Main', 'Supporting', 'Cameo', 'Other']
        rank = {t: i for i, t in enumerate(order)}

        # One entry per name, keeping its strongest billing.
        best: dict[str, str] = {}
        for character in self.characters:
            name = character.get('name')
            if not name:
                continue
            ctype = character.get('type') or 'Other'
            if name not in best or rank.get(ctype, 99) < rank.get(best[name], 99):
                best[name] = ctype

        by_type: dict[str, list[str]] = {}
        for name, ctype in best.items():
            by_type.setdefault(ctype, []).append(name)

        caps = {'Main': 12, 'Supporting': 10}
        lines: list[str] = []
        overflow = 0
        for billing, cap in caps.items():
            names = by_type.get(billing)
            if not names:
                continue
            lines.append(f'**{billing}**: {", ".join(names[:cap])}')
            overflow += max(len(names) - cap, 0)

        # Everything that isn't Main/Supporting (cameos, misc) becomes overflow.
        overflow += sum(len(v) for t, v in by_type.items() if t not in caps)

        text = '\n'.join(lines)
        if overflow:
            text = f'{text}\n-# +{overflow} more' if text else f'-# {overflow} characters'
        return text

    def to_container(self, full_img: bool = True) -> discord.ui.Container:
        """Build the Components V2 release card for this comic.

        ``full_img`` shows the cover as a full-width :class:`~discord.ui.MediaGallery`;
        otherwise it is a compact :class:`~discord.ui.Thumbnail` beside the heading.
        Variant covers are folded into this card rather than rendered separately.
        """
        colour = self.brand.colour if self.brand is not None else 0
        container = discord.ui.Container(accent_colour=colour)

        heading = f'## [{self.title or "Untitled"}]({self.url})' if self.url else f'## {self.title or "Untitled"}'
        tag = '-# 🔜 **Coming Soon** — cover art not yet available' if self.is_coming_soon else None

        if not full_img:
            body = heading
            if tag:
                body += f'\n{tag}'
            if self.description:
                body += f'\n{truncate(self.description, 600)}'
            container.add_item(discord.ui.Section(body, accessory=discord.ui.Thumbnail(self.cover_url)))
        else:
            container.add_item(discord.ui.TextDisplay(heading))
            if tag:
                container.add_item(discord.ui.TextDisplay(tag))
            if self.description:
                container.add_item(discord.ui.TextDisplay(truncate(self.description, 700)))

        container.add_item(discord.ui.Separator())

        info = self._general_info()
        if info:
            container.add_item(discord.ui.TextDisplay(info))

        if self.creators:
            creators_text, more = self.format_creators_compact()
            if more:
                creators_text = f'{creators_text}\n-# +{more} more credits'
            container.add_item(discord.ui.TextDisplay(f'### Creators\n{creators_text}'))

        characters = self._format_characters()
        if characters:
            container.add_item(discord.ui.TextDisplay(f'### Characters\n{characters}'))

        # Collapse the per-story breakdown to a single line — the full list is a wall
        # for collections and adds little in a feed.
        if len(self.stories) > 1:
            total_pages = sum(s.get('pages') or 0 for s in self.stories)
            summary = f'{len(self.stories)} stories collected'
            if total_pages:
                summary += f' · {total_pages} pages'
            container.add_item(discord.ui.TextDisplay(f'-# {summary}'))

        if full_img:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(self.cover_url)))

        variant_items = [
            discord.MediaGalleryItem(v['cover'], description=truncate(v.get('name') or '', 90))
            for v in self.variants[:10] if v.get('cover')
        ]
        if variant_items:
            count = self.variant_count or len(self.variants)
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f'### Variant Covers • {count}'))
            container.add_item(discord.ui.MediaGallery(*variant_items))

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


class ComicFeed(BaseRecord, table="comic_config", pk="id"):

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
