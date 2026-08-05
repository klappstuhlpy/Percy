"""News: feed parsing and conservative subject tagging.

The second stream over the same subjects releases fan out through. See
``migrations/V43__news.sql`` for the tables, ``app/database/repositories/news.py`` for the
data-access layer, ``app/clients/news.py`` for the transport, and the plan's §4.5 for why a
story is tagged with the same keys a subscription is expressed in.
"""

from app.services.news.dispatch import NEWS_MEDIA, news_key, news_releasable
from app.services.news.feeds import (
    EXCERPT_LIMIT,
    FeedItem,
    ParsedFeed,
    parse_date,
    parse_feed,
    strip_html,
    truncate,
)
from app.services.news.ingest import (
    MIN_POLL_INTERVAL,
    NewsIngest,
    NewsIngestReport,
    due_sources,
    load_vocabulary,
)
from app.services.news.payload import (
    browse_payload,
    item_payload,
    source_payload,
    subject_payload,
)
from app.services.news.sources import DEFAULT_SOURCES, NewsSource, seed_sources
from app.services.news.tagging import (
    COMMON_WORDS,
    MIN_TERM_LENGTH,
    RULES,
    Tag,
    Term,
    TermIndex,
    build_terms,
    compile_terms,
    tag,
    tags_as_rows,
)
from app.services.news.vocabulary import catalogue_terms

__all__ = (
    'COMMON_WORDS',
    'DEFAULT_SOURCES',
    'EXCERPT_LIMIT',
    'MIN_POLL_INTERVAL',
    'MIN_TERM_LENGTH',
    'NEWS_MEDIA',
    'RULES',
    'FeedItem',
    'NewsIngest',
    'NewsIngestReport',
    'NewsSource',
    'ParsedFeed',
    'Tag',
    'Term',
    'TermIndex',
    'browse_payload',
    'build_terms',
    'catalogue_terms',
    'compile_terms',
    'due_sources',
    'item_payload',
    'load_vocabulary',
    'news_key',
    'news_releasable',
    'parse_date',
    'parse_feed',
    'seed_sources',
    'source_payload',
    'strip_html',
    'subject_payload',
    'tag',
    'tags_as_rows',
    'truncate',
)
