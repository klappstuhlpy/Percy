from __future__ import annotations

import asyncio
import collections
import logging
import re
import string
import textwrap
from collections import defaultdict, deque, namedtuple
from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, NamedTuple

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from app.utils import executor

from .cache import doc_cache
from .html import (
    DocMarkdownConverter,
    admonition_kind,
    clean_signature,
    clean_version_text,
    elements_to_markdown,
    get_general_description,
    get_signatures,
    is_admonition,
    is_member_definition,
    is_version_div,
)
from .models import MAX_SIGNATURE_AMOUNT, Admonition, DocField, DocItem, DocResult, Member, Operation

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from app.core import Bot

log = logging.getLogger(__name__)

_PARAMETERS_RE = re.compile(r"\((.+)\)")

#: Groups that have no call signature to scrape (settings, glossary terms, …).
_NO_SIGNATURE_GROUPS = {
    "envvar",
    "setting",
    "templatefilter",
    "templatetag",
    "term",
}
#: Heading tag names that carry a description in a sibling ``dd`` (Python domain objects).
_HEADING_DESC_GROUPS = {"dt", "dl"}
#: Field names we never surface verbatim (handled elsewhere or pure noise).
_SKIP_FIELDS = {"supported operations"}

_EMBED_CODE_BLOCK_LINE_LENGTH = 61
_MAX_SIGNATURES_LENGTH = (_EMBED_CODE_BLOCK_LINE_LENGTH + 8) * MAX_SIGNATURE_AMOUNT
_MAX_DESCRIPTION_LENGTH = 2500
_MAX_FIELD_LENGTH = 1024
#: A section lookup lists at most this many member definitions, each capped at this description length.
_MAX_MEMBERS = 12
_MAX_MEMBER_DESC = 350
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_TRUNCATE_STRIP_CHARACTERS = "!?:;." + string.whitespace

BracketPair = namedtuple("BracketPair", ["opening_bracket", "closing_bracket"])
_BRACKET_PAIRS = {
    "{": BracketPair("{", "}"),
    "(": BracketPair("(", ")"),
    "[": BracketPair("[", "]"),
    "<": BracketPair("<", ">"),
}


def _split_parameters(parameters_string: str) -> Iterator[str]:
    """Split parameters of a signature into individual parameter strings on commas.

    Long string literals are not accounted for.
    """
    last_split = 0
    depth = 0
    current_search: BracketPair | None = None

    enumerated_string = enumerate(parameters_string)
    for index, character in enumerated_string:
        if character in {"'", '"'}:
            quote_character = character
            preceding_backslashes = 0
            for _, character in enumerated_string:
                if character == quote_character and not preceding_backslashes % 2:
                    break
                if character == "\\":
                    preceding_backslashes += 1
                else:
                    preceding_backslashes = 0

        elif current_search is None:
            if (current_search := _BRACKET_PAIRS.get(character)) is not None:
                depth = 1
            elif character == ",":
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


def _truncate_signatures(signatures: Collection[str]) -> list[str]:
    """Truncate passed signatures to not exceed `_MAX_SIGNATURES_LENGTH`.

    If the signatures need to be truncated, parameters are collapsed until they fit within the limit.
    Individual signatures can consist of max 1, 2, ..., `MAX_SIGNATURE_AMOUNT` lines of text,
    inversely proportional to the amount of signatures.
    """
    if not signatures:
        return []
    if sum(len(signature) for signature in signatures) <= _MAX_SIGNATURES_LENGTH:
        return [signature.strip() for signature in signatures]

    max_signature_length = _EMBED_CODE_BLOCK_LINE_LENGTH * (MAX_SIGNATURE_AMOUNT + 1 - len(signatures))
    formatted_signatures = []
    for signature in signatures:
        signature = signature.strip()
        if len(signature) > max_signature_length:
            if (parameters_match := _PARAMETERS_RE.search(signature)) is None:
                formatted_signatures.append(textwrap.shorten(signature, max_signature_length, placeholder="..."))
                continue

            truncated_signature = []
            parameters_string = parameters_match[1]
            running_length = len(signature) - len(parameters_string)
            for parameter in _split_parameters(parameters_string):
                if (len(parameter) + running_length) <= max_signature_length - 5:
                    truncated_signature.append(parameter)
                    running_length += len(parameter) + 1
                else:
                    truncated_signature.append(" ...")
                    formatted_signatures.append(signature.replace(parameters_string, ",".join(truncated_signature)))
                    break
        else:
            formatted_signatures.append(signature)

    return formatted_signatures


def _strip_headerlinks(nodes: list[Tag | NavigableString]) -> None:
    """Remove the ¶ permalink anchors Sphinx sprinkles after every heading/term."""
    for node in nodes:
        if isinstance(node, Tag):
            for anchor in node.find_all("a", class_="headerlink"):
                anchor.decompose()


def _safe_truncate(markdown: str, max_length: int) -> str:
    """Truncate `markdown` to `max_length`, never splitting inside a fenced code block."""
    if len(markdown) <= max_length:
        return markdown

    cut = markdown[:max_length]
    # Don't leave a code fence open.
    if cut.count("```") % 2:
        cut = cut[: cut.rfind("```")]

    for delimiter in ("\n\n", "\n", ". ", ", ", " "):
        index = cut.rfind(delimiter)
        if index > max_length // 2:
            cut = cut[:index]
            break

    return cut.strip(_TRUNCATE_STRIP_CHARACTERS) + " …"


def _parse_admonition(div: Tag, converter: DocMarkdownConverter) -> Admonition:
    """Lift an admonition / see-also callout into a structured banner."""
    kind = admonition_kind(div)
    title_tag = div.find(class_="admonition-title")
    if title_tag is not None:
        title = title_tag.get_text(" ", strip=True)
        title_tag.extract()
    else:
        title = kind.title()

    body = _safe_truncate(elements_to_markdown(list(div.children), converter), _MAX_FIELD_LENGTH)
    return Admonition(title=title or kind.title(), body=body, kind=kind)


def _parse_operations(div: Tag, converter: DocMarkdownConverter) -> list[Operation]:
    """Parse a discord.py *Supported Operations* block into ordered :class:`Operation`s.

    A ``New in version`` / ``Changed in version`` note nested under an operation is detached and
    stored on the operation so the renderer can tab it neatly beneath that single entry.
    """
    operations: list[Operation] = []
    # Each ``.. describe::`` becomes its own ``dl.describe`` block, so collect every ``dt`` in the div
    # rather than assuming a single definition list holds them all.
    for term in div.find_all("dt"):
        description_tag = term.find_next_sibling("dd")
        name = " ".join(term.get_text(" ", strip=True).split())
        if not name:
            continue

        version: str | None = None
        description = ""
        if description_tag is not None:
            version_tag = description_tag.find(is_version_div)
            if version_tag is not None:
                version = clean_version_text(version_tag)
                version_tag.extract()
            _strip_headerlinks([description_tag])
            description = elements_to_markdown(list(description_tag.children), converter)

        operations.append(Operation(name=name, description=description, version=version))

    return operations


def _parse_field_list(definition_list: Tag, converter: DocMarkdownConverter) -> list[DocField]:
    """Parse a ``dl.field-list`` (Parameters / Raises / Returns / Return type / Yields / …)."""
    fields: list[DocField] = []
    for term in definition_list.find_all("dt", recursive=False):
        description_tag = term.find_next_sibling("dd")
        # numpydoc decorates the field name with a CSS ``<span class="colon">:</span>``; drop it.
        name = " ".join(term.get_text(" ", strip=True).split()).rstrip(":").strip()
        if not name or description_tag is None or name.lower() in _SKIP_FIELDS:
            continue

        _strip_headerlinks([description_tag])
        value = _safe_truncate(_render_field_value(description_tag, converter), _MAX_FIELD_LENGTH)
        if value:
            fields.append(DocField(name=name, value=value))

    return fields


def _render_field_value(description_tag: Tag, converter: DocMarkdownConverter) -> str:
    """Render a field's value, handling numpydoc's ``name : type`` definition-list parameters.

    Sphinx renders Parameters/Returns as a ``<ul>``; numpydoc renders them as a nested
    ``<dl>`` whose ``:`` separator is CSS-only — so a naive conversion glues the parameter name to
    its type (``xarray_like``). When that nested list is present we rebuild each entry explicitly.
    """
    nested = next((c for c in description_tag.find_all("dl", recursive=False)), None)
    if nested is not None and (rendered := _render_parameter_list(nested, converter)):
        return rendered
    return elements_to_markdown(list(description_tag.children), converter)


def _render_parameter_list(definition_list: Tag, converter: DocMarkdownConverter) -> str:
    """Render a numpydoc ``name : type`` parameter ``dl`` as a bullet list tying each name to its type."""
    lines: list[str] = []
    for term in definition_list.find_all("dt", recursive=False):
        working = term.__copy__()
        classifiers = [
            " ".join(span.get_text(" ", strip=True).split()) for span in working.find_all("span", class_="classifier")
        ]
        for span in working.find_all("span", class_="classifier"):
            span.extract()
        name = " ".join(working.get_text(" ", strip=True).split())

        types = ", ".join(c for c in classifiers if c)
        if name:
            head = f"- **{name}**" + (f" (*{types}*)" if types else "")
        elif types:
            head = f"- *{types}*"
        else:
            continue

        description_tag = term.find_next_sibling("dd")
        if description_tag is not None:
            description = " ".join(elements_to_markdown(list(description_tag.children), converter).split())
            if description:
                head += f" — {description}"
        lines.append(head)

    return "\n".join(lines)


def _member_domain(definition_list: Tag) -> str:
    """Return the Sphinx domain (``c`` / ``py`` / ``cpp`` / …) of a member ``dl``."""
    classes: list[str] = definition_list.get("class", [])  # type: ignore
    return classes[0] if classes else "py"


def _parse_member(definition_list: Tag, converter: DocMarkdownConverter) -> Member | None:
    """Parse a single member ``dl`` (a struct / function / macro / attribute) into a :class:`Member`.

    Only a one-line *summary* (the lead paragraph) is kept, so a category page that documents whole
    methods — like pygit2's commit-log tutorial — stays a tidy list instead of inlining every method's
    full body, prose lists and examples.
    """
    term = definition_list.find("dt", recursive=False)
    if term is None:
        return None

    signature = clean_signature(term)
    if not signature:
        return None

    description = ""
    version: str | None = None
    description_tag = term.find_next_sibling("dd")
    if description_tag is not None:
        version_tag = description_tag.find(is_version_div)
        if version_tag is not None:
            version = clean_version_text(version_tag)

        # Use only the lead paragraph as a summary; the member's own lookup shows the full body.
        lead = description_tag.find("p", recursive=False) or description_tag.find("p")
        if lead is not None:
            _strip_headerlinks([lead])
            summary = " ".join(elements_to_markdown([lead], converter).split())
            description = _safe_truncate(summary, _MAX_MEMBER_DESC)

    return Member(
        signature=signature,
        description=description,
        version=version,
        domain=_member_domain(definition_list),
    )


def _parse_section(section: Tag, converter: DocMarkdownConverter) -> DocResult:
    """Parse a documentation *section* (a category page) into intro text plus a list of members.

    This is what makes category pages such as CPython's "Create Config" render as a clean list where
    each ``struct`` / ``void`` / function signature is tied to its own description, instead of a flat
    blob. It works for any Sphinx domain (``c``, ``py``, ``cpp``, …).
    """
    result = DocResult()

    heading = section.find(_HEADING_TAGS, recursive=False)
    if heading is not None:
        for anchor in heading.find_all("a", class_="headerlink"):
            anchor.decompose()
        result.title = " ".join(heading.get_text(" ", strip=True).split())

    intro_nodes: list[Tag | NavigableString] = []
    seen_member = False
    for child in section.children:
        if isinstance(child, NavigableString):
            if child.strip() and not seen_member:
                intro_nodes.append(child)
            continue
        if not isinstance(child, Tag):
            continue

        if child.name in _HEADING_TAGS:
            continue
        if child.name == "section":
            # Don't dive into sub-sections — they own their own lookups.
            break

        if is_admonition(child):
            result.admonitions.append(_parse_admonition(child, converter))
        elif is_version_div(child):
            if note := clean_version_text(child):
                result.version_changes.append(note)
        elif child.name == "dl" and is_member_definition(child):
            seen_member = True
            if len(result.members) < _MAX_MEMBERS and (member := _parse_member(child, converter)) is not None:
                result.members.append(member)
        elif not seen_member:
            intro_nodes.append(child)

    _strip_headerlinks(intro_nodes)
    result.description = _safe_truncate(elements_to_markdown(intro_nodes, converter), _MAX_DESCRIPTION_LENGTH)

    # A pure landing section (only sub-sections, no prose or members): offer a table of contents so
    # the card is useful instead of empty.
    if not result.description and not result.members and not result.admonitions:
        result.description = _section_table_of_contents(section, converter.page_url)

    return result


def _find_main_section(soup: BeautifulSoup) -> Tag | None:
    """Locate a page's primary content container for anchorless (whole-page) lookups."""
    main = soup.find(attrs={"role": "main"}) or soup
    section = main.find("section") or main.find("div", class_="section")
    if section is not None:
        return section
    # Older Sphinx themes wrap the body in ``div.body``/``div.document`` with no ``section`` element.
    return main if isinstance(main, Tag) and main is not soup else None


def _section_table_of_contents(section: Tag, page_url: str) -> str:
    """Build a bullet list linking to a section's direct sub-sections (used for landing pages)."""
    links: list[str] = []
    for subsection in section.find_all("section", recursive=False):
        sub_id = subsection.get("id")
        heading = subsection.find(_HEADING_TAGS, recursive=False)
        if not sub_id or heading is None:
            continue
        for anchor in heading.find_all("a", class_="headerlink"):
            anchor.decompose()
        title = " ".join(heading.get_text(" ", strip=True).split())
        if title:
            links.append(f"- [{title}]({page_url}#{sub_id})")

    if not links:
        return ""
    return "**In this section**\n" + "\n".join(links[:15])


@executor
def parse_symbol(soup: BeautifulSoup, doc_item: DocItem) -> DocResult | None:
    """@executor

    Parse the HTML page in `soup` into a structured :class:`DocResult` for `doc_item`.

    The signature, description, callout banners, version notes, supported operations, field lists and
    section members are extracted independently so the renderer can lay each out on its own terms.
    Domain-agnostic: handles object pages (a ``dt`` + ``dd``) and category pages (a ``section``) for
    any Sphinx site — discord.py, CPython's Python and C API, aiohttp, and so on.
    """
    converter = DocMarkdownConverter(page_url=doc_item.url)

    # Page/label entries (``std:doc`` / ``std:module``) carry no anchor at all: render the page's
    # main section so the lookup still produces a useful overview instead of failing.
    if not doc_item.symbol_id:
        main_section = _find_main_section(soup)
        return _parse_section(main_section, converter) if main_section is not None else None

    heading = soup.find(id=doc_item.symbol_id)
    if heading is None:
        return None

    # Sections / labels / glossary anchors: render the category's intro plus its member definitions.
    if heading.name not in _HEADING_DESC_GROUPS:
        section = heading if heading.name == "section" else heading.find_parent("section")
        if section is not None:
            return _parse_section(section, converter)

        # Fallback for label targets that live outside any section: a flat description.
        description_nodes = get_general_description(heading)
        _strip_headerlinks(description_nodes)
        markdown = _safe_truncate(elements_to_markdown(description_nodes, converter), _MAX_DESCRIPTION_LENGTH)
        return DocResult(description=markdown)

    result = DocResult()
    if doc_item.group not in _NO_SIGNATURE_GROUPS:
        result.signatures = _truncate_signatures(get_signatures(heading))

    description_tag = heading.find_next_sibling("dd")
    if description_tag is None:
        return result

    description_nodes: list[Tag | NavigableString] = []
    for child in description_tag.children:
        if isinstance(child, NavigableString):
            if child.strip():
                description_nodes.append(child)
            continue
        if not isinstance(child, Tag):
            continue

        if is_admonition(child):
            result.admonitions.append(_parse_admonition(child, converter))
        elif is_version_div(child):
            if note := clean_version_text(child):
                result.version_changes.append(note)
        elif child.name == "div" and "operations" in child.get("class", []):
            result.operations.extend(_parse_operations(child, converter))
        elif child.name == "dl" and "field-list" in child.get("class", []):
            result.fields.extend(_parse_field_list(child, converter))
        elif is_member_definition(child):
            # A nested, separately-documented member: it owns its own DocItem, stop here.
            break
        else:
            description_nodes.append(child)

    _strip_headerlinks(description_nodes)
    result.description = _safe_truncate(elements_to_markdown(description_nodes, converter), _MAX_DESCRIPTION_LENGTH)
    return result


class QueueItem(NamedTuple):
    """Contains a `DocItem` and the `BeautifulSoup` object needed to parse it."""

    doc_item: DocItem
    soup: BeautifulSoup

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DocItem):
            return self.doc_item == other
        return NamedTuple.__eq__(self, other)

    def __hash__(self) -> int:
        return hash(self.doc_item)


class ParseResultFuture(asyncio.Future):
    """Future with metadata for the parser class.

    `user_requested` is set by the parser when a Future is requested by a user and moved to the front,
    allowing the futures to only be waited for when clearing if they were user requested.
    """

    def __init__(self) -> None:
        super().__init__()
        self.user_requested = False


class BatchParser:
    """Parse the documentation of every symbol on a page once the first symbol from it is requested.

    DocItems are added through the `add_item` method which maps them to their page; the first
    `get_symbol` call for a page fetches the HTML and queues every symbol on it, avoiding repeated
    requests to the same page.
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

    async def get_symbol(self, doc_item: DocItem) -> DocResult | None:
        """|coro|

        Return the parsed :class:`DocResult` for `doc_item`.

        If no symbol from `doc_item`'s page was fetched before, the HTML is fetched and every item
        from the page is queued for parsing. Not safe to run while `self.clear` is running.
        """
        if doc_item not in self._item_futures and doc_item not in self.queue:
            self._item_futures[doc_item].user_requested = True

            async with self.bot.session.get(doc_item.url, raise_for_status=True) as response:

                @executor
                def soupify(text: str) -> BeautifulSoup:
                    return BeautifulSoup(text, "lxml")

                soup = await soupify(await response.text(encoding="utf8"))

            self.queue.extendleft(QueueItem(item, soup) for item in self._page_doc_items[doc_item.url])
            log.debug("Added items from %s to the parse queue.", doc_item.url)

            if self.__task is None:
                self.__task = self.bot.loop.create_task(self._parse_queue(), name="Doc Item parsing Queue")
        else:
            self._item_futures[doc_item].user_requested = True

        with suppress(ValueError):
            self._move_to_front(doc_item)
        return await self._item_futures[doc_item]

    async def _parse_queue(self) -> None:
        """|coro|

        Parse all items from the queue, setting their result on the futures and caching them.
        The coroutine runs as long as the queue is not empty, resetting `self.__task` to None when done.
        """
        log.debug("Starting queue parsing.")
        try:
            while self.queue:
                item, soup = self.queue.pop()

                if (future := self._item_futures[item]).done():
                    continue

                result: DocResult | None = None
                try:
                    result = await parse_symbol(soup, item)  # type: ignore[misc]
                    if result is not None:
                        item.result = result
                        await doc_cache.set(item, result)
                except Exception:
                    log.exception("Unexpected error when handling %s.", item)

                future.set_result(result)
                del self._item_futures[item]
                await asyncio.sleep(0.1)
        finally:
            self.__task = None
            log.debug("Finished parsing queue.")

    def _move_to_front(self, item: QueueItem | DocItem) -> None:
        """Move `item` to the front of the parse queue."""
        item_index = self.queue.index(item)
        queue_item = self.queue[item_index]
        del self.queue[item_index]

        self.queue.append(queue_item)
        log.debug("Moved %s to the front of the queue.", item)

    def add_item(self, doc_item: DocItem) -> None:
        """Map a DocItem to its page so that the symbol will be parsed once the page is requested."""
        self._page_doc_items[doc_item.url].append(doc_item)

    async def remove(self, package: str) -> None:
        """|coro|

        Drop every queued item, future and page mapping belonging to `package`.
        """
        for queue_item in [i for i in self.queue if i.doc_item.package == package]:
            self.queue.remove(queue_item)
            self._item_futures.pop(queue_item.doc_item, None)

        for url in [url for url, items in self._page_doc_items.items() if items and items[0].package == package]:
            self._page_doc_items.pop(url, None)

    async def clear(self) -> None:
        """|coro|

        Clear all internal symbol data.
        Wait for all user-requested symbols to be parsed before clearing the parser.
        """
        for future in filter(attrgetter("user_requested"), self._item_futures.values()):
            with suppress(asyncio.CancelledError):
                await future

        if self.__task is not None:
            self.__task.cancel()
            self.__task = None

        self.queue.clear()
        self._page_doc_items.clear()
        self._item_futures.clear()
