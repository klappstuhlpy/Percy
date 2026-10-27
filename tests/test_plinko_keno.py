import random

import pytest

from app.cogs.games.engine import keno, plinko


@pytest.mark.parametrize('rows', plinko.ROWS)
@pytest.mark.parametrize('risk', plinko.RISKS)
def test_plinko_rtp(rows: int, risk: str) -> None:
    assert 0.96 <= plinko.rtp(rows, risk) <= 0.98
    table = plinko.multipliers(rows, risk)
    assert len(table) == rows + 1 and table == table[::-1]
    rng = random.Random(1)
    n = 40000
    total = sum(plinko.drop(rows, risk, rng)[2] for _ in range(n)) / n
    assert 0.85 <= total <= 1.1


@pytest.mark.parametrize('picks', range(1, keno.MAX_PICKS + 1))
def test_keno_rtp(picks: int) -> None:
    assert 0.88 <= keno.rtp(picks) <= keno.TARGET_RTP + 0.001
    assert all(m <= keno.MAX_MULTIPLIER for m in keno.paytable(picks).values())


def test_keno_draw_and_score() -> None:
    drawn = keno.draw(random.Random(2))
    assert len(drawn) == keno.DRAWN and all(1 <= n <= keno.POOL for n in drawn)
    hits, mult = keno.score(set(list(drawn)[:3]), drawn)
    assert len(hits) == 3 and mult == keno.paytable(3)[3]
    misses = set(sorted(set(range(1, keno.POOL + 1)) - drawn)[:10])
    assert keno.score(misses, drawn)[1] == 0.0
