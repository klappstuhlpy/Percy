"""Comic catalogue ingest: fetched issues -> the ``comic_*`` archive tables.

See ``migrations/V40__comic_catalogue.sql`` for the tables,
``app/database/repositories/releases.py`` for the data-access layer, and the comic cog's
6 h ``refresh_inventories`` task for the caller.
"""

from app.services.comics.ingest import (
    ComicIngest,
    ComicIngestReport,
    character_rows,
    creator_rows,
    release_row,
    split_series,
)
from app.services.comics.payload import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    browse_payload,
    character_payload,
    credit_payload,
    issue_sort_key,
    names_payload,
    release_payload,
    series_context,
    series_detail_payload,
    series_summary_payload,
)

__all__ = (
    'DEFAULT_PER_PAGE',
    'MAX_PER_PAGE',
    'ComicIngest',
    'ComicIngestReport',
    'browse_payload',
    'character_payload',
    'character_rows',
    'creator_rows',
    'credit_payload',
    'issue_sort_key',
    'names_payload',
    'release_payload',
    'release_row',
    'series_context',
    'series_detail_payload',
    'series_summary_payload',
    'split_series',
)
