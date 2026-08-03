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

__all__ = (
    'ComicIngest',
    'ComicIngestReport',
    'character_rows',
    'creator_rows',
    'release_row',
    'split_series',
)
