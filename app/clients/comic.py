from __future__ import annotations

import datetime
import logging
import re
from typing import Any, ClassVar
from urllib.parse import urljoin

import aiohttp
import discord
from bs4 import BeautifulSoup

from app.utils import executor, fnumb, truncate, utcparse
from app.utils.helpers import NotCaseSensitiveEnum
from app.utils.lock import lock

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
        year = datetime.datetime.now(datetime.UTC).year
        if self == self.DC:
            return f'Data provided by [League of Comic Geeks](https://leagueofcomicgeeks.com/). © {year} DC'
        elif self == self.MARVEL:
            return f'Data provided by [League of Comic Geeks](https://leagueofcomicgeeks.com/). © {year} MARVEL'
        elif self == self.MANGA:
            return f'© {year} [VIZ Media](https://www.viz.com/), LLC. All rights reserved.'
        else:
            return ""


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


log = logging.getLogger(__name__)


class LOCGClient:
    """Client for the self-hosted League of Comic Geeks API wrapper.

    Calls the locg-api Express service to fetch weekly Marvel/DC releases.
    """

    def __init__(self, session: aiohttp.ClientSession, *, base_url: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def fetch_comics(self, publisher: str, *, date: str | None = None) -> list[GenericComic]:
        """Fetch comics for a publisher from the locg-api service.

        Parameters
        ----------
        publisher: :class:`str`
            One of ``'marvel'`` or ``'dc'``.
        date: :class:`str` | None
            Optional date in ``YYYY-MM-DD`` format. Defaults to this week.
        """
        url = f"{self.base_url}/comics/{publisher}"
        params = {}
        if date:
            params["date"] = date

        log.debug("Connecting to locg-api at %s (publisher=%s, date=%s)", url, publisher, date or "this week")
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    json = await resp.json()
                    log.error("locg-api returned status %s for %s: %s", resp.status, publisher, json)
                    # raise RuntimeError(f"locg-api returned {resp.status}: {text}")
                data = await resp.json()
        except aiohttp.ClientConnectionError as exc:
            log.error("Failed to connect to locg-api at %s for %s: %s", self.base_url, publisher, exc)
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.error("locg-api request for %s failed: %s", publisher, exc)
            raise

        log.debug("Connected to locg-api; parsing %s response.", publisher)

        brand = Brand.MARVEL if publisher == "marvel" else Brand.DC
        comics: list[GenericComic] = []

        for entry in data.get("comics", []):
            release_date = None
            if entry.get("releaseDate"):
                try:
                    release_date = utcparse(entry["releaseDate"])
                except (ValueError, TypeError):
                    pass

            price: float | None = None
            if entry.get("price"):
                try:
                    price_str = entry["price"].replace("$", "").strip()
                    price = float(price_str)
                except (ValueError, TypeError):
                    pass

            page_count = entry.get("pages")

            comics.append(
                GenericComic(
                    brand=brand,
                    _id=entry.get("id") or (entry.get("title") or entry.get("name") or "").replace(" ", ""),
                    title=entry.get("title") or entry.get("name"),
                    description=entry.get("description") or None,
                    creators=self._group_creators(entry.get("creators")),
                    image_url=entry.get("cover"),
                    url=entry.get("url") or "",
                    page_count=page_count if isinstance(page_count, int) else None,
                    price=price,
                    _copyright=brand.copyright,
                    date=release_date,
                    # Enriched detail fields (present when locg-api ran with ?details=true).
                    characters=entry.get("characters") or [],
                    variants=entry.get("variants") or [],
                    stories=entry.get("stories") or [],
                    cover_date=entry.get("coverDate"),
                    upc=entry.get("upc"),
                    isbn=entry.get("isbn"),
                    sku=entry.get("sku"),
                    foc=entry.get("foc"),
                    comic_format=entry.get("format"),
                    rating=entry.get("rating"),
                    pulls=entry.get("pulls"),
                    setting=entry.get("setting"),
                    variant_count=entry.get("variantCount"),
                )
            )

        log.info("Fetched %d %s comic(s) from locg-api.", len(comics), publisher)
        return comics

    @staticmethod
    def _group_creators(creators: list[dict] | None) -> dict[str, list[str]] | None:
        """Collapse locg-api's flat ``[{name, role, url}]`` credits into the
        ``{role: [names]}`` shape Percy's :class:`GenericComic` renders from."""
        if not creators:
            return None

        grouped: dict[str, list[str]] = {}
        for creator in creators:
            role = (creator or {}).get("role")
            name = (creator or {}).get("name")
            if role and name and name not in grouped.setdefault(role, []):
                grouped[role].append(name)
        return grouped or None


def serialize_resource_id_from_brand(bound_args: dict) -> str:
    """Return the cache key of the Brand `item` from the bound args of ComicCache.set."""
    item: Brand = bound_args["item"]
    return f"comic:{item}"


class ComicCache:
    """Cache for the Comics cog.

    This class is used to store the Comics for a Brand.
    """

    def __init__(self) -> None:
        self._internal_cache: dict[Brand, list[GenericComic]] = {}

    def __repr__(self) -> str:
        return f"<ComicCache len={len(self._internal_cache)}>"

    def reset(self) -> None:
        """Clear the internal cache."""
        self._internal_cache.clear()

    @lock("ComicCache.set", serialize_resource_id_from_brand, wait=True)
    async def set(self, item: Brand, value: list[GenericComic]) -> None:
        """Set the Comics `value` for the brand `item`."""
        self._internal_cache.setdefault(item, [])
        self._internal_cache[item] = value

    def get(self, item: Brand) -> list[GenericComic] | None:
        """Return the Comics for the brand `item`."""
        if item in self._internal_cache:
            return self._internal_cache[item]
        return None

    def delete(self, item: Brand) -> bool:
        """Delete the Comics for the brand `item`."""
        if item in self._internal_cache:
            del self._internal_cache[item]
            return True
        return False


def extract_authors(text: str) -> dict[str, list[str]]:
    author_pattern = re.compile(r"(?P<position>[^,]+) by (?P<name>[^,]+)")
    authors = author_pattern.findall(text)

    author_dict = {}
    for position, name in authors:
        author_dict[position] = [name.strip()]

    return author_dict


def remove_html_tags(content: str) -> str:
    """Removes HTML tags from a string."""
    clean_text = re.sub("<.*?>", "", content)  # Remove HTML tags
    clean_text = re.sub(r"\s+", " ", clean_text)  # Remove extra whitespace
    return clean_text


class Parser:
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Percy/1.0 (https://github.com/klappstuhlpy/Percy)"}
    """Parser object for comic fetching."""

    VIZ_ENDPOINT: ClassVar[str] = "https://www.viz.com/calendar/{year}/{month}"
    RAW_VIZ_ENDPOINT: ClassVar[str] = "https://www.viz.com"

    @classmethod
    @executor
    def _bs4_parse_viz(cls, text: str, *, index: int, element: str) -> GenericComic | None:
        soup = BeautifulSoup(text, "html.parser")
        base_prd = soup.find("h2", class_="type-lg type-xl--md line-solid weight-bold mar-b-md mar-b-lg--md")
        if base_prd is None:
            return None

        title = remove_html_tags(base_prd.text)
        _img_tag = soup.find("div", class_="product-image mar-x-auto mar-b-lg pad-x-md")
        image_url: str | None = str(_img_tag.find("img").get("src")) if _img_tag else None  # type: ignore[union-attr]

        store_table = soup.find("table", class_="purchase-table")
        try:
            price = store_table.find("span").text[1:]  # type: ignore[union-attr]
        except (KeyError, AttributeError):
            price = None
        if isinstance(price, str) and price.endswith("*"):
            price = price[:-1]

        try:
            price = float(price) if price else 0.0
        except (ValueError, TypeError):
            price = 0.0

        base_obj = soup.find("div", class_="o_geo-block")
        if base_obj:
            price_note: str | None = (
                base_obj.find(  # type: ignore[union-attr]
                    "p", class_="mar-t-rg"
                ).text
                if base_obj.find("p", class_="mar-t-rg")
                else None
            )
        else:
            price_note = "N/A"

        info_table = soup.find("div", class_="row pad-b-xl")
        if info_table is None:
            return None

        desc_tag = info_table.find("p")
        if desc_tag is None:
            return None

        desc = remove_html_tags(desc_tag.text.strip())
        authors_tag = info_table.find("div", class_="mar-b-md")
        authors = extract_authors(authors_tag.text) if authors_tag else {}
        release_tag = info_table.find("div", class_="o_release-date mar-b-md")
        if release_tag is None:
            return None
        release_date = release_tag.text.replace("Release", "")
        isbn = (
            info_table.find("div", class_="o_isbn13 mar-b-md").text
            if info_table.find("div", class_="o_isbn13 mar-b-md")
            else None
        )
        trim_size = (
            info_table.find("div", class_="o_trimsize mar-b-md").text
            if info_table.find("div", class_="o_trimsize mar-b-md")
            else None
        )

        spec_table = soup.find("div", class_="g-6--md g-omega--md")
        result = {}
        for i in spec_table.find_all("div", class_="mar-b-md") if spec_table else []:
            clean_text = remove_html_tags(i.text.strip())
            spec_map = ["Length", "Series", "Category", "Age Rating"]
            for spec in spec_map:
                if clean_text.startswith(spec):
                    value = clean_text.replace(spec, "").strip()
                    result[spec] = value

        if result.get("Category") in ["TV Series", "Movie"]:
            return None

        page_info = result.get("Length", "")
        page_count: int | None = None
        if page_info:
            digits = "".join(c for c in page_info if c.isdigit())
            page_count = int(digits) if digits else None

        return GenericComic(
            brand=Brand.MANGA,
            _id=index,
            title=title,
            description=desc,
            creators=authors,
            image_url=image_url,
            url=element,
            page_count=page_count,
            price=float(price),
            _copyright=f"© {datetime.datetime.now(datetime.UTC).year} VIZ Media, LLC. All rights reserved.",
            date=utcparse(release_date),
            isbn=isbn,
            trim_size=trim_size,
            price_note=price_note,
            category=result.get("Category"),
            age_rating=result.get("Age Rating"),
        )

    @classmethod
    async def bs4_viz(cls) -> list[GenericComic]:
        ref = cls.VIZ_ENDPOINT.format(year=datetime.datetime.now(datetime.UTC).year, month=datetime.datetime.now(datetime.UTC).month)

        mangas = []
        async with aiohttp.ClientSession() as session:
            async with session.get(ref, headers=cls.HEADERS) as resp:
                soup = BeautifulSoup(await resp.text(), "html.parser")
                elements = [
                    urljoin(cls.RAW_VIZ_ENDPOINT, i.get("href"))  # type: ignore[arg-type]
                    for i in soup.find_all("a", class_="product-thumb ar-inner type-center")
                ]

            for index, element in enumerate(elements):
                async with session.get(element, headers=cls.HEADERS) as resp:
                    _cs_manga = await cls._bs4_parse_viz(await resp.text(), index=index, element=element)
                    if _cs_manga is not None:
                        mangas.append(_cs_manga)

        return mangas
