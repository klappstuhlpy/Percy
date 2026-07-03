"""Pure achievement logic (no ``discord`` imports).

Achievements are static milestones evaluated against an
:class:`EconomySnapshot` the cog assembles from the database. Evaluation is
idempotent — it returns *every* achievement the snapshot qualifies for, and the
cog diffs that against the already-earned set to find new unlocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.economy.jobs import JOB_LADDER

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = (
    'ACHIEVEMENTS',
    'Achievement',
    'EconomySnapshot',
    'evaluate_achievements',
    'get_achievement',
)


@dataclass(frozen=True, slots=True)
class EconomySnapshot:
    """Everything achievement checks need to know about a member, in one bundle."""

    net_worth: int = 0
    daily_streak: int = 0
    shifts: int = 0
    job_id: str | None = None
    prestige: int = 0
    has_pet: bool = False
    quests_completed: int = 0
    items_owned: int = 0


@dataclass(frozen=True, slots=True)
class Achievement:
    """A milestone badge with a one-time cash reward."""

    id: str
    name: str
    emoji: str
    description: str
    reward: int
    check: Callable[[EconomySnapshot], bool]


#: The full badge catalogue, in display order.
ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement(
        'pocket_money', 'Pocket Money', '\N{COIN}',
        'Reach a net worth of 1,000.', 100,
        lambda s: s.net_worth >= 1_000,
    ),
    Achievement(
        'entrepreneur', 'Entrepreneur', '\N{CHART WITH UPWARDS TREND}',
        'Reach a net worth of 10,000.', 250,
        lambda s: s.net_worth >= 10_000,
    ),
    Achievement(
        'six_figures', 'Six Figures', '\N{BANKNOTE WITH DOLLAR SIGN}',
        'Reach a net worth of 100,000.', 1_000,
        lambda s: s.net_worth >= 100_000,
    ),
    Achievement(
        'millionaire', 'Millionaire', '\N{MONEY BAG}',
        'Reach a net worth of 1,000,000.', 5_000,
        lambda s: s.net_worth >= 1_000_000,
    ),
    Achievement(
        'dedicated', 'Dedicated', '\N{FIRE}',
        'Hold a 7-day daily streak.', 500,
        lambda s: s.daily_streak >= 7,
    ),
    Achievement(
        'unstoppable', 'Unstoppable', '\N{VOLCANO}',
        'Hold a 30-day daily streak.', 2_500,
        lambda s: s.daily_streak >= 30,
    ),
    Achievement(
        'first_shift', 'First Shift', '\N{BRIEFCASE}',
        'Work your first shift.', 100,
        lambda s: s.shifts >= 1,
    ),
    Achievement(
        'workaholic', 'Workaholic', '\N{FACTORY}',
        'Work 100 shifts.', 1_000,
        lambda s: s.shifts >= 100,
    ),
    Achievement(
        'top_of_the_ladder', 'Top of the Ladder', JOB_LADDER[-1].emoji,
        f'Get hired as {JOB_LADDER[-1].name}.', 2_000,
        lambda s: s.job_id == JOB_LADDER[-1].id,
    ),
    Achievement(
        'ascended', 'Ascended', '\N{GLOWING STAR}',
        'Prestige for the first time.', 2_500,
        lambda s: s.prestige >= 1,
    ),
    Achievement(
        'pet_parent', 'Pet Parent', '\N{PAW PRINTS}',
        'Adopt a pet.', 200,
        lambda s: s.has_pet,
    ),
    Achievement(
        'quartermaster', 'Quartermaster', '\N{SCROLL}',
        'Complete 25 daily quests.', 1_000,
        lambda s: s.quests_completed >= 25,
    ),
    Achievement(
        'collector', 'Collector', '\N{PACKAGE}',
        'Own 50 items at once.', 500,
        lambda s: s.items_owned >= 50,
    ),
)

_ACHIEVEMENTS_BY_ID: dict[str, Achievement] = {a.id: a for a in ACHIEVEMENTS}


def get_achievement(achievement_id: str) -> Achievement | None:
    """The achievement for a stored id, or ``None`` if it was removed."""
    return _ACHIEVEMENTS_BY_ID.get(achievement_id)


def evaluate_achievements(snapshot: EconomySnapshot) -> tuple[str, ...]:
    """Every achievement id ``snapshot`` currently qualifies for, in catalogue order."""
    return tuple(a.id for a in ACHIEVEMENTS if a.check(snapshot))
