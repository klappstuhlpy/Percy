from __future__ import annotations

import asyncio
import collections
import logging
from collections import defaultdict, deque
from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, NamedTuple

from bs4 import BeautifulSoup

from app.utils import executor

from . import _cog, doc_cache
from ._parsing import get_field_markdown, get_symbol_markdown

if TYPE_CHECKING:
    from app.core import Bot

log = logging.getLogger(__name__)


class QueueItem(NamedTuple):
    """Contains a `_cog.DocItem` and the `BeautifulSoup` object needed to parse it."""

    doc_item: _cog.DocItem
    soup: BeautifulSoup

    def __eq__(self, other: QueueItem | _cog.DocItem) -> bool:
        if isinstance(other, _cog.DocItem):
            return self.doc_item == other
        return NamedTuple.__eq__(self, other)


class ParseResultFuture(asyncio.Future):
    """Future with metadata for the parser class.

    `user_requested` is set by the parser when a Future is requested by a user and moved to the front,
    allowing the futures to only be waited for when clearing if they were user requested.
    """

    def __init__(self) -> None:
        super().__init__()
        self.user_requested = False


class BatchParser:
    """Get the Markdown of all symbols on a page and send them to redis when a symbol is requested.

    DocItems are added through the `add_item` method which adds them to the `_page_doc_items` dict.
    `get_markdown` is used to fetch the Markdown; when this is used for the first time on a page,
    all the symbols are queued to be parsed to avoid multiple web requests to the same page.
    """

    if TYPE_CHECKING:
        bot: Bot
        queue: deque[QueueItem]
        _page_doc_items: dict[str, list[_cog.DocItem]]
        _item_futures: dict[_cog.DocItem, ParseResultFuture]
        __task: asyncio.Task | None

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot

        self.queue: deque[QueueItem] = collections.deque()
        self._page_doc_items: dict[str, list[_cog.DocItem]] = defaultdict(list)
        self._item_futures: dict[_cog.DocItem, ParseResultFuture] = defaultdict(ParseResultFuture)
        self.__task: asyncio.Task | None = None

    async def get_markdown(self, doc_item: _cog.DocItem) -> str | None:
        """|coro|

        If no symbols were fetched from `doc_item`s page before,
        the HTML has to be fetched and then all items from the page are put into the parse queue.

        Not safe to run while `self.clear` is running.

        Parameters
        ----------
        doc_item : _cog.DocItem
            The symbol to get the Markdown for.

        Returns
        -------
        str | None
            The Markdown of the symbol or None if the symbol could not be found.
        """
        if doc_item not in self._item_futures and doc_item not in self.queue:
            self._item_futures[doc_item].user_requested = True

            async with self.bot.session.get(doc_item.url, raise_for_status=True) as response:
                @executor
                def bs4(text: str) -> BeautifulSoup:
                    return BeautifulSoup(text, 'lxml')

                soup = await bs4(await response.text(encoding='utf8'))

            self.queue.extendleft(QueueItem(item, soup) for item in self._page_doc_items[doc_item.url])
            log.debug('Added items from %s to the parse queue.', doc_item.url)

            if self.__task is None:
                self.__task = self.bot.loop.create_task(self._parse_queue(), name='Doc Item parsing Queue')
        else:
            self._item_futures[doc_item].user_requested = True
        with suppress(ValueError):
            self._move_to_front(doc_item)
        return await self._item_futures[doc_item]

    async def _parse_queue(self) -> None:
        """|coro|

        Parse all items from the queue, setting their result Markdown on the futures and sending them to redis.
        The coroutine will run as long as the queue is not empty, resetting `self.__task` to None when finished.
        """
        log.debug('Starting queue parsing.')
        try:
            while self.queue:
                item, soup = self.queue.pop()  # type: _cog.DocItem, BeautifulSoup
                markdown = None

                if (future := self._item_futures[item]).done():
                    continue

                try:
                    fields_markdown = await get_field_markdown(soup, item)
                    markdown = await get_symbol_markdown(soup, item)
                    if markdown is not None:
                        item.resolved_fields = fields_markdown
                        await doc_cache.set(item, markdown)
                except Exception:
                    log.exception('Unexpected error when handling %s.', item)
                future.set_result(markdown)
                del self._item_futures[item]
                await asyncio.sleep(0.1)
        finally:
            self.__task = None
            log.debug('Finished parsing queue.')

    def _move_to_front(self, item: QueueItem | _cog.DocItem) -> None:
        """Move `item` to the front of the parse queue."""
        item_index = self.queue.index(item)
        queue_item = self.queue[item_index]
        del self.queue[item_index]

        self.queue.append(queue_item)
        log.debug('Moved %s to the front of the queue.', item)

    def add_item(self, doc_item: _cog.DocItem) -> None:
        """Map a _cog.DocItem to its page so that the symbol will be parsed once the page is requested."""
        self._page_doc_items[doc_item.url].append(doc_item)

    async def remove(self, doc_key: str) -> None:
        """|coro|

        Remove all items from the queue that are from the page with the given key.

        Parameters
        ----------
        doc_key : str
            The URL of the page to remove all items from.
        """
        for item in filter(lambda i: i.doc_item.url == doc_key, self.queue):
            self.queue.remove(item)
            del self._item_futures[item.doc_item]
        del self._page_doc_items[doc_key]

    async def clear(self) -> None:
        """|coro|

        Clear all internal symbol data.
        Wait for all user-requested symbols to be parsed before clearing the parser.
        """
        for future in filter(attrgetter('user_requested'), self._item_futures.values()):
            await future

        if self.__task is not None:
            self.__task.cancel()
            self.__task = None

        self.queue.clear()
        self._page_doc_items.clear()
        self._item_futures.clear()
