"""Release subscriptions: subject derivation, fan-out and digest building.

See ``migrations/V42__subscriptions.sql`` for the tables,
``app/database/repositories/releases.py`` for the data-access layer, and the plan's §4.1 for
why one subscription table serves both media.
"""

from app.services.releases.digest import DEFAULT_LIMIT, NEWS_LIMIT, Digest, DigestGroup, build_digest
from app.services.releases.overview import (
    BRAND_LABELS,
    Entry,
    build_overview,
    comic_entry,
    group_rows,
    title_entry,
)
from app.services.releases.progress import PROGRESS_STATUSES, check_status, progress_payload
from app.services.releases.subjects import (
    COMIC_SUBJECT_TYPES,
    STREAMS,
    TITLE_SUBJECT_TYPES,
    Releasable,
    Subject,
    comic_releasable,
    match,
    normalise,
    title_releasable,
)

__all__ = (
    'BRAND_LABELS',
    'COMIC_SUBJECT_TYPES',
    'DEFAULT_LIMIT',
    'NEWS_LIMIT',
    'PROGRESS_STATUSES',
    'STREAMS',
    'TITLE_SUBJECT_TYPES',
    'Digest',
    'DigestGroup',
    'Entry',
    'Releasable',
    'Subject',
    'build_digest',
    'build_overview',
    'check_status',
    'comic_entry',
    'comic_releasable',
    'group_rows',
    'match',
    'normalise',
    'progress_payload',
    'title_entry',
    'title_releasable',
)
