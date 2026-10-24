from __future__ import annotations

from app.services.economy import MARKET_TAX, MAX_LISTINGS, MAX_PRICE_EACH, market_error, sale_proceeds


def _err(quantity: int = 1, price_each: int = 10, owned: int = 5, open_listings: int = 0) -> str | None:
    return market_error(quantity=quantity, price_each=price_each, owned=owned, open_listings=open_listings)


def test_proceeds_round_tax_down() -> None:
    assert MARKET_TAX == 0.05
    assert sale_proceeds(100, 2) == (200, 10, 190)
    assert sale_proceeds(1, 1) == (1, 0, 1)
    assert sale_proceeds(19, 1) == (19, 0, 19)
    gross, tax, net = sale_proceeds(MAX_PRICE_EACH, 3)
    assert gross == tax + net
    assert tax >= 0


def test_valid_listing() -> None:
    assert _err() is None
    assert _err(quantity=5, price_each=MAX_PRICE_EACH) is None


def test_listing_errors() -> None:
    assert _err(quantity=0)
    assert _err(price_each=0)
    assert _err(price_each=MAX_PRICE_EACH + 1)
    assert _err(open_listings=MAX_LISTINGS)
    assert 'only own 5' in (_err(quantity=6) or '')
