"""The built-in news sources, and the insert-if-absent seeding that installs them.

``news_sources`` is a *table* precisely so it is not a constant (the plan's §9: a publisher who
objects is one row set to ``enabled = false``, no deploy). But a table nobody has populated is
an empty news stream, and asking every operator to hand-write six INSERTs to get the feature at
all is the same mistake ``comics backfill`` exists to undo. So: a small built-in list, seeded
once, editable afterwards.

**Seeding never re-enables a disabled source.** :func:`seed_sources` inserts only what is
absent, which is the whole promise of that kill switch -- a re-seed on the next deploy must not
undo an operator's decision, and
:meth:`~app.database.repositories.news.NewsRepository.upsert_source` would (its ``ON CONFLICT``
writes ``enabled``), which is why it is called only for slugs that do not exist yet.

Choosing the list
-----------------
§9 asks for official publisher feeds first. Marvel's and DC's own feeds are behind a bot-blocking
edge (both answer ``403`` to any identified client, including ours), so they are **not** in this
list: a URL that cannot be verified to be a working feed would ship as a permanently red row in
``last_status`` pretending to be a source. What is here instead are the trade and comics
newsrooms that syndicate full RSS and are conventionally cited by name -- which is exactly the
shape §9 permits: headline, short excerpt, source name, link out, never the article body.

Every URL below was fetched and confirmed to return an RSS document with items on 2026-08-04.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from app.database.repositories.news import NewsRepository

__all__ = ('DEFAULT_SOURCES', 'NewsSource', 'seed_sources')


class NewsSource(NamedTuple):
    """One built-in feed. ``media`` says which catalogue its stories are tagged against."""

    slug: str
    name: str
    homepage: str
    feed_url: str
    media: str


#: The shipped list. Small on purpose: every source is volume, and news volume is the risk §8
#: names. Two comics newsrooms, one that covers both, three film/TV trades.
DEFAULT_SOURCES: tuple[NewsSource, ...] = (
    NewsSource('comics-beat', 'The Beat', 'https://www.comicsbeat.com/',
               'https://www.comicsbeat.com/feed/', 'comic'),
    NewsSource('bleeding-cool-comics', 'Bleeding Cool (Comics)', 'https://bleedingcool.com/comics/',
               'https://bleedingcool.com/comics/feed/', 'comic'),
    NewsSource('cbr', 'CBR', 'https://www.cbr.com/',
               'https://www.cbr.com/feed/', 'both'),
    NewsSource('deadline', 'Deadline', 'https://deadline.com/',
               'https://deadline.com/feed/', 'title'),
    NewsSource('variety', 'Variety', 'https://variety.com/',
               'https://variety.com/feed/', 'title'),
    NewsSource('hollywood-reporter', 'The Hollywood Reporter', 'https://www.hollywoodreporter.com/',
               'https://www.hollywoodreporter.com/feed/', 'title'),
)


async def seed_sources(
    repository: NewsRepository, sources: tuple[NewsSource, ...] = DEFAULT_SOURCES,
) -> list[str]:
    """Registers any built-in source that is not already a row. Returns the slugs added.

    Idempotent, and *only* additive: an existing slug is left exactly as it is -- disabled,
    renamed, or pointed at a different URL by an operator. Re-running this is a no-op, which is
    what makes it safe to call on every bot start.
    """
    added: list[str] = []
    for source in sources:
        if await repository.get_source(source.slug) is not None:
            continue
        await repository.upsert_source(
            source.slug, source.name, source.feed_url, homepage=source.homepage, media=source.media)
        added.append(source.slug)
    return added
