"""Grouping a user's matched releasables into the one digest they get DMed.

Pure, and deliberately free of any formatting: this decides *what* is in a digest and in what
order, the cog decides what it looks like. One digest per user per run is the anti-spam rule
from the plan's D8 -- three matched items are three lines, never three DMs.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterable

    from app.services.releases.subjects import Releasable

__all__ = ('DEFAULT_LIMIT', 'NEWS_LIMIT', 'Digest', 'DigestGroup', 'build_digest')

#: How many items a digest shows before it becomes "and N more". Sized for a Discord embed --
#: a dozen lines is skimmable, thirty is a wall no one reads.
DEFAULT_LIMIT = 12

#: How many **news** stories one digest carries, capped separately from the releases above.
#:
#: The plan's §4.4 and §8 both say the same thing: news is far higher-frequency than releases,
#: and a busy news week must never crowd out the release someone actually subscribed for. One
#: shared cap would do exactly that -- a franchise producing a dozen stories a day would fill
#: all twelve slots and the issue shipping that Wednesday would fall off the end. So the two
#: halves are capped independently and the release half is never squeezed.
#:
#: Five, because the dispatch runs hourly and the caller takes this slice *before* claiming
#: delivery: whatever does not fit is still unclaimed and leads the next hour's digest, so a
#: burst drains at 120 stories a day rather than being dropped. Five lines is also about as much
#: news as sits under a release list without the DM reading as a newsletter.
NEWS_LIMIT = 5


@dataclass(frozen=True, slots=True)
class DigestGroup:
    """One heading in a digest: a medium and a release date, with the items under it.

    ``date`` is ``None`` for items with no release date -- announced but not yet dated, which
    is exactly the "what's coming" a subscriber wants, so they group rather than disappear.
    """

    media: str
    date: datetime.date | None
    items: list[Releasable]


@dataclass(frozen=True, slots=True)
class Digest:
    """What one user is told about in one run.

    ``shown`` counts the items in :attr:`groups`; ``overflow`` counts the ones that did not fit
    the cap, i.e. the "and N more" tail. A digest with no groups is never sent.
    """

    user_id: int
    groups: list[DigestGroup]
    shown: int
    overflow: int


def _order(item: Releasable) -> tuple[str, int, int, str]:
    """Sort key: medium, then release date **descending** with undated items first, then name.

    Dates sort descending via their negated ordinal, since a date cannot be negated directly.
    Undated items lead their medium because an announced-but-undated title is news, not
    backlog.
    """
    if item.release_date is None:
        return (item.media, 0, 0, item.name)
    return (item.media, 1, -item.release_date.toordinal(), item.name)


def build_digest(user_id: int, items: Iterable[Releasable], *, limit: int = DEFAULT_LIMIT) -> Digest:
    """Groups ``items`` into a capped digest for one user.

    Ordered by medium, then by release date descending; the first ``limit`` items are kept and
    the rest counted into :attr:`Digest.overflow`. The cap is applied to the *flat* ordering
    before grouping, so a digest never shows a half-empty group it had to truncate.

    Empty input returns a digest with no groups rather than ``None`` -- callers check
    ``digest.groups`` and skip the DM, which keeps "nothing happened" off the error path.
    """
    ordered = sorted(items, key=_order)
    shown = ordered[:max(limit, 0)]
    groups = [
        DigestGroup(media=media, date=date, items=list(group))
        for (media, date), group in groupby(shown, key=lambda item: (item.media, item.release_date))
    ]
    return Digest(user_id=user_id, groups=groups, shown=len(shown), overflow=len(ordered) - len(shown))
