"""Universe watchlist: TOML seed parsing + TMDB-backed ingest.

See ``migrations/V38__watchlist_core.sql`` / ``V39__watchlist_progress.sql`` for the tables
this ingests into, ``app/database/repositories/watchlist.py`` for the data-access layer, and
``main.py``'s ``watchlist`` CLI group for the entry point.
"""

from app.services.watchlist.ingest import IngestReport, WatchlistIngest, movie_row, provider_rows, slugify, tv_row
from app.services.watchlist.seeds import (
    EraSeed,
    PathSeed,
    SeedError,
    TitleSeed,
    UniverseSeed,
    accent_to_int,
    load_seed,
)

__all__ = (
    'EraSeed',
    'IngestReport',
    'PathSeed',
    'SeedError',
    'TitleSeed',
    'UniverseSeed',
    'WatchlistIngest',
    'accent_to_int',
    'load_seed',
    'movie_row',
    'provider_rows',
    'slugify',
    'tv_row',
)
