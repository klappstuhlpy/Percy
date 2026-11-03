"""Pure drawing logic for the poker hand-strength table.

The data's job is *change across streets*, so it is drawn as small multiples in one
table rather than one bar chart per street: a row per hand category (every category,
strongest on top, so rows line up), a column per street, and in each cell a short bar
on one shared 0-100% scale beside the exact percentage. The most likely hand of each
street is the only loud mark; a hand that has become impossible shows a dash. A final
row answers the question a player actually decides on: the best hand they hold at
least half the time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matplotlib.colors import to_hex, to_rgb

from app.rendering.templates.theme import BRAND, FAINT, FOREGROUND, LINE, MUTED, PAD, PANEL_BG, TRACK_BG, Canvas

if TYPE_CHECKING:
    from io import BytesIO

    from app.rendering.models import HandOddsData

__all__ = ('draw_hand_odds', 'likely_floor')

WIDTH = 900
ROW = 30
LABEL_WIDTH = 150
HEAD_ROW = 30
FOOT_ROW = 44
BAR_HEIGHT = 10
VALUE_WIDTH = 64  # right-aligned percentage column inside each cell

_QUIET_BAR = to_hex(tuple(a * 0.45 + b * 0.55 for a, b in zip(to_rgb(BRAND), to_rgb(PANEL_BG))))


def likely_floor(odds: dict[str, float], hands: tuple[str, ...]) -> tuple[str, float] | None:
    """The strongest hand held *at least* 50% of the time, with that cumulative chance.

    ``hands`` is ordered weakest first; "at least X" sums X and everything stronger.
    """
    cumulative = 0.0
    for hand in reversed(hands):
        cumulative += odds.get(hand, 0.0)
        if cumulative >= 50:
            return hand, min(cumulative, 100.0)
    return None


def _percent(value: float) -> str:
    text = f'{value:.1f}' if value >= 1 else f'{value:.2f}'.rstrip('0').rstrip('.')
    return f'{text}%'


def draw_hand_odds(data: HandOddsData) -> BytesIO:
    """One table: hands down the side, streets across, a bar and a number per cell."""
    hands = list(reversed(data.hands))  # strongest on top
    stages = data.stages or ['—']
    top_offset = PAD + (60 if data.subtitle else 36)
    height = int(top_offset + HEAD_ROW + len(hands) * ROW + FOOT_ROW + PAD)

    canvas = Canvas(WIDTH, height)
    canvas.header(data.title, data.subtitle)

    left = PAD + LABEL_WIDTH
    col = (WIDTH - left - PAD) / len(stages)
    bar_max = col - VALUE_WIDTH - 16

    y = top_offset
    for i, stage in enumerate(stages):
        canvas.text(left + i * col, y + HEAD_ROW / 2, stage, size=12, color=MUTED, va='center')
    y += HEAD_ROW
    canvas.box(PAD, y - 1, WIDTH - 2 * PAD, 1, LINE)

    leaders = [max(odds, key=odds.__getitem__) if odds else None for odds in data.odds]
    for hand in hands:
        mid = y + ROW / 2
        canvas.text(PAD, mid, hand, size=13, va='center')
        for i, odds in enumerate(data.odds):
            x = left + i * col
            value = odds.get(hand, 0.0)
            canvas.box(x, mid - BAR_HEIGHT / 2, bar_max, BAR_HEIGHT, TRACK_BG, radius=BAR_HEIGHT / 2)
            if value <= 0:
                canvas.text(x + bar_max + VALUE_WIDTH, mid, '—', size=12, color=FAINT, ha='right', va='center')
                continue
            loud = hand == leaders[i]
            width = max(bar_max * value / 100, float(BAR_HEIGHT))  # a trace amount reads as a dot
            canvas.box(x, mid - BAR_HEIGHT / 2, width, BAR_HEIGHT, BRAND if loud else _QUIET_BAR, radius=BAR_HEIGHT / 2)
            canvas.text(
                x + bar_max + VALUE_WIDTH, mid, _percent(value), size=12.5,
                color=FOREGROUND if loud else MUTED, ha='right', va='center',
            )
        y += ROW

    canvas.box(PAD, y + 4, WIDTH - 2 * PAD, 1, LINE)
    mid = y + 4 + FOOT_ROW / 2
    canvas.text(PAD, mid, 'Likely at least', size=12, color=MUTED, va='center')
    for i, odds in enumerate(data.odds):
        floor = likely_floor(odds, data.hands)
        x = left + i * col
        if floor is None:
            canvas.text(x, mid, '—', size=12, color=FAINT, va='center')
            continue
        hand, chance = floor
        canvas.text(x, mid, hand, size=13, va='center')
        canvas.text(x + bar_max + VALUE_WIDTH, mid, f'{chance:.0f}%', size=12.5, color=MUTED, ha='right', va='center')

    return canvas.png()
