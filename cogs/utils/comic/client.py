from __future__ import annotations

from typing import Any, TYPE_CHECKING
from urllib.parse import urlencode
import hashlib
import datetime

import aiohttp

if TYPE_CHECKING:
    from bot import Percy


DEFAULT_API_VERSION = 'v1'


class Marvel(object):
    """An Object-Oriented wrapper for the Marvel API"""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.config = self.bot.config.marvel

    @property
    def _endpoint(self) -> str:
        return f'http://gateway.marvel.com/{DEFAULT_API_VERSION}/public/'

    @property
    def _auth(self) -> str:
        """Creates hash from api keys and returns all required parametsrs"""
        ts = datetime.datetime.now().strftime("%Y-%m-%d%H:%M:%S")
        hash_string = hashlib.md5(("%s%s%s" % (ts, self.config.private_key, self.config.public_key)).encode('utf-8')).hexdigest()
        return "ts=%s&apikey=%s&hash=%s" % (ts, self.config.public_key, hash_string)

    async def _call(self, resource_url, params=None) -> dict:
        """Calls the Marvel API endpoint"""

        url = "%s%s" % (self._endpoint, resource_url)
        if params:
            url += "?%s&%s" % (params, self._auth)
        else:
            url += "?%s" % self._auth

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    resp.raise_for_status()

                urlfinal = await resp.json()
        return urlfinal

    def _params(self, params) -> str:
        """Takes dictionary of parameters and returns urlencoded string"""
        return urlencode(params)

    async def get_comic(self, _id: int):
        """Fetches a single comic by id.

        GET -> /v1/public/comics/{comicId}
        """

        from cogs.utils.comic.marvel.comic import Comic, ComicDataWrapper
        url = "%s/%s" % (Comic.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return ComicDataWrapper(self, response)

    async def get_comics(self, **kwargs):
        """Fetches list of comics.

        GET -> /v1/public/comics
        """

        from cogs.utils.comic.marvel.comic import Comic, ComicDataWrapper
        response = await self._call(Comic.SPEC_ENDPOINT, self._params(kwargs))
        return ComicDataWrapper(self, response)

    '''async def get_character(self, _id: int):
        """Fetches a single character by id.

        GET -> /v1/public/characters
        """

        from cogs.utils.comic.container.character import Character, CharacterDataWrapper
        url = "%s/%s" % (Character.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return CharacterDataWrapper(self, response)

    async def get_characters(self, **kwargs):
        """Fetches lists of comic characters with optional filters.

        GET -> /v1/public/characters/{characterId}
        """

        from cogs.utils.comic.container.character import Character, CharacterDataWrapper
        response = await self._call(Character.SPEC_ENDPOINT, self._params(kwargs))
        return CharacterDataWrapper(self, response, kwargs)
    
    async def get_creator(self, _id: int):
        """Fetches a single creator by id.

        GET -> /v1/public/creators/{creatorId}
        """

        from cogs.utils.comic.container.creator import Creator, CreatorDataWrapper
        url = "%s/%s" % (Creator.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return CreatorDataWrapper(self, response)

    async def get_creators(self, **kwargs):
        """Fetches lists of creators.

        GET -> /v1/public/creators
        """

        from cogs.utils.comic.container.creator import Creator, CreatorDataWrapper
        response = await self._call(Creator.SPEC_ENDPOINT, self._params(kwargs))
        return CreatorDataWrapper(self, response)

    async def get_event(self, _id: int):
        """Fetches a single event by id.

        GET -> /v1/public/event/{eventId}
        """

        from cogs.utils.comic.container.event import Event, EventDataWrapper
        url = "%s/%s" % (Event.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return EventDataWrapper(self, response)

    async def get_events(self, **kwargs):
        """Fetches lists of events.

        GET -> /v1/public/events
        """

        from cogs.utils.comic.container.event import Event, EventDataWrapper
        response = await self._call(Event.SPEC_ENDPOINT, self._params(kwargs))
        return EventDataWrapper(self, response)

    async def get_single_series(self, _id: int):
        """Fetches a single comic series by id.

        GET -> /v1/public/series/{seriesId}
        """

        from cogs.utils.comic.container.series import Series, SeriesDataWrapper
        url = "%s/%s" % (Series.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return SeriesDataWrapper(self, response)

    async def get_series(self, **kwargs):
        """Fetches lists of events.

        GET -> /v1/public/events
        """

        from cogs.utils.comic.container.series import Series, SeriesDataWrapper
        response = await self._call(Series.SPEC_ENDPOINT, self._params(kwargs))
        return SeriesDataWrapper(self, response)

    async def get_story(self, _id: int):
        """Fetches a single story by id.

        GET -> /v1/public/stories/{storyId}
        """

        from cogs.utils.comic.container.story import Story, StoryDataWrapper
        url = "%s/%s" % (Story.SPEC_ENDPOINT, _id)
        response = await self._call(url)
        return StoryDataWrapper(self, response)

    async def get_stories(self, **kwargs):
        """Fetches lists of stories.

        GET -> /v1/public/stories
        """

        from cogs.utils.comic.container.story import Story, StoryDataWrapper
        response = await self._call(Story.SPEC_ENDPOINT, self._params(kwargs))
        return StoryDataWrapper(self, response)'''


class MarvelObject(object):
    """
    Base class for all Marvel API classes
    """

    def __init__(self, marvel: Marvel, data: dict):
        self.marvel: Marvel = marvel
        self.data: dict = data

    def __unicode__(self) -> str:
        try:
            return self.data['name']
        except:
            return self.data['title']

    async def get_related_resource(self, instance: MarvelObject | Any, instace_wrapper, **kwargs):
        """
        Takes a related resource Class
        and returns the related resource DataWrapper.
        For Example, Given a Character instance, return
        a ComicsDataWrapper related to that character.
        /character/{characterId}/comics

        :returns:  DataWrapper -- DataWrapper for requested Resource
        """
        url = "%s/%s/%s" % (instance.SPEC_ENDPOINT, instance.id, instance.resource_url)
        response = await self.marvel._call(url, self.marvel._params(kwargs))
        return instace_wrapper(self.marvel, response)

    @staticmethod
    def str_to_datetime(string) -> datetime:
        """Converts '2013-11-20T17:40:18-0500' format to 'datetime' object"""
        from datetime import datetime
        return datetime.strptime(string[:-6], '%Y-%m-%dT%H:%M:%S')


class DataWrapper(MarvelObject):
    """
    Base DataWrapper
    """

    def __init__(self, marvel: Marvel, data: dict, params=None):
        self.marvel: Marvel = marvel
        self.data: dict = data
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


class DataContainer(MarvelObject):
    """Base DataContainer"""

    def __init__(self, marvel: Marvel, data: dict):
        self.marvel: Marvel = marvel
        self.data: data = data

    @property
    def offset(self) -> int:
        """The requested offset (number of skipped results) of the call."""
        return int(self.data['offset'])

    @property
    def limit(self) -> int:
        """The requested result limit."""
        return int(self.data['limit'])

    @property
    def total(self):
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
        return self.results[0]


class List(MarvelObject):
    """Base List object"""

    @property
    def available(self) -> int:
        """The number of total available resources in this list. Will always be greater
        than or equal to the "returned" value."""
        return int(self.data['available'])

    @property
    def returned(self) -> int:
        """The number of resources returned in this collection (up to 20)."""
        return int(self.data['returned'])

    @property
    def collectionURI(self) -> str:
        """The path to the full list of resources in this collection."""
        return self.data['collectionURI']


class Summary(MarvelObject):
    """Base Summary object"""

    @property
    def resourceURI(self) -> str:
        """The path to the individual resource."""
        return self.data['resourceURI']

    @property
    def name(self) -> str:
        """The canonical name of the resource."""
        return self.data['name']


class TextObject(MarvelObject):
    """Base TextObject object"""

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


class Image(MarvelObject):
    """Base Image object"""

    @property
    def path(self) -> str:
        """The directory path of to the image."""
        return self.data['path']

    @property
    def extension(self) -> str:
        """The file extension for the image. """
        return self.data['extension']

    def __repr__(self):
        return "%s.%s" % (self.path, self.extension)
    