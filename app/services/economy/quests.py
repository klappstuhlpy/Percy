"""Pure daily-quest logic (no ``discord`` imports).

Every member gets a deterministic board of daily quests — the same
``(guild, user, day)`` always produces the same board, so no state is needed
until progress starts. The cog persists rows lazily and bumps ``progress`` from
the matching activity hooks; rewards pay out the moment a quest completes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

__all__ = (
    'DAILY_QUEST_COUNT',
    'QUEST_POOL',
    'Quest',
    'QuestSpec',
    'generate_daily_quests',
)

#: How many quests each member gets per day.
DAILY_QUEST_COUNT = 3


@dataclass(frozen=True, slots=True)
class QuestSpec:
    """A quest template in the pool; ``kind`` is the activity hook that advances it."""

    key: str
    kind: str
    #: Format template; receives ``goal``.
    template: str
    goal_min: int
    goal_max: int
    #: Reward per unit of ``goal``.
    reward_per_unit: int


#: The quest pool. ``kind`` values map to the cog's activity hooks.
QUEST_POOL: tuple[QuestSpec, ...] = (
    QuestSpec('fish_n', 'fish', 'Catch {goal} fish', 3, 6, 80),
    QuestSpec('hunt_n', 'hunt', 'Bag {goal} hunts', 2, 5, 110),
    QuestSpec('work_n', 'work', 'Work {goal} shifts', 1, 3, 150),
    QuestSpec('beg_n', 'beg', 'Beg {goal} times', 4, 8, 40),
    QuestSpec('dig_n', 'dig', 'Dig up {goal} finds', 3, 6, 70),
    QuestSpec('search_n', 'search', 'Search {goal} locations', 3, 6, 70),
    QuestSpec('deposit_amount', 'deposit', 'Deposit {goal} cash into your bank', 500, 2_000, 0),
    QuestSpec('gift_n', 'gift', 'Gift {goal} items to other members', 1, 2, 200),
)


@dataclass(frozen=True, slots=True)
class Quest:
    """One concrete quest on a member's daily board."""

    key: str
    kind: str
    description: str
    goal: int
    reward: int


def _reward_for(spec: QuestSpec, goal: int) -> int:
    """A quest's payout: per-unit reward, or 20% of the goal for amount-based quests."""
    if spec.reward_per_unit:
        return spec.reward_per_unit * goal
    return max(goal // 5, 50)


def generate_daily_quests(
    guild_id: int,
    user_id: int,
    day: datetime.date,
    *,
    count: int = DAILY_QUEST_COUNT,
) -> tuple[Quest, ...]:
    """The deterministic quest board for a member on ``day``.

    Seeded by ``(guild_id, user_id, day)``, so regenerating always yields the
    same board — rows only need persisting once progress is made.
    """
    # str seeds hash via SHA-512 inside Random.seed, so this is stable across processes.
    rng = random.Random(f'{guild_id}:{user_id}:{day.toordinal()}')
    specs = rng.sample(list(QUEST_POOL), k=min(count, len(QUEST_POOL)))
    quests = []
    for spec in specs:
        goal = rng.randint(spec.goal_min, spec.goal_max)
        if spec.key == 'deposit_amount':
            goal = goal // 100 * 100  # round to something friendly
        quests.append(Quest(spec.key, spec.kind, spec.template.format(goal=f'{goal:,}'), goal, _reward_for(spec, goal)))
    return tuple(quests)
