"""Death rolls for risky activities (no ``discord`` imports)."""

from __future__ import annotations

import random

__all__ = ('DEATH_CHANCES', 'LIFESAVER_KEY', 'roll_death')

#: ``builtin_key`` of the Lifesaver item, which is consumed instead of dying.
LIFESAVER_KEY = 'lifesaver'

#: Chance of dying per activity. ``hunt`` rolls on every hunt, ``crime`` only on a failed crime and
#: ``search`` only when the search was an injury; ``postmemes`` never kills.
DEATH_CHANCES: dict[str, float] = {'hunt': 0.04, 'crime': 0.10, 'search': 0.25, 'postmemes': 0.0}


def roll_death(activity: str, rng: random.Random | None = None) -> bool:
    """Whether ``activity`` killed the player; an unknown activity never does."""
    chance = DEATH_CHANCES.get(activity, 0.0)
    return chance > 0 and (rng or random).random() < chance
