from __future__ import annotations

import asyncio
import collections
from collections import defaultdict, deque
from contextlib import suppress
from operator import attrgetter
from typing import NamedTuple

from bs4 import BeautifulSoup
from pydis_core.utils import scheduling

from bot import Percy
from launcher import get_logger

from . import _cog, doc_cache
from ._parsing import get_symbol_markdown, get_field_markdown

log = get_logger(__name__)


class QueueItem(NamedTuple):
    """Contains a `DocItem` and the `BeautifulSoup` object needed to parse it."""

    doc_item: _cog.DocItem
    soup: BeautifulSoup

    def __eq__(self, other: QueueItem | _cog.DocItem):
        if isinstance(other, _cog.DocItem):
            return self.doc_item == other
        return NamedTuple.__eq__(self, other)


class ParseResultFuture(asyncio.Future):
    """
    Future with metadata for the parser class.

    `user_requested` is set by the parser when a Future is requested by an user and moved to the front,
    allowing the futures to only be waited for when clearing if they were user requested.
    """

    def __init__(self):
        super().__init__()
        self.user_requested = False


class BatchParser:
    """
    Get the Markdown of all symbols on a page and send them to redis when a symbol is requested.

    DocItems are added through the `add_item` method which adds them to the `_page_doc_items` dict.
    `get_markdown` is used to fetch the Markdown; when this is used for the first time on a page,
    all of the symbols are queued to be parsed to avoid multiple web requests to the same page.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._queue: deque[QueueItem] = collections.deque()
        self._page_doc_items: dict[str, list[_cog.DocItem]] = defaultdict(list)
        self._item_futures: dict[_cog.DocItem, ParseResultFuture] = defaultdict(ParseResultFuture)
        self._parse_task = None

    async def get_markdown(self, doc_item: _cog.DocItem) -> str | None:
        """
        Get the result Markdown of `doc_item`.

        If no symbols were fetched from `doc_item`s page before,
        the HTML has to be fetched and then all items from the page are put into the parse queue.

        Not safe to run while `self.clear` is running.
        """
        if doc_item not in self._item_futures and doc_item not in self._queue:
            self._item_futures[doc_item].user_requested = True

            async with self.bot.session.get(doc_item.url, raise_for_status=True) as response:
                soup = await self.bot.loop.run_in_executor(
                    None,
                    BeautifulSoup,
                    await response.text(encoding="utf8"),
                    "lxml",
                )

            self._queue.extendleft(QueueItem(item, soup) for item in self._page_doc_items[doc_item.url])
            log.debug(f"Added items from {doc_item.url} to the parse queue.")

            if self._parse_task is None:
                self._parse_task = scheduling.create_task(self._parse_queue(), name="Queue parse")
        else:
            self._item_futures[doc_item].user_requested = True
        with suppress(ValueError):
            self._move_to_front(doc_item)
        return await self._item_futures[doc_item]

    async def _parse_queue(self) -> None:
        """
        Parse all items from the queue, setting their result Markdown on the futures and sending them to redis.

        The coroutine will run as long as the queue is not empty, resetting `self._parse_task` to None when finished.
        """
        log.trace("Starting queue parsing.")
        try:
            while self._queue:
                item, soup = self._queue.pop()  # type: _cog.DocItem, BeautifulSoup
                markdown = None

                if (future := self._item_futures[item]).done():
                    continue

                try:
                    fields_mardown = await get_field_markdown(soup, item)
                    markdown = await get_symbol_markdown(soup, item)
                    if markdown is not None:
                        item.resolved_fields = fields_mardown
                        await doc_cache.set(item, markdown)
                except Exception:
                    log.exception(f"Unexpected error when handling {item}")
                future.set_result(markdown)
                del self._item_futures[item]
                await asyncio.sleep(0.1)
        finally:
            self._parse_task = None
            log.trace("Finished parsing queue.")

    def _move_to_front(self, item: QueueItem | _cog.DocItem) -> None:
        """Move `item` to the front of the parse queue."""
        item_index = self._queue.index(item)
        queue_item = self._queue[item_index]
        del self._queue[item_index]

        self._queue.append(queue_item)
        log.trace(f"Moved {item} to the front of the queue.")

    def add_item(self, doc_item: _cog.DocItem) -> None:
        """Map a DocItem to its page so that the symbol will be parsed once the page is requested."""
        self._page_doc_items[doc_item.url].append(doc_item)

    async def remove(self, doc_key: str) -> None:
        """
        Remove all items from the queue that are from the page with the given key.

        The key is the URL of the page.
        """
        for item in filter(lambda i: i.doc_item.url == doc_key, self._queue):
            self._queue.remove(item)
            del self._item_futures[item.doc_item]
        del self._page_doc_items[doc_key]

    async def clear(self) -> None:
        """
        Clear all internal symbol data.

        Wait for all user-requested symbols to be parsed before clearing the parser.
        """
        for future in filter(attrgetter("user_requested"), self._item_futures.values()):
            await future
        if self._parse_task is not None:
            self._parse_task.cancel()
        self._queue.clear()
        self._page_doc_items.clear()
        self._item_futures.clear()
