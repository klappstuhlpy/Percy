"""Pure player-market rules: listing limits, validation and what a sale pays out.

The repository's ``market_*`` methods do the atomic escrow and purchase; nothing
here needs Discord or a database.
"""

from __future__ import annotations

__all__ = (
    'MARKET_TAX',
    'MAX_LISTINGS',
    'MAX_PRICE_EACH',
    'market_error',
    'sale_proceeds',
)

#: Share of a sale the seller pays; it is removed from the economy (a sink).
MARKET_TAX = 0.05

#: Most open listings one seller may have at a time.
MAX_LISTINGS = 10

#: Highest asking price for a single unit.
MAX_PRICE_EACH = 100_000_000


def market_error(*, quantity: int, price_each: int, owned: int, open_listings: int) -> str | None:
    """Why a listing is refused (``None`` when it is fine)."""
    if quantity < 1:
        return 'You have to list at least one.'
    if price_each < 1:
        return 'The price must be at least 1.'
    if price_each > MAX_PRICE_EACH:
        return f'The price per item cannot exceed {MAX_PRICE_EACH:,}.'
    if open_listings >= MAX_LISTINGS:
        return f'You can only have {MAX_LISTINGS} listings open at a time.'
    if owned < quantity:
        return f'You only own {owned:,} of that item.'
    return None


def sale_proceeds(price_each: int, quantity: int) -> tuple[int, int, int]:
    """``(gross, tax, net)`` for a sale; the tax is rounded down and never negative."""
    gross = price_each * quantity
    tax = max(int(gross * MARKET_TAX), 0)
    return gross, tax, gross - tax
