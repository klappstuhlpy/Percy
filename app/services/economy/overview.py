"""Pure text for the ``cooldowns`` and ``multipliers`` cards (no ``discord`` imports)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    'describe_multipliers',
    'split_cooldowns',
    'tool_uses_left',
)


def split_cooldowns(entries: Iterable[tuple[str, float]]) -> tuple[list[str], list[tuple[str, float]]]:
    """Split ``(name, seconds left)`` pairs into ready names and waiting pairs (soonest first)."""
    ready: list[str] = []
    waiting: list[tuple[str, float]] = []
    for name, left in entries:
        if left > 0:
            waiting.append((name, left))
        else:
            ready.append(name)
    return sorted(ready), sorted(waiting, key=lambda pair: pair[1])


def tool_uses_left(quantity: int, durability: int | None, max_durability: int) -> int:
    """Uses left across ``quantity`` units; the first unit may be partly worn (``durability`` NULL = fresh)."""
    if quantity <= 0:
        return 0
    return (quantity - 1) * max_durability + (durability if durability is not None else max_durability)


def _pct(factor: float) -> str:
    return f'{(factor - 1) * 100:+.0f}%'


def describe_multipliers(
    *,
    guild: float,
    prestige: float,
    loot: float,
    xp: float,
    vote: float,
    tools: Iterable[tuple[str, int | None]],
) -> list[str]:
    """One line per factor, then the combined earning multiplier (guild x prestige).

    ``tools`` is ``(tool name, uses left)``; ``None`` or 0 means the member has none.
    """
    lines = [
        f'Server payout: **x{guild:g}**',
        f'Prestige bonus: **x{prestige:g}** ({_pct(prestige)})',
        f'Loot boost: **x{loot:g}**' + ('' if loot > 1 else ' (none active)'),
        f'XP boost: **x{xp:g}**' + ('' if xp > 1 else ' (none active)'),
        f'Vote XP boost: **x{vote:g}**' + ('' if vote > 1 else ' (none active)'),
    ]
    lines.extend(f'{name}: ' + (f'**{uses}** uses left' if uses else 'not owned') for name, uses in tools)
    lines.append(f'Combined earning multiplier: **x{guild * prestige:.2f}**')
    return lines
