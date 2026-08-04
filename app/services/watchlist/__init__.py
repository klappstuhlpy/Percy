"""Universe watchlist: TOML seed parsing + TMDB-backed ingest.

See ``migrations/V38__watchlist_core.sql`` / ``V39__watchlist_progress.sql`` for the tables
this ingests into, ``app/database/repositories/watchlist.py`` for the data-access layer, and
``main.py``'s ``watchlist`` CLI group for the entry point.
"""

from app.services.watchlist.ingest import IngestReport, WatchlistIngest, movie_row, provider_rows, slugify, tv_row
from app.services.watchlist.payload import (
    credit_payloads,
    person_payload,
    person_summary,
    universe_payload,
    universe_stats,
    universe_summary,
)
from app.services.watchlist.people import CAST_LIMIT, CREW_ROLES, credit_rows
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
    'CAST_LIMIT',
    'CREW_ROLES',
    'EraSeed',
    'IngestReport',
    'PathSeed',
    'SeedError',
    'TitleSeed',
    'UniverseSeed',
    'WatchlistIngest',
    'accent_to_int',
    'credit_payloads',
    'credit_rows',
    'load_seed',
    'movie_row',
    'person_payload',
    'person_summary',
    'provider_rows',
    'slugify',
    'tv_row',
    'universe_payload',
    'universe_stats',
    'universe_summary',
)
