"""A tagged story, as the release fan-out sees it.

Pure. One function, and the whole reason it is only one: **news reuses the release matcher
unchanged**. :func:`~app.services.releases.subjects.match` already knows about muted delivery,
the ``streams`` dial, the backlog cutoff and per-user dedupe, and it takes
:class:`~app.services.releases.subjects.Releasable`\\s -- so the entire news half of the
dispatch is "turn a ``news_items`` row plus its ``news_subjects`` rows into a releasable" and
then the existing code path.

Two decisions live here.

**``media`` is the constant** :data:`NEWS_MEDIA`, not the medium of the subjects that tagged the
story. A story about a comic *and* its film adaptation carries subjects in both catalogues, and
if it became two releasables it would take two delivery claims and appear twice in one DM.
``match`` dedupes per user by :attr:`Releasable.key`, so one releasable carrying both media's
subjects is told once -- and because the claim is ``(user, media, key)``, a constant ``media``
is also what stops two consecutive runs claiming the same story under two different names.
``release_deliveries.media`` deliberately carries no CHECK constraint (``V42``), so this is a
legal third value rather than a schema change.

**``key`` is** ``news/{id}``, so the namespace §4.4 promises holds: a comic issue
(``{brand}/{slug}``), a film (``{universe}/{slug}``) and a story can never collide on one key.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from app.services.releases.subjects import Releasable, Subject

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ('NEWS_MEDIA', 'news_key', 'news_releasable')

#: The ``media`` value news travels under, in ``release_deliveries`` and on a releasable.
NEWS_MEDIA = 'news'


def news_key(news_id: int | str) -> str:
    """The ``release_key`` for one story."""
    return f'news/{news_id}'


def _subject(entry: Any) -> Subject | None:
    """Reads one ``news_subjects`` row (or a bare triple) into a subject."""
    if isinstance(entry, tuple):
        media, subject_type, value = entry
    else:
        media, subject_type, value = entry['media'], entry['subject_type'], entry['subject_value']
    subject = Subject.of(media, subject_type, value)
    return subject if subject.value else None


def news_releasable(
    row: Mapping[str, Any],
    subjects: Iterable[Any] = (),
    *,
    source: str | None = None,
) -> Releasable:
    """Maps a ``news_items`` row plus its tags onto a releasable the matcher understands.

    ``first_seen`` is ``fetched_at`` -- when *we* learned about the story, which is what the
    matcher's backlog cutoff compares a subscription's ``created_at`` against. Using
    ``published_at`` there would let a feed's week-old back-fill look older than a subscription
    made yesterday and vanish. ``release_date`` is the published date, so the digest groups a
    day's stories together.

    ``source`` is the publisher's display name, and it is not decoration: §9 requires every
    story to name its source and link the original wherever it is shown, the DM included.
    """
    published = row['published_at']
    return Releasable(
        media=NEWS_MEDIA,
        key=news_key(row['id']),
        name=str(row['headline'] or '').strip(),
        release_date=published.date() if isinstance(published, datetime.datetime) else published,
        first_seen=row['fetched_at'],
        cover_url=row['image_url'],
        url=row['url'],
        subjects=frozenset(s for s in (_subject(entry) for entry in subjects) if s is not None),
        source=source,
    )
