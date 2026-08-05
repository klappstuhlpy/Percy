"""Tests for RSS/Atom parsing (``app/services/news/feeds.py``).

Pure: fixture documents in, rows out. The two fixtures are shaped like the feeds actually
polled -- namespaced Atom with ``<link href>`` and ``media:thumbnail``, RSS 2.0 with ``guid``,
``pubDate``, an HTML ``description`` and an ``<enclosure>`` -- rather than the minimal examples
from the specs, because the spec-minimal cases are not the ones that break.

The §9 constraints (headline + short excerpt + link, never full text) are tested as contract,
not as formatting: the excerpt cap and the HTML stripping are what keeps stored data legal.
"""

from __future__ import annotations

import datetime

import pytest

from app.services.news import EXCERPT_LIMIT, parse_date, parse_feed, strip_html, truncate

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Publisher Newsroom</title>
    <link>https://example.test/</link>
    <description>Everything from the publisher</description>
    <item>
      <title>Loki returns for a third season</title>
      <link>https://example.test/loki-season-three</link>
      <guid isPermaLink="false">urn:example:1</guid>
      <pubDate>Tue, 04 Aug 2026 12:00:00 +0000</pubDate>
      <description>&lt;p&gt;The &lt;b&gt;God of Mischief&lt;/b&gt; is back &amp;amp; filming.&lt;/p&gt;</description>
      <dc:creator>A. Reporter</dc:creator>
      <enclosure url="https://cdn.example.test/loki.jpg" type="image/jpeg" length="12345"/>
    </item>
    <item>
      <title>Amazing Spider-Man #1 announced</title>
      <link>https://example.test/asm-1</link>
      <guid isPermaLink="false">urn:example:2</guid>
      <pubDate>not a date at all</pubDate>
      <description>A new run starts in October.</description>
      <media:thumbnail url="https://cdn.example.test/asm.jpg"/>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <title>Studio Wire</title>
  <link rel="self" href="https://example.test/feed.atom"/>
  <updated>2026-08-04T13:00:00Z</updated>
  <entry>
    <title>Pedro Pascal joins the cast</title>
    <link rel="self" href="https://example.test/feed.atom#1"/>
    <link rel="alternate" href="https://example.test/pascal-cast"/>
    <id>tag:example.test,2026:1</id>
    <updated>2026-08-04T12:30:00+02:00</updated>
    <summary type="html">&lt;p&gt;Casting confirmed for the sequel.&lt;/p&gt;</summary>
    <author><name>B. Writer</name></author>
    <media:thumbnail url="https://cdn.example.test/pascal.jpg"/>
  </entry>
</feed>
"""


# -- RSS 2.0 --------------------------------------------------------------

def test_rss_items_are_parsed_in_document_order() -> None:
    feed = parse_feed(RSS)
    assert feed.error is None
    assert [item.headline for item in feed.items] == [
        'Loki returns for a third season', 'Amazing Spider-Man #1 announced']


def test_rss_item_fields() -> None:
    item = parse_feed(RSS).items[0]
    assert item.url == 'https://example.test/loki-season-three'
    assert item.external_id == 'urn:example:1'
    assert item.excerpt == 'The God of Mischief is back & filming.'
    assert item.author == 'A. Reporter'
    assert item.image_url == 'https://cdn.example.test/loki.jpg'
    assert item.published_at == datetime.datetime(2026, 8, 4, 12, 0)


def test_media_thumbnail_is_used_when_there_is_no_enclosure() -> None:
    assert parse_feed(RSS).items[1].image_url == 'https://cdn.example.test/asm.jpg'


def test_an_unparseable_date_does_not_lose_the_item() -> None:
    item = parse_feed(RSS).items[1]
    assert item.published_at is None
    assert item.headline == 'Amazing Spider-Man #1 announced'


# -- Atom -----------------------------------------------------------------

def test_atom_entry_fields() -> None:
    feed = parse_feed(ATOM)
    item = feed.items[0]
    assert item.url == 'https://example.test/pascal-cast'
    assert item.external_id == 'tag:example.test,2026:1'
    assert item.headline == 'Pedro Pascal joins the cast'
    assert item.excerpt == 'Casting confirmed for the sequel.'
    assert item.author == 'B. Writer'
    assert item.image_url == 'https://cdn.example.test/pascal.jpg'


def test_atom_prefers_the_alternate_link_over_self() -> None:
    """``rel="self"`` points at the feed document -- linking a subscriber there is a bug."""
    assert parse_feed(ATOM).items[0].url == 'https://example.test/pascal-cast'


def test_atom_dates_are_stored_as_naive_utc() -> None:
    """``12:30+02:00`` is ``10:30`` UTC, and Percy stores timestamps naive."""
    published = parse_feed(ATOM).items[0].published_at
    assert published == datetime.datetime(2026, 8, 4, 10, 30)
    assert published is not None and published.tzinfo is None


# -- dates ----------------------------------------------------------------

@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('Tue, 04 Aug 2026 12:00:00 +0000', datetime.datetime(2026, 8, 4, 12, 0)),
        ('Tue, 04 Aug 2026 12:00:00 GMT', datetime.datetime(2026, 8, 4, 12, 0)),
        ('Tue, 04 Aug 2026 14:00:00 +0200', datetime.datetime(2026, 8, 4, 12, 0)),
        ('2026-08-04T12:00:00Z', datetime.datetime(2026, 8, 4, 12, 0)),
        ('2026-08-04T12:00:00+00:00', datetime.datetime(2026, 8, 4, 12, 0)),
        ('2026-08-04', datetime.datetime(2026, 8, 4, 0, 0)),
        ('', None),
        (None, None),
        ('yesterday', None),
        ('Tue, 32 Aug 2026 99:00:00 +0000', None),
    ],
)
def test_parse_date(value: str | None, expected: datetime.datetime | None) -> None:
    assert parse_date(value) == expected


# -- excerpt hygiene (§9) -------------------------------------------------

def test_strip_html_removes_markup_and_unescapes_entities() -> None:
    assert strip_html('<p>Hello <b>there</b> &amp; welcome</p>') == 'Hello there & welcome'


def test_strip_html_drops_script_and_style_contents() -> None:
    """Their *contents* are not prose, so removing only the tags would keep the code."""
    assert strip_html('<style>p{color:red}</style>Real text<script>alert(1)</script>') == 'Real text'


def test_strip_html_collapses_whitespace() -> None:
    assert strip_html('<p>one</p>\n\n<p>two   three</p>') == 'one two three'


def test_truncate_is_a_no_op_below_the_cap() -> None:
    assert truncate('short', 100) == 'short'


def test_truncate_cuts_at_a_word_boundary_within_the_budget() -> None:
    text = 'word ' * 200
    cut = truncate(text)
    assert len(cut) <= EXCERPT_LIMIT
    assert cut.endswith('…')
    assert 'wor…' not in cut


def test_excerpt_is_capped_on_the_way_in() -> None:
    """The cap is a legal boundary (§9), so it is enforced by the parser, not the renderer."""
    body = 'Sentence about the film. ' * 100
    document = RSS.replace('A new run starts in October.', body)
    item = parse_feed(document).items[1]
    assert item.excerpt is not None
    assert len(item.excerpt) <= EXCERPT_LIMIT
    assert len(body) > EXCERPT_LIMIT


# -- dedupe ---------------------------------------------------------------

def test_duplicate_urls_collapse_to_one_item() -> None:
    document = RSS.replace('https://example.test/asm-1', 'https://example.test/loki-season-three')
    feed = parse_feed(document)
    assert len(feed.items) == 1
    assert feed.duplicates == 1


def test_duplicate_external_ids_collapse_when_the_urls_differ() -> None:
    """§5.1's second axis: a feed that rewrote its links but kept its guid."""
    document = RSS.replace('urn:example:2', 'urn:example:1')
    feed = parse_feed(document)
    assert [item.url for item in feed.items] == ['https://example.test/loki-season-three']
    assert feed.duplicates == 1


def test_url_dedupe_wins_before_the_id_dedupe() -> None:
    """Same URL under two guids is one story; the strong axis is checked first."""
    document = RSS.replace('https://example.test/asm-1', 'https://example.test/loki-season-three')
    assert parse_feed(document).items[0].external_id == 'urn:example:1'


# -- malformed input ------------------------------------------------------

def test_malformed_xml_returns_a_reason_and_no_items() -> None:
    feed = parse_feed('<rss><channel><item><title>oops')
    assert feed.items == ()
    assert feed.error is not None


def test_garbage_input_never_raises() -> None:
    feed = parse_feed('this is not xml at all')
    assert not feed
    assert feed.error is not None


@pytest.mark.parametrize('document', ['', '   ', b''])
def test_empty_document_is_an_error_not_a_crash(document: str | bytes) -> None:
    assert parse_feed(document).error is not None


def test_an_item_without_a_link_is_skipped_not_stored() -> None:
    """§9 requires every stored item to link to its original."""
    document = RSS.replace('<link>https://example.test/asm-1</link>', '')
    feed = parse_feed(document)
    assert [item.headline for item in feed.items] == ['Loki returns for a third season']
    assert feed.skipped == 1


def test_bytes_input_with_an_encoding_declaration_parses() -> None:
    """A ``str`` carrying an encoding declaration is a hard error in lxml; both must work."""
    assert parse_feed(RSS.encode('utf-8')).items[0].headline == 'Loki returns for a third season'


def test_a_byte_order_mark_is_tolerated() -> None:
    assert parse_feed(b'\xef\xbb\xbf' + RSS.encode('utf-8')).items != ()


def test_an_undeclared_entity_is_recovered_rather_than_losing_the_feed() -> None:
    """The single most common real-world break: ``&nbsp;`` in a document with no DTD."""
    document = RSS.replace('A new run starts in October.', 'A new&nbsp;run starts in October.')
    feed = parse_feed(document)
    assert len(feed.items) == 2
