"""Pure drawing logic for the equalizer graph (matplotlib, Agg).

Rendered in the quiet panel style shared with :mod:`app.rendering.templates.charts`:
a 2px Catmull-Rom curve through the band gains over a 10% coral wash, ringed band
markers, a hairline gain grid and a slightly louder zero baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from app.rendering.templates.theme import (
    BRAND,
    LINE_STRONG,
    PAD,
    PANEL_BG,
    SCALE,
    Canvas,
    pt,
    style_axes,
)

if TYPE_CHECKING:
    from io import BytesIO

__all__ = ('draw_equalizer',)

WIDTH, HEIGHT = 800, 300  # logical px
WIDTH_PX, HEIGHT_PX = WIDTH * SCALE, HEIGHT * SCALE

# Lavalink's fixed 15-band equalizer; gains are clamped to its valid range.
BAND_LABELS = ('25', '40', '63', '100', '160', '250', '400', '630', '1K', '1.6K', '2.5K', '4K', '6.3K', '10K', '16K')
MIN_GAIN = -0.25
MAX_GAIN = 1.0


def _smooth_curve(xs: np.ndarray, ys: np.ndarray, samples: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate a Catmull-Rom spline through the points for a smooth curve."""
    if len(xs) < 3:
        return xs, ys

    px = np.concatenate(([xs[0]], xs, [xs[-1]]))
    py = np.concatenate(([ys[0]], ys, [ys[-1]]))
    t = np.linspace(0.0, 1.0, samples, endpoint=False)
    t2, t3 = t * t, t * t * t

    out_x: list[np.ndarray] = []
    out_y: list[np.ndarray] = []
    for i in range(1, len(px) - 2):
        for p, out in ((px, out_x), (py, out_y)):
            p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
            out.append(
                0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
            )

    return np.concatenate([*out_x, xs[-1:]]), np.concatenate([*out_y, ys[-1:]])


def draw_equalizer(gains: list[float]) -> BytesIO:
    """Draws the equalizer band graph for the given gains and returns a PNG buffer."""
    bands = len(gains)
    xs = np.arange(bands, dtype=float)
    ys = np.clip(np.asarray(gains, dtype=float), MIN_GAIN, MAX_GAIN)

    canvas = Canvas(WIDTH, HEIGHT)
    top = canvas.header('Equalizer', 'Gain per band (Hz)')
    ax = canvas.axes(PAD + 40, top + 6, PAD + 6, PAD + 22)
    ax.set_xlim(-0.4, max(bands - 0.6, 0.6))
    ax.set_ylim(MIN_GAIN - 0.06, MAX_GAIN + 0.06)
    ax.set_yticks([-0.25, 0.0, 0.25, 0.5, 0.75, 1.0])
    labels = BAND_LABELS if bands == len(BAND_LABELS) else tuple(str(i + 1) for i in range(bands))
    ax.set_xticks(list(range(bands)), labels)
    style_axes(ax, grid='y')
    ax.yaxis.set_major_formatter(lambda v, _: '0' if v == 0 else f'{v:+.2f}'.rstrip('0').rstrip('.'))
    ax.axhline(0.0, color=LINE_STRONG, linewidth=pt(1), zorder=2)

    if bands:
        curve_x, curve_y = _smooth_curve(xs, ys)
        curve_y = np.clip(curve_y, MIN_GAIN - 0.05, MAX_GAIN + 0.05)  # spline overshoot stays inside the panel
        ax.fill_between(curve_x, curve_y, 0.0, color=BRAND, alpha=0.10, linewidth=0, zorder=3)
        ax.plot(curve_x, curve_y, color=BRAND, linewidth=pt(2), solid_capstyle='round', solid_joinstyle='round', zorder=4)
        ax.scatter(xs, ys, s=pt(10) ** 2, color=BRAND, edgecolor=PANEL_BG, linewidth=pt(2), zorder=5, clip_on=False)

    return canvas.png()
