from __future__ import annotations

import datetime

from cogs.utils.comic.client import Summary, List, DataWrapper, DataContainer, MarvelObject


class CreatorDataWrapper(DataWrapper):

    @property
    def ex_data(self):
        return CreatorDataContainer(self.marvel, self.data['data'])


class CreatorDataContainer(DataContainer):

    @property
    def results(self):
        return [Creator(self.marvel, creator) for creator in self.data['results']]


class Creator(MarvelObject):
    """Creator object
    Takes a dict of creator attrs"""

    SPEC_ENDPOINT: str = 'creators'

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
        return "%s.%s" % (self.data['thumbnail']['path'], self.data['thumbnail']['extension'])

    @property
    def comics(self):
        """Returns ComicList object"""
        from cogs.utils.comic.marvel.comic import ComicList
        return ComicList(self.marvel, self.data['comics'])

    '''@property
    def series(self):
        """Returns SeriesList object"""
        from cogs.utils.comic.container.series import SeriesList
        return SeriesList(self.marvel, self.data['series'])

    @property
    def stories(self):
        """Returns StoryList object"""
        from cogs.utils.comic.container.story import StoryList
        return StoryList(self.marvel, self.data['stories'])

    @property
    def events(self):
        """Returns EventList object"""
        from cogs.utils.comic.container.event import EventList
        return EventList(self.marvel, self.data['events'])

    async def get_comics(self, **kwargs):
        """
        Returns a full ComicDataWrapper object for this creator.

        GET -> /creators/{creatorId}/comics
        """
        from .comic import Comic, ComicDataWrapper
        return await self.get_related_resource(Comic, ComicDataWrapper, **kwargs)

    async def get_events(self, **kwargs):
        """
        Returns a full EventDataWrapper object for this creator.

        GET -> /creators/{creatorId}/events
        """
        from cogs.utils.comic.container.event import EventDataWrapper, Event
        return await self.get_related_resource(Event, EventDataWrapper, **kwargs)

    async def get_series(self, **kwargs):
        """
        Returns a full SeriesDataWrapper object for this creator.

        GET -> /creators/{creatorId}/series
        """
        from cogs.utils.comic.container.series import Series, SeriesDataWrapper
        return await self.get_related_resource(Series, SeriesDataWrapper, **kwargs)

    async def get_stories(self, **kwargs):
        """
        Returns a full StoryDataWrapper object for this creator.

        GET -> /creators/{creatorId}/stories
        """
        from cogs.utils.comic.container.story import StoryDataWrapper, Story
        return await self.get_related_resource(Story, StoryDataWrapper, ** kwargs)'''


class CreatorList(List):
    """CreatorList object"""

    @property
    def items(self) -> list[CreatorSummary]:
        return [CreatorSummary(self.marvel, item) for item in self.data['items']]


class CreatorSummary(Summary):
    """CreatorSummary object"""

    @property
    def role(self):
        return self.data['role']
