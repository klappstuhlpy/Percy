"""Pure engine for the turn-based PvP fight.

Two fighters alternate turns. A **punch** deals random damage (crits double it) and
is halved if the target is guarding; **defend** raises a guard for the next hit and
patches up a little HP; **flee** forfeits. First to 0 HP loses. No Discord imports.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

__all__ = ('CRIT_CHANCE', 'MAX_HP', 'Action', 'Fight', 'Turn')

MAX_HP: int = 100
PUNCH_DAMAGE: tuple[int, int] = (8, 20)
CRIT_CHANCE: float = 0.1
DEFEND_HEAL: tuple[int, int] = (3, 8)


class Action(StrEnum):
    PUNCH = 'punch'
    DEFEND = 'defend'
    FLEE = 'flee'


@dataclass(frozen=True, slots=True)
class Turn:
    """What one action did, for the fight log."""

    actor: int
    action: Action
    damage: int = 0
    heal: int = 0
    crit: bool = False
    blocked: bool = False


class Fight:
    """State of one fight between fighter ``0`` (challenger, moves first) and fighter ``1``."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self.hp: list[int] = [MAX_HP, MAX_HP]
        self.guarding: list[bool] = [False, False]
        self.turn: int = 0
        self.winner: int | None = None

    @property
    def finished(self) -> bool:
        return self.winner is not None

    def act(self, action: Action) -> Turn:
        """Apply the current fighter's ``action`` and pass the turn."""
        if self.finished:
            raise RuntimeError('fight is over')
        actor, target = self.turn, 1 - self.turn

        if action is Action.FLEE:
            self.winner = target
            return Turn(actor, action)

        if action is Action.DEFEND:
            self.guarding[actor] = True
            heal = min(self._rng.randint(*DEFEND_HEAL), MAX_HP - self.hp[actor])
            self.hp[actor] += heal
            self.turn = target
            return Turn(actor, action, heal=heal)

        damage = self._rng.randint(*PUNCH_DAMAGE)
        crit = self._rng.random() < CRIT_CHANCE
        if crit:
            damage *= 2
        blocked = self.guarding[target]
        if blocked:
            damage //= 2
            self.guarding[target] = False
        self.hp[target] = max(0, self.hp[target] - damage)
        if self.hp[target] == 0:
            self.winner = actor
        self.turn = target
        return Turn(actor, action, damage=damage, crit=crit, blocked=blocked)
