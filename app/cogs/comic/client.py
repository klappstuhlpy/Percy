from __future__ import annotations

import datetime
import logging
import re
from typing import ClassVar
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from app.utils import executor, utcparse
from app.utils.lock import lock

from .models import Brand, GenericComic

log = logging.getLogger(__name__)


class LOCGClient:
    """Client for the self-hosted League of Comic Geeks API wrapper.

    Calls the comic-api Express service to fetch weekly Marvel/DC releases.
    """

    def __init__(self, session: aiohttp.ClientSession, *, base_url: str = "http://127.0.0.1:8070") -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def fetch_comics(self, publisher: str, *, date: str | None = None) -> list[GenericComic]:
        """Fetch comics for a publisher from the comic-api service.

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

        log.debug("Connecting to comic-api at %s (publisher=%s, date=%s)", url, publisher, date or "this week")
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.error("comic-api returned status %s for %s: %s", resp.status, publisher, text[:500])
                    raise RuntimeError(f"comic-api returned {resp.status}: {text}")
                data = await resp.json()
        except aiohttp.ClientConnectionError as exc:
            log.error("Failed to connect to comic-api at %s for %s: %s", self.base_url, publisher, exc)
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.error("comic-api request for %s failed: %s", publisher, exc)
            raise

        log.debug("Connected to comic-api; parsing %s response.", publisher)

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
                    # Enriched detail fields (present when comic-api ran with ?details=true).
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

        log.info("Fetched %d %s comic(s) from comic-api.", len(comics), publisher)
        return comics

    @staticmethod
    def _group_creators(creators: list[dict] | None) -> dict[str, list[str]] | None:
        """Collapse comic-api's flat ``[{name, role, url}]`` credits into the
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
            _copyright=f"© {datetime.datetime.now().year} VIZ Media, LLC. All rights reserved.",
            date=utcparse(release_date),
            isbn=isbn,
            trim_size=trim_size,
            price_note=price_note,
            category=result.get("Category"),
            age_rating=result.get("Age Rating"),
        )

    @classmethod
    async def bs4_viz(cls) -> list[GenericComic]:
        ref = cls.VIZ_ENDPOINT.format(year=datetime.datetime.now().year, month=datetime.datetime.now().month)

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
