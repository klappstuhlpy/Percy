"""Pure drawing logic for the equalizer graph (matplotlib, Agg).

Rendered in the klappstuhl.me admin panel style shared with
:mod:`app.rendering.templates.charts`: a smooth Catmull-Rom curve through the
band gains with a translucent coral area fill, round band markers and a subtle
gain grid. Replaces the old Pillow renderer that drew over a template asset.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

import numpy as np

from app.rendering.templates.theme import (
    BRAND,
    BRAND_BRIGHT,
    GRID,
    MUTED,
    PANEL_BG,
    RUBIK,
    add_panel_title,
    figure_to_image,
    panel_figure,
)

if TYPE_CHECKING:
    from matplotlib.axes import Axes

__all__ = ('draw_equalizer', 'draw_progress_bar')

from PIL import Image, ImageDraw, ImageFont

from app.rendering.primitives import ASSETS

BAR_WIDTH = 240
BAR_HEIGHT = 12
_STEPS = 50

_BAR_CACHE: dict[tuple[int, str], bytes] = {}

_BRAND_CORAL = (217, 119, 87)
_LIVE_RED = (220, 53, 53)
_TRACK_BG = (55, 55, 60)
_TRACK_EMPTY = (39, 39, 42)
_INDICATOR_SIZE = 10

_LIVE_FONT_PATH = ASSETS / 'fonts' / 'ginto-bold.otf'
_LIVE_FONT: ImageFont.FreeTypeFont | None = None


def _get_live_font() -> ImageFont.FreeTypeFont:
    global _LIVE_FONT
    if _LIVE_FONT is None:
        _LIVE_FONT = ImageFont.truetype(str(_LIVE_FONT_PATH), 9)
    return _LIVE_FONT


def _draw_position_bar(ratio: int) -> Image.Image:
    """Thin track line with a circular playhead indicator."""
    img = Image.new('RGBA', (BAR_WIDTH, BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    track_y = BAR_HEIGHT // 2
    track_h = 4
    y0 = track_y - track_h // 2
    y1 = track_y + track_h // 2

    # Full track background
    draw.rounded_rectangle((0, y0, BAR_WIDTH - 1, y1), radius=2, fill=_TRACK_BG)

    # Filled portion
    fill_x = int((ratio / _STEPS) * BAR_WIDTH)
    if fill_x > 0:
        draw.rounded_rectangle((0, y0, fill_x, y1), radius=2, fill=_BRAND_CORAL)

    # Playhead circle
    cx = max(_INDICATOR_SIZE // 2, min(fill_x, BAR_WIDTH - _INDICATOR_SIZE // 2))
    draw.ellipse(
        (cx - _INDICATOR_SIZE // 2, track_y - _INDICATOR_SIZE // 2,
         cx + _INDICATOR_SIZE // 2, track_y + _INDICATOR_SIZE // 2),
        fill=_BRAND_CORAL,
    )
    return img


def _draw_volume_bar(ratio: int) -> Image.Image:
    """Minimal thin rounded bar for volume."""
    img = Image.new('RGBA', (BAR_WIDTH, BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    track_y = BAR_HEIGHT // 2
    track_h = 6
    y0 = track_y - track_h // 2
    y1 = track_y + track_h // 2

    draw.rounded_rectangle((0, y0, BAR_WIDTH - 1, y1), radius=3, fill=_TRACK_EMPTY)

    fill_x = int((ratio / _STEPS) * BAR_WIDTH)
    if fill_x > 0:
        draw.rounded_rectangle((0, y0, fill_x, y1), radius=3, fill=_BRAND_CORAL)

    return img


def _draw_live_bar() -> Image.Image:
    """Same height as position bar: red track split by bold 'LIVE' text in the middle."""
    font = _get_live_font()
    bbox = font.getbbox('LIVE')
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    gap = 6

    img = Image.new('RGBA', (BAR_WIDTH, BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    track_y = BAR_HEIGHT // 2
    track_h = 4
    y0 = track_y - track_h // 2
    y1 = track_y + track_h // 2

    mid = BAR_WIDTH // 2
    left_end = mid - text_w // 2 - gap
    right_start = mid + text_w // 2 + gap

    # Left half of bar
    draw.rounded_rectangle((0, y0, left_end, y1), radius=2, fill=_LIVE_RED)
    # Right half of bar
    draw.rounded_rectangle((right_start, y0, BAR_WIDTH - 1, y1), radius=2, fill=_LIVE_RED)

    # Centered LIVE text
    tx = mid - text_w // 2
    ty = (BAR_HEIGHT - text_h) // 2 - bbox[1]
    draw.text((tx, ty), 'LIVE', fill=(255, 255, 255), font=font)

    return img


def draw_progress_bar(ratio: int, *, variant: str = 'position') -> BytesIO:
    """Draw a minimal progress bar. Cached by (ratio, variant).

    Parameters
    ----------
    ratio: int
        Fill level from 0 to 50.
    variant: str
        'position' draws a thin track with playhead; 'volume' draws a slim filled bar;
        'live' draws a red bar with LIVE text.
    """
    ratio = max(0, min(ratio, _STEPS))
    key = (ratio, variant)
    if key in _BAR_CACHE:
        buf = BytesIO(_BAR_CACHE[key])
        buf.seek(0)
        return buf

    if variant == 'position':
        img = _draw_position_bar(ratio)
    elif variant == 'live':
        img = _draw_live_bar()
    else:
        img = _draw_volume_bar(ratio)

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    _BAR_CACHE[key] = buf.getvalue()
    buf.seek(0)
    return buf

WIDTH_PX = 1600
HEIGHT_PX = 600
CHART_DPI = 100

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

    figure = panel_figure(WIDTH_PX, HEIGHT_PX, CHART_DPI)
    add_panel_title(figure, 'Equalizer', width_px=WIDTH_PX, height_px=HEIGHT_PX)

    ax: Axes = figure.add_axes((90 / WIDTH_PX, 70 / HEIGHT_PX, 1 - 130 / WIDTH_PX, 1 - 170 / HEIGHT_PX))
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_xlim(-0.5, max(bands - 0.5, 0.5))
    ax.set_ylim(MIN_GAIN - 0.07, MAX_GAIN + 0.06)

    # Subtle gain grid with an emphasized zero line.
    ax.set_yticks([-0.25, 0.0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(
        ['-0.25', '0', '+0.25', '+0.50', '+0.75', '+1.00'], fontproperties=RUBIK, fontsize=12, color=MUTED
    )
    ax.grid(axis='y', color=GRID, linewidth=1, zorder=1)
    ax.axhline(0.0, color=MUTED, linewidth=1, alpha=0.55, zorder=2)

    labels = BAND_LABELS if bands == len(BAND_LABELS) else tuple(str(i + 1) for i in range(bands))
    ax.set_xticks(list(range(bands)))
    ax.set_xticklabels(labels, fontproperties=RUBIK, fontsize=12, color=MUTED)
    ax.tick_params(length=0, pad=12)

    if bands:
        curve_x, curve_y = _smooth_curve(xs, ys)
        curve_y = np.clip(curve_y, MIN_GAIN - 0.05, MAX_GAIN + 0.04)  # spline overshoot stays inside the panel

        ax.fill_between(curve_x, curve_y, 0.0, color=BRAND, alpha=0.18, linewidth=0, zorder=3)
        ax.plot(
            curve_x, curve_y, color=BRAND, linewidth=3,
            solid_capstyle='round', solid_joinstyle='round', zorder=4,
        )
        ax.scatter(xs, ys, s=90, color=BRAND_BRIGHT, edgecolor=PANEL_BG, linewidth=2.5, zorder=5)

    image = figure_to_image(figure).convert('RGB')
    figure.clear()

    buffer = BytesIO()
    image.save(buffer, format='png')
    buffer.seek(0)
    return buffer
