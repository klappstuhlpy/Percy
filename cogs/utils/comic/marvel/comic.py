from __future__ import annotations
from datetime import datetime

from cogs.utils.comic.client import Summary, List, DataWrapper, DataContainer, MarvelObject, TextObject, Image


class ComicDataWrapper(DataWrapper):
    """Comic Data Wrapper"""

    @property
    def ex_data(self):
        return ComicDataContainer(self.marvel, self.data['data'])


class ComicDataContainer(DataContainer):

    @property
    def results(self):
        return [Comic(self.marvel, comic) for comic in self.data['results']]


class Comic(MarvelObject):
    """Comic object"""

    SPEC_ENDPOINT: str = 'comics'

    @property
    def id(self) -> int:
        return self.data['id']

    @property
    def digitalId(self) -> int:
        return int(self.data['digitalId'])

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
    def upc(self) -> str :
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
    def variants(self) -> list[ComicSummary]:
        return [ComicSummary(self.marvel, variant) for variant in self.data['variants']]

    @property
    def collections(self) -> list[ComicSummary]:
        return [ComicSummary(self.marvel, collection) for collection in self.data['collections']]

    @property
    def collectedIssues(self) -> list[ComicSummary]:
        return [ComicSummary(self.marvel, issue) for issue in self.data['collectedIssues']]

    @property
    def dates(self) -> list[ComicDate]:
        return [ComicDate(self.marvel, date) for date in self.data['dates']]

    @property
    def prices(self) -> list[ComicPrice]:
        return [ComicPrice(self.marvel, price) for price in self.data['prices']]

    @property
    def thumbnail(self) -> Image:
        return Image(self.marvel, self.data['thumbnail'])

    @property
    def images(self) -> list[Image]:
        return [Image(self.marvel, image) for image in self.data['images']]

    @property
    def creators(self):
        from cogs.utils.comic.marvel.creator import CreatorList
        return CreatorList(self.marvel, self.data['creators'])

    '''
    @property
    def characters(self):
        from cogs.utils.comic.container.character import CharacterList
        return CharacterList(self.marvel, self.data['characters'])

    @property
    def stories(self):
        from cogs.utils.comic.container.story import StoryList
        return StoryList(self.marvel, self.data['stories'])

    @property
    def events(self):
        from cogs.utils.comic.container.event import EventList
        return EventList(self.marvel, self.data['events'])

    async def get_creators(self, **kwargs):
        """
        Returns a full CreatorDataWrapper object for this character.

        GET -> /comics/{comicId}/creators
        """
        from cogs.utils.comic.container.creator import CreatorDataWrapper, Creator
        return await self.get_related_resource(Creator, CreatorDataWrapper, **kwargs)

    async def get_characters(self, **kwargs):
        """
        Returns a full CharacterDataWrapper object for this character.

        GET -> /comics/{comicId}/characters
        """
        from cogs.utils.comic.container.character import CharacterDataWrapper, Character
        return await self.get_related_resource(Character, CharacterDataWrapper, **kwargs)

    async def get_events(self, **kwargs):
        """
        Returns a full EventDataWrapper object this character.

        GET -> /comics/{comicID}/events
        """
        from cogs.utils.comic.container.event import EventDataWrapper, Event
        return await self.get_related_resource(Event, EventDataWrapper, **kwargs)

    async def get_stories(self, **kwargs):
        """
        Returns a full StoryDataWrapper object this comic.

        GET -> /comics/{comicId}/stories
        """
        from cogs.utils.comic.container.story import StoryDataWrapper, Story
        return await self.get_related_resource(Story, StoryDataWrapper, **kwargs)'''


class ComicList(List):
    """ComicList object"""

    @property
    def items(self) -> list[ComicSummary]:
        """Returns List of ComicSummary objects"""
        return [ComicSummary(self.marvel, item) for item in self.data['items']]


class ComicSummary(Summary):
    """CommicSummary object"""


class ComicDate(DataContainer):
    """ComicDate object  """

    @property
    def type(self):
        return self.data['type']

    @property
    def date(self):
        return self.str_to_datetime(self.data['date'])


class ComicPrice(MarvelObject):
    """ComicPrice object"""

    @property
    def type(self):
        return self.data['type']

    @property
    def price(self):
        return float(self.data['price'])
