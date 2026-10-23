"""Pure trade rules: one side's offer, the checks around it and how it reads.

The UI keeps a :class:`TradeOffer` per member; the repository's ``execute_trade``
does the atomic exchange. Nothing here needs Discord or a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    'MAX_TRADE_LINES',
    'TradeOffer',
    'summarize_offer',
    'trade_error',
    'trade_failure_message',
)

#: Most distinct lines (items plus cash) one side may put on the table.
MAX_TRADE_LINES = 10


@dataclass(slots=True)
class TradeOffer:
    """What one member puts up: ``items`` maps item id to quantity."""

    items: dict[int, int] = field(default_factory=dict)
    cash: int = 0
    confirmed: bool = False

    @property
    def lines(self) -> int:
        return len(self.items) + (1 if self.cash else 0)

    @property
    def empty(self) -> bool:
        return not self.items and not self.cash


def trade_error(initiator_id: int, partner_id: int, *, partner_is_bot: bool) -> str | None:
    """Why ``initiator`` cannot open a trade with ``partner`` (``None`` when they can)."""
    if partner_is_bot:
        return 'Bots do not trade.'
    if initiator_id == partner_id:
        return 'You cannot trade with yourself.'
    return None


def summarize_offer(offer: TradeOffer, names: Mapping[int, str], cash_text: str = '') -> str:
    """One line per offered item (and the cash, via ``cash_text``), or an empty marker."""
    lines = [f'×**{qty:,}** {names.get(item_id, "Unknown item")}' for item_id, qty in offer.items.items()]
    if offer.cash:
        lines.append(cash_text or f'{offer.cash:,} cash')
    return '\n'.join(lines) or '*Nothing yet*'


def trade_failure_message(reason: str) -> str:
    """The user-facing text for an ``execute_trade`` failure code."""
    return {
        'cash': 'Someone no longer has the cash they offered.',
        'items': 'Someone no longer owns the items they offered.',
        'limit': 'An item would exceed its per-member holding limit for the receiver.',
    }.get(reason, 'The trade could not be completed.')
