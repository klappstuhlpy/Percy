"""Pure helpers for the profile card (best game, title lookup)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils import fuzzy

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.economy.achievements import Achievement


def best_game(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The game with the highest profit, or ``None`` if there is none or nobody is ahead in any game."""
    best = max(rows, key=lambda row: row['profit'], default=None)
    return best if best is not None and best['profit'] > 0 else None


def match_achievement(query: str, earned: Iterable[Achievement]) -> Achievement | None:
    """Resolve ``query`` (id or name, fuzzy) against the earned achievements."""
    pool = list(earned)
    needle = query.strip().casefold()
    for achievement in pool:
        if needle in (achievement.id.casefold(), achievement.name.casefold()):
            return achievement
    names = {achievement.name: achievement for achievement in pool}
    found = fuzzy.extract_one(needle, {name.casefold(): name for name in names}, score_cutoff=70)
    return names[found[2]] if found else None
