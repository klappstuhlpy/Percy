"""RSS 2.0 and Atom parsing: a feed document -> candidate ``news_items`` rows.

Pure: the document arrives as a string or bytes and rows come back, so the whole parser is
unit-testable against fixture XML with no network. Fetching is
:class:`~app.clients.news.NewsClient`'s job.

One parser, both formats
------------------------
RSS and Atom disagree on element names, nesting and namespaces, and real feeds disagree with
both specs. Rather than branch on the root element, this walks every ``item`` (RSS) and
``entry`` (Atom) element by *local* name and reads each field from the first of several
candidate children -- so ``description``/``summary``/``content`` and
``pubDate``/``published``/``updated``/``dc:date`` are all handled by one code path, and a
namespace prefix never matters.

What is stored, and what is not
-------------------------------
The plan's §9 is binding and is enforced here rather than downstream: **headline, a short
excerpt, an image the feed itself offers, the author and a link -- never the article body.**
The excerpt is stripped of HTML and capped at :data:`EXCERPT_LIMIT` (400 characters, cut at a
word boundary). 400 is chosen as the largest cap that is unmistakably a *teaser*: two or three
sentences, enough to decide whether to click through, and short enough that a digest can carry
several items without a wall of text. An item with no link is dropped entirely -- §9 requires
every item to link to its original, so an unlinkable story has no legal shape to be stored in.

Dedupe order is §5.1's: ``url`` first, then ``(source, external_id)``. A syndicated story
republished under two guids is one story; a feed that rewrites its links while keeping its guid
is the case the second axis catches. Within one document there is only one source, so the
second axis is just ``external_id`` here -- the cross-source half is the unique constraint in
``migrations/V43__news.sql``.

Malformed input never raises into the caller. A document that will not parse comes back as an
empty :class:`ParsedFeed` carrying a ``error`` string, which the caller writes to
``news_sources.last_status`` -- a dead feed has to be *visible*, and an exception escaping a
polling loop is how one source takes the rest down with it.
"""

from __future__ import annotations

import datetime
import html
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from lxml import etree

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

__all__ = (
    'EXCERPT_LIMIT',
    'FeedItem',
    'ParsedFeed',
    'parse_date',
    'parse_feed',
    'strip_html',
    'truncate',
)

#: Hard cap on a stored excerpt, in characters. See the module docstring for the reasoning --
#: it is a legal boundary (§9), not a display preference, so it is enforced on write.
EXCERPT_LIMIT = 400

#: Elements that carry one story, by local name: RSS 2.0 and Atom respectively.
_ENTRY_NAMES = ('item', 'entry')

#: Field candidates, in preference order, by local name.
_TITLE_NAMES = ('title',)
_EXCERPT_NAMES = ('description', 'summary', 'subtitle', 'content')
_ID_NAMES = ('guid', 'id')
_DATE_NAMES = ('pubdate', 'published', 'updated', 'date')
_AUTHOR_NAMES = ('author', 'creator', 'dc:creator')

_SCRIPT_RE = re.compile(r'<(script|style)\b.*?</\1>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]*>')
_WHITESPACE_RE = re.compile(r'\s+')

#: XXE and entity-expansion are off: a feed is untrusted input from an arbitrary publisher, and
#: neither external entities nor a DTD has any business in an item list.
_PARSER_ARGS: dict[str, Any] = {'resolve_entities': False, 'no_network': True, 'load_dtd': False}


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One candidate ``news_items`` row.

    ``published_at`` is **naive UTC** like every other timestamp Percy stores, and is ``None``
    when the feed's date was missing or unparseable -- an item is never dropped for a bad date,
    it just sorts by ``fetched_at`` instead.
    """

    external_id: str | None
    url: str
    headline: str
    excerpt: str | None
    image_url: str | None
    author: str | None
    published_at: datetime.datetime | None


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    """The result of one parse: the items, and why anything is missing.

    ``error`` is set only when the *document* failed; individual unusable entries are counted
    in ``skipped`` instead, since one bad item must not cost a whole feed.
    """

    items: tuple[FeedItem, ...] = ()
    error: str | None = None
    skipped: int = 0
    duplicates: int = 0

    def __bool__(self) -> bool:
        return bool(self.items)


def _local_name(element: Any) -> str:
    """The lowercase local name of an element, or ``''`` for a comment/PI."""
    tag = element.tag
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1].lower()


def _children(element: Any, names: Iterable[str]) -> Iterator[Any]:
    """Yields direct children whose local name is one of ``names``, in document order."""
    wanted = {name.rsplit(':', 1)[-1].lower() for name in names}
    for child in element:
        if _local_name(child) in wanted:
            yield child


def _first_text(element: Any, names: Iterable[str]) -> str | None:
    """The text of the first non-empty matching child, unescaped and whitespace-collapsed."""
    for child in _children(element, names):
        text = _clean(''.join(child.itertext()))
        if text:
            return text
    return None


def _clean(value: str | None) -> str:
    """Unescapes entities and collapses whitespace."""
    return _WHITESPACE_RE.sub(' ', html.unescape(value or '')).strip()


def strip_html(value: str | None) -> str:
    """Reduces a feed's HTML description to plain text.

    Script and style blocks go first (their *contents* are not prose), then tags, then a second
    unescape for the feeds that double-escape their markup.
    """
    without_blocks = _SCRIPT_RE.sub(' ', value or '')
    return _clean(_TAG_RE.sub(' ', without_blocks))


def truncate(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """Cuts ``text`` to ``limit`` characters at a word boundary, adding an ellipsis.

    The ellipsis is part of the budget, so the result is never longer than ``limit``.
    """
    if len(text) <= limit:
        return text
    head = text[:limit - 1].rstrip()
    spaced = head.rsplit(' ', 1)[0] if ' ' in head else head
    return f'{spaced.rstrip()}…'


def _naive_utc(value: datetime.datetime) -> datetime.datetime:
    """Drops an aware datetime to naive UTC, which is how Percy stores timestamps."""
    return value.astimezone(datetime.UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def parse_date(value: str | None) -> datetime.datetime | None:
    """Parses a feed date into naive UTC, or ``None`` if it is unusable.

    Both formats in the wild are tried: RFC 822 (``pubDate: Tue, 04 Aug 2026 12:00:00 +0000``)
    via :func:`email.utils.parsedate_to_datetime`, and ISO 8601 (``updated:
    2026-08-04T12:00:00Z``) via :meth:`datetime.datetime.fromisoformat`. Neither raises out of
    here -- a feed with a broken date is common and is not a reason to lose the story.
    """
    text = (value or '').strip()
    if not text:
        return None
    for parse in (parsedate_to_datetime, datetime.datetime.fromisoformat):
        try:
            parsed = parse(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, datetime.datetime):
            return _naive_utc(parsed)
    return None


def _link(entry: Any) -> str | None:
    """The story's URL: RSS puts it in ``<link>``'s text, Atom in ``<link href=...>``.

    Atom's ``alternate`` link (explicit or implied by having no ``rel``) is the human-readable
    page; ``rel="self"``/``"enclosure"`` are not, and picking one of those would link a
    subscriber to a feed document.
    """
    fallback: str | None = None
    for child in _children(entry, ('link',)):
        href = (child.get('href') or '').strip()
        rel = (child.get('rel') or 'alternate').strip().lower()
        if href:
            if rel == 'alternate':
                return href
            fallback = fallback or href
            continue
        text = _clean(child.text)
        if text:
            return text
    return fallback


def _image(entry: Any) -> str | None:
    """The image the feed offers for syndication, or ``None``.

    Only what the feed itself publishes -- an ``<enclosure>`` or a ``media:*`` element with an
    image type. Nothing is scraped out of the article body: §9 forbids rehosting an image the
    feed does not offer, and a URL lifted from prose is exactly that.
    """
    for child in _children(entry, ('enclosure', 'thumbnail', 'content', 'image')):
        url = (child.get('url') or child.get('href') or '').strip()
        if not url:
            continue
        kind = (child.get('type') or '').lower()
        medium = (child.get('medium') or '').lower()
        if kind.startswith('image/') or medium == 'image' or _local_name(child) in ('thumbnail', 'image'):
            return url
    return None


def _author(entry: Any) -> str | None:
    """The byline: RSS/``dc:creator`` put it in the text, Atom nests a ``<name>``."""
    for child in _children(entry, _AUTHOR_NAMES):
        nested = _first_text(child, ('name',))
        if nested:
            return nested
        text = _clean(''.join(child.itertext()))
        if text:
            return text
    return None


def _item(entry: Any) -> FeedItem | None:
    """Maps one ``<item>``/``<entry>`` onto a row, or ``None`` if it is unusable."""
    url = _link(entry)
    headline = _first_text(entry, _TITLE_NAMES)
    if not url or not headline:
        return None
    excerpt = strip_html(_raw_excerpt(entry))
    return FeedItem(
        external_id=_first_text(entry, _ID_NAMES) or None,
        url=url,
        headline=headline,
        excerpt=truncate(excerpt) or None,
        image_url=_image(entry),
        author=_author(entry),
        published_at=parse_date(_first_text(entry, _DATE_NAMES)),
    )


def _raw_excerpt(entry: Any) -> str:
    """The first non-empty summary-ish child, with its markup intact for :func:`strip_html`."""
    for child in _children(entry, _EXCERPT_NAMES):
        raw = ''.join(child.itertext())
        if raw.strip():
            return raw
    return ''


def _document(raw: str | bytes) -> bytes:
    """Normalises input to bytes lxml will accept.

    A ``str`` carrying an XML encoding declaration is a hard error in lxml, and a BOM or
    leading whitespace before the declaration is a parse error -- both are ordinary in feeds
    served by ordinary CMSes, so both are handled rather than reported.
    """
    data = raw.encode('utf-8') if isinstance(raw, str) else bytes(raw)
    return data.lstrip(b'\xef\xbb\xbf \t\r\n')


def _root(raw: str | bytes) -> tuple[Any, str | None]:
    """Parses a document, falling back to lxml's recovering parser once.

    Returns the root and, when the strict parse failed, the syntax error that made the retry
    necessary -- kept so a feed that recovers into nothing usable can still say *why*.

    Strict first, because a feed that parses cleanly should not be silently repaired. The one
    retry exists for the single most common real-world break -- an undeclared HTML entity such
    as ``&nbsp;`` in a document with no DTD -- which is a publisher's CMS bug, not a reason to
    lose their whole feed.
    """
    data = _document(raw)
    if not data:
        raise etree.XMLSyntaxError('empty document', None, 0, 0)
    try:
        return etree.fromstring(data, parser=etree.XMLParser(**_PARSER_ARGS)), None
    except etree.XMLSyntaxError as exc:
        root = etree.fromstring(data, parser=etree.XMLParser(recover=True, **_PARSER_ARGS))
        if root is None:
            raise
        return root, str(exc)


def parse_feed(document: str | bytes) -> ParsedFeed:
    """Parses an RSS 2.0 or Atom document into candidate ``news_items`` rows.

    Items are returned in document order, deduped by ``url`` and then by ``external_id``
    (§5.1). Entries with no link or no headline are counted in ``skipped`` rather than raising.
    A document that yields nothing usable -- broken XML, an empty channel, or entries that are
    all unlinkable -- returns no items and an ``error`` saying which, because the caller's job
    is to record that in ``news_sources.last_status``.
    """
    try:
        root, recovered = _root(document)
    except (etree.XMLSyntaxError, ValueError) as exc:
        return ParsedFeed(error=f'unparseable feed: {exc}')

    items: list[FeedItem] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    skipped = duplicates = 0

    for element in root.iter():
        if _local_name(element) not in _ENTRY_NAMES:
            continue
        item = _item(element)
        if item is None:
            skipped += 1
            continue
        if item.url in seen_urls or (item.external_id is not None and item.external_id in seen_ids):
            duplicates += 1
            continue
        seen_urls.add(item.url)
        if item.external_id is not None:
            seen_ids.add(item.external_id)
        items.append(item)

    if not items:
        reason = f'malformed feed: {recovered}' if recovered else 'no items found'
        return ParsedFeed(error=reason, skipped=skipped, duplicates=duplicates)
    return ParsedFeed(items=tuple(items), skipped=skipped, duplicates=duplicates)
