"""Collection book summary (no ``discord`` imports): which catalogue drops a member has found."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.economy.catalogue import CATALOGUE

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    'DROP_KEYS',
    'CollectionGroup',
    'collection_summary',
)

#: Keys of every catalogue drop (tools, Banknote and Lifesaver are not collectable).
DROP_KEYS: frozenset[str] = frozenset(i.key for i in CATALOGUE if i.drop_from)

UNDISCOVERED = '???'


@dataclass(frozen=True, slots=True)
class CollectionGroup:
    """One activity's drops: ``entries`` are ``(label, found)`` in catalogue order."""

    activity: str
    entries: tuple[tuple[str, bool], ...]

    @property
    def found(self) -> int:
        return sum(1 for _, ok in self.entries if ok)

    @property
    def total(self) -> int:
        return len(self.entries)


def collection_summary(found_keys: Iterable[str]) -> tuple[CollectionGroup, ...]:
    """Group the catalogue drops by activity; undiscovered ones are labelled ``???``."""
    found = set(found_keys)
    groups: dict[str, list[tuple[str, bool]]] = {}
    for item in CATALOGUE:
        if not item.drop_from:
            continue
        seen = item.key in found
        groups.setdefault(item.drop_from, []).append((f'{item.emoji} {item.name}' if seen else UNDISCOVERED, seen))
    return tuple(CollectionGroup(activity, tuple(entries)) for activity, entries in groups.items())
