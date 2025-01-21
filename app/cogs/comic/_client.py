from __future__ import annotations

import asyncio
import datetime
import hashlib
from typing import TYPE_CHECKING, Any, TypeVar, Generic

import discord
import yarl

from app.utils import utcparse
from app.utils.lock import lock
from config import marvel as marvel_config

if TYPE_CHECKING:
    from app.core import Bot

K_T = TypeVar('K_T', bound=dict[str, Any])


class MarvelError(discord.HTTPException):
    """Base exception class for all Marvel errors."""
    pass


class Marvel:
    """A client for the Marvel API.

    Attributes
    ----------
    BASE_URL: str
        The base URL for the Marvel API.
    """

    BASE_URL = 'http://gateway.marvel.com/v1/public/'

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.config = marvel_config

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

        req_url = yarl.URL(self.BASE_URL) / url

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        params.update(data)

        async with self.bot.session.request(method, req_url, params=params, headers=headers) as r:
            remaining = r.headers.get('X-Ratelimit-Remaining')
            js = await r.json()
            if r.status == 429 or remaining == '0':
                delta = discord.utils._parse_ratelimit_header(r)
                await asyncio.sleep(delta)
                return await self.request(method, url, data=data, headers=headers)
            elif 300 > r.status >= 200:
                return js
            else:
                raise MarvelError(r, js['message'])

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
    def str_to_datetime(text: str) -> datetime:
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
    def date(self) -> datetime:
        return self.str_to_datetime(self.data['date'])

    @property
    def price(self) -> float:
        return float(self.data['price'])

    @property
    def ex_data(self) -> DataContainer:
        return DataContainer(self.marvel, self.data['data'])


class DataContainer(MarvelObject, Generic[K_T]):
    """Base DataContainer"""

    data: K_T

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
        return self.data[0]

    @property
    def results(self) -> list[Comic]:
        return [Comic(self.marvel, comic) for comic in self.data['results']]


class List(MarvelObject, Generic[K_T]):
    """Base List object"""

    data: K_T

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


class Summary(MarvelObject, Generic[K_T]):
    """Base Summary object"""

    data: K_T

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


class TextObject(MarvelObject, Generic[K_T]):
    """Base TextObject object"""

    data: K_T

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


class Image(MarvelObject, Generic[K_T]):
    """Base Image object"""

    data: K_T

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


class Comic(MarvelObject, Generic[K_T]):
    """Comic object"""

    ENDPOINT: str = 'comics'
    data: dict[str, K_T]

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
    def modified(self) -> datetime:
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


class Creator(MarvelObject, Generic[K_T]):
    """Creator object for Marvel API."""

    ENDPOINT: str = 'creators'
    data: dict[str, K_T]

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
    def modified(self) -> datetime:
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
