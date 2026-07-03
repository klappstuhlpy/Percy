"""Pure pet-companion logic (no ``discord`` imports).

A member owns at most one pet. Pets accrue passive earnings between claims —
capped at each species' storage window — scaled by how recently they were fed:
a well-fed pet earns full rate, a hungry one half, a starving one nothing. The
cog persists only ``(species, name, last_fed, last_claim)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime

__all__ = (
    'HUNGRY_AFTER_HOURS',
    'PET_SPECIES',
    'STARVING_AFTER_HOURS',
    'HungerState',
    'PetClaim',
    'PetSpecies',
    'compute_pet_claim',
    'get_species',
    'hunger_state',
)

#: Hours since feeding after which a pet earns at half rate.
HUNGRY_AFTER_HOURS = 18.0
#: Hours since feeding after which a pet stops earning entirely.
STARVING_AFTER_HOURS = 36.0


class HungerState(StrEnum):
    """How hungry a pet currently is; drives the earning factor."""

    FED = 'fed'
    HUNGRY = 'hungry'
    STARVING = 'starving'

    @property
    def factor(self) -> float:
        """The earnings multiplier this state applies."""
        return {HungerState.FED: 1.0, HungerState.HUNGRY: 0.5, HungerState.STARVING: 0.0}[self]


@dataclass(frozen=True, slots=True)
class PetSpecies:
    """An adoptable species: purchase cost, passive earn rate and upkeep."""

    id: str
    name: str
    emoji: str
    cost: int
    #: Cash accrued per hour while well fed.
    hourly_rate: int
    #: Earnings stop accruing past this many unclaimed hours.
    storage_hours: int
    #: Cost of one feeding.
    feed_cost: int


#: The adoption catalogue, cheapest first.
PET_SPECIES: tuple[PetSpecies, ...] = (
    PetSpecies('hamster', 'Hamster', '\N{HAMSTER FACE}', 500, 8, 12, 25),
    PetSpecies('cat', 'Cat', '\N{CAT FACE}', 2_500, 20, 18, 60),
    PetSpecies('dog', 'Dog', '\N{DOG FACE}', 3_000, 26, 14, 80),
    PetSpecies('parrot', 'Parrot', '\N{BIRD}', 6_000, 45, 20, 120),
    PetSpecies('fox', 'Fox', '\N{FOX FACE}', 12_000, 80, 24, 200),
    PetSpecies('dragon', 'Dragon', '\N{DRAGON FACE}', 50_000, 250, 36, 600),
)

_SPECIES_BY_ID: dict[str, PetSpecies] = {species.id: species for species in PET_SPECIES}


def get_species(species_id: str) -> PetSpecies | None:
    """The species for a stored id, or ``None`` if it was removed from the catalogue."""
    return _SPECIES_BY_ID.get(species_id)


def hunger_state(last_fed: datetime.datetime, *, now: datetime.datetime) -> HungerState:
    """How hungry a pet fed at ``last_fed`` is at ``now``."""
    hours = max((now - last_fed).total_seconds() / 3600, 0.0)
    if hours >= STARVING_AFTER_HOURS:
        return HungerState.STARVING
    if hours >= HUNGRY_AFTER_HOURS:
        return HungerState.HUNGRY
    return HungerState.FED


@dataclass(frozen=True, slots=True)
class PetClaim:
    """The resolved earnings of one pet claim."""

    amount: int
    #: Hours that actually counted (capped at the species' storage window).
    hours: float
    hunger: HungerState


def compute_pet_claim(
    species: PetSpecies,
    last_claim: datetime.datetime,
    last_fed: datetime.datetime,
    *,
    now: datetime.datetime,
) -> PetClaim:
    """Resolve a pet claim: rate times capped hours since last claim, scaled by hunger."""
    hours = min(max((now - last_claim).total_seconds() / 3600, 0.0), float(species.storage_hours))
    state = hunger_state(last_fed, now=now)
    amount = int(species.hourly_rate * hours * state.factor)
    return PetClaim(amount, hours, state)
