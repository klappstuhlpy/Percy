from __future__ import annotations

import datetime
import hashlib
import re
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from app.clients import BaseHTTPClient, HTTPClientError
from app.utils import executor, utcparse
from app.utils.lock import lock
from config import marvel as marvel_config

from .models import Brand, GenericComic

if TYPE_CHECKING:
    from app.core import Bot


class MarvelError(HTTPClientError):
    """Base exception class for all Marvel errors."""
    pass


class Marvel(BaseHTTPClient):
    """A client for the Marvel API.

    Inherits rate-limit retries, transport-error backoff and circuit-breaking from
    :class:`~app.clients.BaseHTTPClient`; this class only owns request signing (the
    timestamp/hash Marvel requires) and surfaces failures as :class:`MarvelError`.
    """

    BASE_URL = 'http://gateway.marvel.com/v1/public/'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot.session, name='Marvel')
        self.bot: Bot = bot
        self.config = marvel_config

    def _should_retry(self, response: aiohttp.ClientResponse, payload: Any) -> bool:
        # Marvel signals an exhausted window with X-Ratelimit-Remaining: 0 even on a 200;
        # back off in that case too so the next call doesn't immediately get a 429.
        return response.status == 429 or response.headers.get('X-Ratelimit-Remaining') == '0'

    def _build_error(self, response: aiohttp.ClientResponse, payload: Any) -> HTTPClientError:
        message = payload.get('message') if isinstance(payload, dict) else str(payload)
        return MarvelError(response, message)

    @lock('Marvel', 'request', wait=True)
    async def request(
            self,
            method: str,
            url: str,
            *,
            data: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
    ) -> Any:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d%H:%M:%S %Z')
        params = {
            'apikey': self.config.public_key,
            'ts': timestamp,
            'hash': hashlib.md5(
                f'{timestamp}{self.config.private_key}{self.config.public_key}'.encode()
            ).hexdigest()
        }

        hdrs = {
            'Accept': 'application/json'
        }

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        if data is not None:
            params.update(data)

        return await self.fetch(method, url, params=params, headers=hdrs)

    async def get_comic(self, _id: int) -> DataWrapper:
        """Fetches a single comic by id."""

        data = await self.request('GET', 'comics', data={'id': _id})
        return DataWrapper(self, data)

    async def get_comics(self, **kwargs: Any) -> DataWrapper:
        """Fetches list of comics."""

        data = await self.request('GET', 'comics', data=kwargs)
        return DataWrapper(self, data)


class MarvelObject:
    """Base class for all Marvel API classes"""

    def __init__(self, marvel: Marvel, data: dict) -> None:
        self.marvel: Marvel = marvel
        self.data: dict = data

    @staticmethod
    def str_to_datetime(text: str) -> datetime.datetime:
        """Converts string to datetime object"""
        return utcparse(text)


class DataWrapper(MarvelObject):
    """Base DataWrapper"""

    def __init__(self, marvel: Marvel, data: dict, params: Any = None) -> None:
        super().__init__(marvel, data)
        self.params = params

    @property
    def code(self) -> int:
        """The HTTP status code of the returned result."""
        return int(self.data['code'])

    @property
    def status(self) -> str:
        """A string description of the call status."""
        return self.data['status']

    @property
    def etag(self) -> str:
        """ A digest value of the content returned by the call."""
        return self.data['etag']

    @property
    def type(self) -> str:
        return self.data['type']

    @property
    def date(self) -> datetime.datetime:
        return self.str_to_datetime(self.data['date'])

    @property
    def price(self) -> float:
        return float(self.data['price'])

    @property
    def ex_data(self) -> DataContainer:
        return DataContainer(self.marvel, self.data['data'])


class DataContainer[K_T: dict[str, Any]](MarvelObject):
    """Base DataContainer"""

    data: K_T  # type: ignore[assignment]

    def __init__(self, marvel: Marvel, data: K_T) -> None:
        super().__init__(marvel, data)

    @property
    def offset(self) -> int:
        """The requested offset (number of skipped results) of the call."""
        return int(self.data['offset'])

    @property
    def limit(self) -> int:
        """The requested result limit."""
        return int(self.data['limit'])

    @property
    def total(self) -> int:
        """The total number of resources available given the current filter set."""
        return int(self.data['total'])

    @property
    def count(self) -> int:
        """The total number of results returned by this call."""
        return int(self.data['count'])

    @property
    def result(self) -> MarvelObject:
        """Returns the first item in the results list.
        Useful for methods that should return only one results. """
        return self.data['results'][0]

    @property
    def results(self) -> list[Comic]:
        return [Comic(self.marvel, comic) for comic in self.data['results']]


class List[K_T: dict[str, Any]](MarvelObject):
    """Base List object"""

    data: K_T  # type: ignore[assignment]

    @property
    def available(self) -> int:
        """The number of total available resources in this list. Will always be greater
        than or equal to the 'returned' value."""
        return int(self.data['available'])

    @property
    def returned(self) -> int:
        """The number of resources returned in this collection (up to 20)."""
        return int(self.data['returned'])

    @property
    def collectionURI(self) -> str:
        """The path to the full list of resources in this collection."""
        return self.data['collectionURI']

    @property
    def items(self) -> list[Summary]:
        """Returns List of ComicSummary objects"""
        return [Summary(self.marvel, item) for item in self.data['items']]


class Summary[K_T: dict[str, Any]](MarvelObject):
    """Base Summary object"""

    data: K_T  # type: ignore[assignment]

    @property
    def resourceURI(self) -> str:
        """The path to the individual resource."""
        return self.data['resourceURI']

    @property
    def name(self) -> str:
        """The canonical name of the resource."""
        return self.data['name']

    @property
    def role(self) -> str:
        """The role of the creator in the parent entity."""
        return self.data['role']


class TextObject[K_T: dict[str, Any]](MarvelObject):
    """Base TextObject object"""

    data: K_T  # type: ignore[assignment]

    @property
    def type(self) -> str:
        """The canonical type of the text object (e.g. solicit text, preview text, etc.)."""
        return self.data['type']

    @property
    def language(self) -> str:
        """The IETF language tag denoting the language the text object is written in."""
        return self.data['language']

    @property
    def text(self) -> str:
        """The text."""
        return self.data['text']


class Image[K_T: dict[str, Any]](MarvelObject):
    """Base Image object"""

    data: K_T  # type: ignore[assignment]

    @property
    def path(self) -> str:
        """The directory path of to the image."""
        return self.data['path']

    @property
    def extension(self) -> str:
        """The file extension for the image. """
        return self.data['extension']

    def __repr__(self) -> str:
        return f'{self.path}.{self.extension}'


class Comic[K_T: dict[str, Any]](MarvelObject):
    """Comic object"""

    ENDPOINT: str = 'comics'
    data: dict[str, K_T]  # type: ignore[assignment]

    @property
    def id(self) -> int:
        return self.data['id']

    @property
    def title(self) -> str:
        return self.data['title']

    @property
    def issueNumber(self) -> float:
        return float(self.data['issueNumber'])

    @property
    def variantDescription(self) -> str:
        return self.data['description']

    @property
    def description(self) -> str:
        return self.data['description']

    @property
    def modified(self) -> datetime.datetime:
        return self.str_to_datetime(self.data['modified'])

    @property
    def modified_raw(self) -> str:
        return self.data['modified']

    @property
    def isbn(self) -> str:
        return self.data['isbn']

    @property
    def upc(self) -> str:
        return self.data['upc']

    @property
    def diamondCode(self) -> str:
        return self.data['diamondCode']

    @property
    def ean(self) -> str:
        return self.data['ean']

    @property
    def issn(self) -> str:
        return self.data['issn']

    @property
    def format(self) -> str:
        return self.data['format']

    @property
    def pageCount(self) -> int:
        return int(self.data['pageCount'])

    @property
    def textObjects(self) -> list[TextObject]:
        return [TextObject(self.marvel, text_object) for text_object in self.data['textObjects']]

    @property
    def resourceURI(self) -> str:
        return self.data['resourceURI']

    @property
    def urls(self) -> list:
        return self.data['urls']

    @property
    def series(self) -> str:
        return self.data['series']

    @property
    def thumbnail(self) -> Image:
        return Image(self.marvel, self.data['thumbnail'])

    @property
    def images(self) -> list[Image]:
        return [Image(self.marvel, image) for image in self.data['images']]

    @property
    def creators(self) -> List[DataWrapper]:
        return List(self.marvel, self.data['creators'])

    @property
    def dates(self) -> list[DataWrapper]:
        return [DataWrapper(self.marvel, date) for date in self.data['dates']]

    @property
    def prices(self) -> list[DataWrapper]:
        return [DataWrapper(self.marvel, price) for price in self.data['prices']]


class Creator[K_T: dict[str, Any]](MarvelObject):
    """Creator object for Marvel API."""

    ENDPOINT: str = 'creators'
    data: dict[str, K_T]  # type: ignore[assignment]

    @property
    def id(self) -> int:
        return int(self.data['id'])

    @property
    def firstName(self) -> str:
        return self.data['firstName']

    @property
    def middleName(self) -> str:
        return self.data['middleName']

    @property
    def lastName(self) -> str:
        return self.data['lastName']

    @property
    def suffix(self) -> str:
        return self.data['suffix']

    @property
    def fullName(self) -> str:
        return self.data['fullName']

    @property
    def modified(self) -> datetime.datetime:
        return self.str_to_datetime(self.data['modified'])

    @property
    def modified_raw(self) -> str:
        return self.data['modified']

    @property
    def resourceURI(self) -> str:
        return self.data['resourceURI']

    @property
    def urls(self) -> str:
        return self.data['urls']

    @property
    def thumbnail(self) -> str:
        return '{}.{}'.format(self.data['thumbnail']['path'], self.data['thumbnail']['extension'])

    @property
    def comics(self) -> List:
        """Returns ComicList object"""
        return List(self.marvel, self.data['comics'])


def serialize_resource_id_from_brand(bound_args: dict) -> str:
    """Return the cache key of the Brand `item` from the bound args of ComicCache.set."""
    item: Brand = bound_args['item']
    return f'comic:{item}'


class ComicCache:
    """Cache for the Comics cog.

    This class is used to store the Comics for a Brand.
    """

    def __init__(self) -> None:
        self._internal_cache: dict[Brand, list[GenericComic]] = {}

    def __repr__(self) -> str:
        return f'<ComicCache len={len(self._internal_cache)}>'

    def reset(self) -> None:
        """Clear the internal cache."""
        self._internal_cache.clear()

    @lock('ComicCache.set', serialize_resource_id_from_brand, wait=True)
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
    author_pattern = re.compile(r'(?P<position>[^,]+) by (?P<name>[^,]+)')
    authors = author_pattern.findall(text)

    author_dict = {}
    for position, name in authors:
        author_dict[position] = [name.strip()]

    return author_dict


def from_destination(c: str, details: dict[str, list]) -> str | None:
    return str(details[c][0]) if c in details else None


def remove_html_tags(content: str) -> str:
    """Removes HTML tags from a string."""
    clean_text = re.sub('<.*?>', '', content)  # Remove HTML tags
    clean_text = re.sub(r'\s+', ' ', clean_text)  # Remove extra whitespace
    return clean_text


class Parser:
    """Parser object for comic fetching."""

    DC_ENDPOINT: ClassVar[str] = 'https://www.dc.com'
    VIZ_ENDPOINT: ClassVar[str] = 'https://www.viz.com/calendar/{year}/{month}'
    RAW_VIZ_ENDPOINT: ClassVar[str] = 'https://www.viz.com'

    @classmethod
    @executor
    def _bs4_parse_dc(cls, text: str, *, index: int, element: str) -> GenericComic | None:
        soup = BeautifulSoup(text, 'html.parser')
        base_prd = soup.find('h2', class_='type-lg type-xl--md line-solid weight-bold mar-b-md mar-b-lg--md')
        if base_prd is None:
            return None

        title = remove_html_tags(base_prd.text)
        _img_tag = soup.find('div', class_='product-image mar-x-auto mar-b-lg pad-x-md')
        image_url: str | None = str(_img_tag.find('img').get('src')) if _img_tag else None  # type: ignore[union-attr]

        store_table = soup.find('table', class_='purchase-table')
        try:
            price = store_table.find('span').text[1:]  # type: ignore[union-attr]
        except (KeyError, AttributeError):
            price = 'N/A'
        if price.endswith('*'):
            price = price[:-1]

        if isinstance(price, str):
            price = 0.0

        base_obj = soup.find('div', class_='o_geo-block')
        if base_obj:
            price_note: str | None = base_obj.find(  # type: ignore[union-attr]
                'p', class_='mar-t-rg').text if base_obj.find('p', class_='mar-t-rg') else None
        else:
            price_note = 'N/A'

        info_table = soup.find('div', class_='row pad-b-xl')
        desc = remove_html_tags(info_table.find('p').text.strip())  # type: ignore[union-attr]
        authors = extract_authors(info_table.find('div', class_='mar-b-md').text)  # type: ignore[union-attr]
        release_date = info_table.find('div', class_='o_release-date mar-b-md').text.replace('Release', '')  # type: ignore[union-attr]
        isbn = info_table.find('div', class_='o_isbn13 mar-b-md').text if info_table.find(  # type: ignore[union-attr]
            'div', class_='o_isbn13 mar-b-md') else None
        trim_size = info_table.find('div', class_='o_trimsize mar-b-md').text if info_table.find(  # type: ignore[union-attr]
            'div', class_='o_trimsize mar-b-md') else None

        spec_table = soup.find('div', class_='g-6--md g-omega--md')
        result = {}
        for i in spec_table.find_all('div', class_='mar-b-md'):  # type: ignore[union-attr]
            clean_text = remove_html_tags(i.text.strip())
            spec_map = ['Length', 'Series', 'Category', 'Age Rating']
            for spec in spec_map:
                if clean_text.startswith(spec):
                    value = clean_text.replace(spec, "").strip()
                    result[spec] = value

        if result.get('Category') in ['TV Series', 'Movie']:
            return None

        page_info = result.get('Length', 'N/A Pages')[0]
        page_count = int(page_info) if not isinstance(page_info, str) else None

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
            _copyright=f'© {datetime.datetime.now().year} VIZ Media, LLC. All rights reserved.',
            date=utcparse(release_date),
            # kwargs
            isbn=isbn,
            trim_size=trim_size,
            price_note=price_note,
            category=result.get('Category'),
            age_rating=result.get('Age Rating')
        )

    @classmethod
    async def bs4_viz(cls) -> list[GenericComic]:
        ref = cls.VIZ_ENDPOINT.format(year=datetime.datetime.now().year, month=datetime.datetime.now().month)

        mangas = []
        async with aiohttp.ClientSession() as session:
            async with session.get(ref) as resp:
                soup = BeautifulSoup(await resp.text(), 'html.parser')
                elements = [
                    urljoin(cls.RAW_VIZ_ENDPOINT, i.get('href'))  # type: ignore[arg-type]
                    for i in soup.find_all('a', class_='product-thumb ar-inner type-center')
                ]

            for index, element in enumerate(elements):
                async with session.get(element) as resp:
                    _cs_manga = await cls._bs4_parse_dc(await resp.text(), index=index, element=element)
                    if _cs_manga is not None:
                        mangas.append(_cs_manga)

        return mangas

    @classmethod
    async def bs4_dc(cls) -> list[GenericComic]:
        async with aiohttp.ClientSession() as session:
            async with session.get(cls.DC_ENDPOINT + '/comics') as resp:
                if resp.status != 200:
                    resp.raise_for_status()

                page = await resp.text()

            soup = BeautifulSoup(page, 'html.parser')
            comics = []
            links: Tag = soup.find('ul', class_='react-multi-carousel-track content-tray-slider')  # type: ignore[assignment]

            for item in links.contents:
                branch = item.findNext(class_='card-button usePointer').get('href')  # type: ignore[union-attr]
                link = cls.DC_ENDPOINT + branch

                async with session.get(link) as resp:
                    if resp.status != 200:
                        resp.raise_for_status()

                    page = await resp.text()

                if page is None:
                    continue

                soup = BeautifulSoup(page, 'html.parser')
                txt = soup.find_all(class_='sc-g8nqnn-0')
                if not txt:
                    continue

                c_type = ''.join(txt[0].find('p', class_='text-left').contents).strip()  # type: ignore[union-attr, arg-type]
                if c_type != 'COMIC BOOK':
                    continue

                title = ''.join(txt[0].find('h1', class_='text-left').contents).strip()  # type: ignore[union-attr, arg-type]

                desc = None
                if len(txt) > 1 and txt[1].find('p'):
                    desc_list = cls._get_desc(txt[1])
                    desc = '\n'.join(i.strip() for i in ''.join(desc_list).split('\n') if i.strip())

                details_list = [i.contents for i in soup.find_all('div', class_='sc-b3fnpg-3')]
                details = {}
                for d in details_list:
                    for dd in d:
                        d_id = dd['id'][len('page151-band11690-Subitem2847'):]  # type: ignore[index]
                        x = None
                        if '-' in d_id:
                            d_id, x = d_id.split('-')
                            x = None if d_id not in ['24', '12'] else x
                        if d_id not in details:
                            details[d_id] = []
                        details[d_id] += [
                            i.contents[0].contents[0] if x else i.contents[0]  # type: ignore[union-attr]
                            for i in dd.contents if type(i) is Tag]

                creators = {}
                if '24' in details:
                    creators['Writer'] = [str(i) for i in details['24']]
                if '12' in details:
                    creators['Artist'] = [str(i) for i in details['12']]

                price = from_destination('33', details)
                price = 0.0 if price == 'FREE' else float(price) if price else None

                date = from_destination('36', details)
                date = utcparse(date) if date else None

                page_count = from_destination('48', details)

                img = soup.find('img', id='page151-band11672-Card11673-img')
                image = img['src'].split('?')[0] if img else None  # type: ignore[index]

                _copyright = str(
                    soup.find('div', class_='small legal d-inline-block').contents[0].contents[0]  # type: ignore
                )

                _cs_comic = GenericComic(
                    brand=Brand.DC,
                    _id=''.join(i for i in title if i.isalnum()),
                    title=title,
                    description=desc,
                    creators=creators,
                    image_url=image,
                    url=link,
                    page_count=int(page_count),
                    price=price,
                    _copyright=_copyright,
                    date=date
                )
                comics.append(_cs_comic)

        return comics

    @classmethod
    async def bs4_marvel(cls) -> dict[int, str]:
        descs: dict[int, str] = {}

        async with aiohttp.ClientSession() as session:
            async with session.get('https://marvel.com/comics/calendar/') as resp:
                if resp.status != 200:
                    resp.raise_for_status()
                page = await resp.text()

            soup = BeautifulSoup(page, 'html.parser')

            for link in soup.find_all('a', class_='meta-title'):
                plink = 'https:' + link.get('href').strip()  # type: ignore[operator, union-attr]
                _id = int(plink.strip('https://www.marvel.com/comics/issue/').split('/')[0])

                page = None
                for _ in range(10):
                    try:
                        async with session.get(plink) as resp:
                            if resp.status != 200:
                                resp.raise_for_status()
                            page = await resp.text()
                        break
                    except aiohttp.ClientPayloadError:
                        pass
                if page is None:
                    continue

                soup = BeautifulSoup(page, 'html.parser')
                try:
                    desc = next(i for i in soup.find_all('p') if 'data-blurb' in i.attrs).get_text().strip()
                except StopIteration:
                    continue

                descs[_id] = desc

        return descs

    @classmethod
    async def marvel_from_api(cls, client: Marvel) -> list[GenericComic]:
        raw = await client.get_comics(format='comic', noVariants='true', dateDescriptor='thisWeek', limit=100)
        m_copyright = raw.data['attributionText']

        comics = [cls._to_comic(c) for c in raw.ex_data.results]
        for c in comics:
            c.brand = Brand.MARVEL
            c.copyright = m_copyright

        return comics

    @classmethod
    def _to_comic(cls, data: Comic) -> GenericComic:
        _cs_comic = GenericComic(
            _id=data.id,
            title=data.title,
            page_count=data.pageCount,
            description=data.description,
        )

        _cs_comic.creators = {}
        _cs_comic.image_url = (
            data.images[0].path + '/clean.jpg'
            if data.images else 'https://klappstuhl.me/gallery/hopxF.jpeg')

        _cs_comic.url = next((i['url'] for i in data.urls if i['type'] == 'detail'), '') or ''
        _cs_comic.price = next((i.price for i in data.prices if i.type == 'printPrice'), None)
        _cs_comic.date = next((i.date for i in data.dates if i.type == 'onsaleDate'), None)

        for cr in data.creators.items:
            role = cr.role.title()
            if role not in _cs_comic.creators:
                _cs_comic.creators[role] = [cr.name]
            else:
                _cs_comic.creators[role].append(cr.name)
        return _cs_comic

    @classmethod
    def _get_desc(cls, tag: Tag) -> list[str]:
        strings = []
        for i in tag.contents:
            if isinstance(i, Tag):
                if i.name in ['p', 'em']:
                    strings += cls._get_desc(i)
            elif isinstance(i, NavigableString):
                s = str(i)
                if tag.name == 'em':
                    s = (' ' if s.startswith(' ') else "") + \
                        f'*{s.strip()}*' + \
                        (' ' if s.endswith(' ') else "")
                strings.append(s)
        return strings

    @classmethod
    async def fetch_marvel_lookup_table(cls, client: Marvel) -> list[GenericComic]:
        comics: list[GenericComic] = await cls.marvel_from_api(client)
        descs: dict[int, str] = await cls.bs4_marvel()

        for c in comics:
            if description := descs.get(c.id):
                c.description = description

        return comics
