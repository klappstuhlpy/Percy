from __future__ import annotations

import asyncio
import collections
import copy
import functools
import logging
import re
import string
import textwrap
from collections import defaultdict, deque, namedtuple
from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, Any, NamedTuple

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from app.utils import executor, find_nth_occurrence, pagify

from .cache import doc_cache
from .html import (
    DocMarkdownConverter,
    FilterAttributes,
    get_dd_description,
    get_general_description,
    get_signatures,
)
from .models import MAX_SIGNATURE_AMOUNT, DocItem

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator

    from app.core import Bot

log = logging.getLogger(__name__)

_WHITESPACE_AFTER_NEWLINES_RE = re.compile(r'(?<=\n\n)(\s+)')
_PARAMETERS_RE = re.compile(r'\((.+)\)')

_NO_SIGNATURE_GROUPS = {
    'envvar',
    'setting',
    'tempaltefilter',
    'templatetag',
    'term',
}
_HEADING_DESC_GROUPS = {
    'dt',
    'dl'
}
_NO_FIELD_GROUPS = {
    'Parameters'
}
_EMBED_CODE_BLOCK_LINE_LENGTH = 61
_MAX_SIGNATURES_LENGTH = (_EMBED_CODE_BLOCK_LINE_LENGTH + 8) * MAX_SIGNATURE_AMOUNT
_MAX_DESCRIPTION_LENGTH = 4096 - _MAX_SIGNATURES_LENGTH
_TRUNCATE_STRIP_CHARACTERS = '!?:;.' + string.whitespace

BracketPair = namedtuple('BracketPair', ['opening_bracket', 'closing_bracket'])
_BRACKET_PAIRS = {
    '{': BracketPair('{', '}'),
    '(': BracketPair('(', ')'),
    '[': BracketPair('[', ']'),
    '<': BracketPair('<', '>'),
}


def _split_parameters(parameters_string: str) -> Iterator[str]:
    """
    Split parameters of a signature into individual parameter strings on commas.

    Long string literals are not accounted for.
    """
    last_split = 0
    depth = 0
    current_search: BracketPair | None = None

    enumerated_string = enumerate(parameters_string)
    for index, character in enumerated_string:
        if character in {''', '''}:
            quote_character = character
            preceding_backslashes = 0
            for _, character in enumerated_string:
                if character == quote_character and not preceding_backslashes % 2:
                    break
                if character == '\\':
                    preceding_backslashes += 1
                else:
                    preceding_backslashes = 0

        elif current_search is None:
            if (current_search := _BRACKET_PAIRS.get(character)) is not None:
                depth = 1
            elif character == ',':
                yield parameters_string[last_split:index]
                last_split = index + 1

        else:
            if character == current_search.opening_bracket:
                depth += 1

            elif character == current_search.closing_bracket:
                depth -= 1
                if depth == 0:
                    current_search = None

    yield parameters_string[last_split:]


def _truncate_signatures(signatures: Collection[str]) -> list[str] | Collection[str]:
    """Truncate passed signatures to not exceed `_MAX_SIGNATURES_LENGTH`.

    If the signatures need to be truncated, parameters are collapsed until they fit withing the limit.
    Individual signatures can consist of max 1, 2, ..., `_MAX_SIGNATURE_AMOUNT` lines of text,
    inversely proportional to the amount of signatures.
    A maximum of `_MAX_SIGNATURE_AMOUNT` signatures is assumed to be passed.
    """
    if sum(len(signature) for signature in signatures) <= _MAX_SIGNATURES_LENGTH:
        return signatures

    max_signature_length = _EMBED_CODE_BLOCK_LINE_LENGTH * (MAX_SIGNATURE_AMOUNT + 1 - len(signatures))
    formatted_signatures = []
    for signature in signatures:
        signature = signature.strip()
        if len(signature) > max_signature_length:
            if (parameters_match := _PARAMETERS_RE.search(signature)) is None:
                formatted_signatures.append(textwrap.shorten(signature, max_signature_length, placeholder='...'))
                continue

            truncated_signature = []
            parameters_string = parameters_match[1]
            running_length = len(signature) - len(parameters_string)
            for parameter in _split_parameters(parameters_string):
                if (len(parameter) + running_length) <= max_signature_length - 5:
                    truncated_signature.append(parameter)
                    running_length += len(parameter) + 1
                else:
                    truncated_signature.append(' ...')
                    formatted_signatures.append(signature.replace(parameters_string, ','.join(truncated_signature)))
                    break
        else:
            formatted_signatures.append(signature)

    return formatted_signatures


def _get_truncated_description(
    elements: Iterable[Tag | NavigableString],
    markdown_converter: DocMarkdownConverter,
    max_length: int,
    max_lines: int,
) -> str:
    """Truncate the Markdown from `elements` to be at most `max_length` characters when rendered or `max_lines` newlines.

    `max_length` limits the length of the rendered characters in the string,
    with the real string length limited to `_MAX_DESCRIPTION_LENGTH` to accommodate discord length limits.
    """
    result = ""
    markdown_element_ends = []
    rendered_length = 0

    tag_end_index = 0
    for element in elements:
        is_tag = isinstance(element, Tag)
        element_length = len(element.text) if is_tag else len(element)

        if rendered_length + element_length < max_length:
            if is_tag:
                element_markdown = markdown_converter.process_tag(element, convert_as_inline=False)
            else:
                element_markdown = markdown_converter.process_text(element)

            rendered_length += element_length
            tag_end_index += len(element_markdown)

            if not element_markdown.isspace():
                markdown_element_ends.append(tag_end_index)
            result += element_markdown
        else:
            break

    if not markdown_element_ends:
        return ""

    newline_truncate_index = find_nth_occurrence(result, '\n', max_lines)
    if newline_truncate_index is not None and newline_truncate_index < _MAX_DESCRIPTION_LENGTH - 3:
        truncate_index = newline_truncate_index
    else:
        truncate_index = _MAX_DESCRIPTION_LENGTH - 3

    if truncate_index >= markdown_element_ends[-1]:
        return result

    possible_truncation_indices = [cut for cut in markdown_element_ends if cut < truncate_index]
    if not possible_truncation_indices:
        force_truncated = result[:truncate_index]
        if force_truncated.count('```') % 2:
            force_truncated = force_truncated[:force_truncated.rfind('```')]
        for string_ in ('\n\n', '\n', '. ', ', ', ',', ' '):
            cutoff = force_truncated.rfind(string_)

            if cutoff != -1:
                truncated_result = force_truncated[:cutoff]
                break
        else:
            truncated_result = force_truncated

    else:
        markdown_truncate_index = possible_truncation_indices[-1]
        truncated_result = result[:markdown_truncate_index]

    return truncated_result.strip(_TRUNCATE_STRIP_CHARACTERS) + '...'


_pagify_description = functools.partial(
    pagify,
    page_length=1024,
    priority=True,
    delims=['\n', ' ']
)


def _create_markdown(
        signatures: list[str] | None,
        description: Iterable[Tag],
        url: str,
        *,
        truncate: bool = True,
        max_length: int = 2700,
        max_lines: int = 13
) -> str:
    """Create a Markdown string with the signatures at the top, and the converted html description below them.

    The signatures are wrapped in python codeblocks, separated from the description by a newline.
    The result Markdown string is max 750 rendered characters for the description with signatures at the start.
    """
    markdown_converter = DocMarkdownConverter(bullets='-', page_url=url)
    if truncate:
        description = _get_truncated_description(
            description,
            markdown_converter=markdown_converter,
            max_length=max_length,
            max_lines=max_lines
        )
    else:
        iter = copy.copy(description)
        description = ""
        for element in iter:
            if isinstance(element, Tag):
                description += markdown_converter.process_tag(element, convert_as_inline=False)
            else:
                description += markdown_converter.process_text(element)

    description = _WHITESPACE_AFTER_NEWLINES_RE.sub("", description)
    if signatures is not None:
        signature = "".join(f'```py\n{signature}```' for signature in _truncate_signatures(signatures))
        return f'{signature}\n{description}'
    return description


@executor
def get_symbol_markdown(soup: BeautifulSoup, symbol_data: DocItem) -> str | None:
    """@executor

    Return parsed Markdown of the passed item using the passed in soup, truncated to fit within a discord message.

    The method of parsing and what information gets included depends on the symbol's group.
    """
    symbol_heading = soup.find(id=symbol_data.symbol_id)

    if symbol_heading is None:
        return None

    signature = None
    if symbol_heading.name not in _HEADING_DESC_GROUPS:
        # No signature, no text description
        description = get_general_description(symbol_heading)
    else:
        if symbol_data.group not in _NO_SIGNATURE_GROUPS:
            signature = get_signatures(symbol_heading)
        description = get_dd_description(
            symbol_heading, attributes=FilterAttributes('div', 'ignore', class_='operations'))

    for description_element in description:
        if isinstance(description_element, Tag):
            for tag in description_element.find_all('a', class_='headerlink'):
                tag.decompose()

    return _create_markdown(signature, description, symbol_data.url).strip()


@executor
def get_field_markdown(soup: BeautifulSoup, symbol_data: DocItem) -> dict[str, Any] | None:
    """@executor

    Return parsed Markdown of the passed item using the passed in soup, truncated to fit within a discord message.

    This is for special fields of the items description, like `Supported Operations` for classes.
    """
    symbol_heading = soup.find(id=symbol_data.symbol_id)

    if symbol_heading is None:
        return None

    fields: dict[str, str] = {}

    operations = get_dd_description(
        symbol_heading, attributes=FilterAttributes('div', 'return', class_='operations'))
    items: list[tuple[str, str]] = []
    for operation in operations:
        if isinstance(operation, Tag):
            for tag in operation.find_all('a', class_='headerlink'):
                tag.decompose()

            if operation.find('dt') and operation.find('dd'):
                operation_name = operation.find('dt').text.strip()
                operation_description = operation.find('dd').text.strip()
                items.append((operation_name, operation_description))

    if items:
        fields['**Supported Operations**'] = '\n'.join([f'`{name}` - {description}' for name, description in items])

    parent_dd = symbol_heading.find_next('dd')
    for field in parent_dd.find_all('dl', class_='field-list simple', recursive=False):
        if field.find('dt') and field.find('dd'):
            name = field.find('dt').text.strip()

            if name in _NO_FIELD_GROUPS:
                continue

            description = _create_markdown(None, field.find_all('dd'), symbol_data.url, truncate=False)

            if len(description) > 1024:
                for i, chunk in enumerate(_pagify_description(description)):
                    fields[name if i == 0 else '\u200b'] = chunk
            else:
                fields[name] = description

    return fields


class QueueItem(NamedTuple):
    """Contains a `DocItem` and the `BeautifulSoup` object needed to parse it."""

    doc_item: DocItem
    soup: BeautifulSoup

    def __eq__(self, other: QueueItem | DocItem) -> bool:
        if isinstance(other, DocItem):
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
        _page_doc_items: dict[str, list[DocItem]]
        _item_futures: dict[DocItem, ParseResultFuture]
        __task: asyncio.Task | None

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot

        self.queue: deque[QueueItem] = collections.deque()
        self._page_doc_items: dict[str, list[DocItem]] = defaultdict(list)
        self._item_futures: dict[DocItem, ParseResultFuture] = defaultdict(ParseResultFuture)
        self.__task: asyncio.Task | None = None

    async def get_markdown(self, doc_item: DocItem) -> str | None:
        """|coro|

        If no symbols were fetched from `doc_item`s page before,
        the HTML has to be fetched and then all items from the page are put into the parse queue.

        Not safe to run while `self.clear` is running.

        Parameters
        ----------
        doc_item : DocItem
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
                item, soup = self.queue.pop()  # type: DocItem, BeautifulSoup
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

    def _move_to_front(self, item: QueueItem | DocItem) -> None:
        """Move `item` to the front of the parse queue."""
        item_index = self.queue.index(item)
        queue_item = self.queue[item_index]
        del self.queue[item_index]

        self.queue.append(queue_item)
        log.debug('Moved %s to the front of the queue.', item)

    def add_item(self, doc_item: DocItem) -> None:
        """Map a DocItem to its page so that the symbol will be parsed once the page is requested."""
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
