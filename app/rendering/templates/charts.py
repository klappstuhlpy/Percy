"""Pure drawing logic for the data charts (matplotlib, Agg) plus the avatar collage (Pillow).

Every chart is laid out on a :class:`~app.rendering.templates.theme.Canvas` in logical
pixels and follows the shared quiet style: sentence-case title over a muted subtitle,
hairline solid gridlines, faint compact tick labels, flat coral marks with a 4px rounded
data end and a square baseline, 2px lines over a 10% wash, and values labelled
selectively (the tip of a bar, the end and the peak of a line) instead of everywhere.

Chart figures use the object-oriented matplotlib API (:class:`~matplotlib.figure.Figure`
with an explicit Agg canvas) instead of ``pyplot``, which holds global state and is not
thread-safe under ``asyncio.to_thread``.
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import TYPE_CHECKING

from matplotlib.colors import LinearSegmentedColormap, to_hex, to_rgb
from matplotlib.textpath import TextPath
from PIL import Image

from app.rendering.primitives import resize_to_limit
from app.rendering.templates.theme import (
    BRAND,
    BRAND_BRIGHT,
    FAINT,
    FONT,
    FOREGROUND,
    LINE,
    LINE_STRONG,
    MUTED,
    PAD,
    PANEL_BG,
    SCALE,
    TRACK_BG,
    Canvas,
    compact,
    nice_ticks,
    pt,
    rounded_bar,
    style_axes,
)

if TYPE_CHECKING:
    from app.rendering.models import BarChartData, HeatmapData, PresenceData, SeriesData

__all__ = (
    'draw_avatar_collage',
    'draw_heatmap_chart',
    'draw_presence_chart',
    'draw_series_chart',
    'merge_images_vertical',
    'render_bar_chart_images',
)

CHART_WIDTH = 960  # logical px
CHART_WIDTH_PX = CHART_WIDTH * SCALE
ROW_HEIGHT = 34
BAR_THICKNESS = 14  # <= 24px, the band's leftover stays air
AXIS_HEIGHT = 26
MAX_BARS_PER_CHART = 18
MIN_LABEL_WIDTH = 110  # keeps bar baselines aligned when charts are stacked


def _mix(color: str, other: str, amount: float) -> str:
    """``amount`` of ``color`` blended over ``other``."""
    a, b = to_rgb(color), to_rgb(other)
    return to_hex(tuple(x * amount + y * (1 - amount) for x, y in zip(a, b)))


# Sequential: one hue, from barely-there to bright. Zero is the empty surface step.
HEAT_CMAP = LinearSegmentedColormap.from_list('heat', [_mix(BRAND, PANEL_BG, 0.28), BRAND, BRAND_BRIGHT])


def _text_px(text: str, size: float) -> float:
    """Rendered width of ``text`` at ``size`` logical px, in logical px."""
    if not text:
        return 0.0
    return TextPath((0, 0), text, prop=FONT, size=pt(size)).get_extents().width / 0.72


def _header_height(title: str, subtitle: str) -> float:
    if not title:
        return PAD
    return PAD + (60 if subtitle else 36)


# -- Horizontal bar chart ------------------------------------------------------


def _draw_bar_chart_page(values: dict[str, int | float], title: str, subtitle: str, ticks: list[float]) -> Image.Image:
    rows = len(values)
    top = _header_height(title, subtitle)
    height = int(top + max(rows, 1) * ROW_HEIGHT + AXIS_HEIGHT + PAD)
    canvas = Canvas(CHART_WIDTH, height)
    if title:
        canvas.header(title, subtitle)
    if not rows:
        canvas.empty(top)
        return canvas.image()

    labels = list(values)
    numbers = list(values.values())
    xmax = ticks[-1]

    label_w = max(MIN_LABEL_WIDTH, *(_text_px(label, 13) for label in labels))
    value_w = max(_text_px(compact(v), 12) for v in [*numbers, xmax])
    left = PAD + label_w + 16
    right = PAD + value_w + 10
    ax = canvas.axes(left, top, right, PAD + AXIS_HEIGHT)
    ax.set_xlim(0, xmax)
    ax.set_ylim(rows - 0.5, -0.5)  # first entry on top
    ax.set_xticks(ticks)
    ax.set_yticks(range(rows), labels)
    style_axes(ax, grid='x')
    ax.xaxis.set_major_formatter(lambda v, _: compact(v))
    for label in ax.get_yticklabels():
        label.set_color(FOREGROUND)
        label.set_fontsize(pt(13))
    ax.axvline(0, color=LINE_STRONG, linewidth=pt(1), zorder=2)

    half = BAR_THICKNESS / ROW_HEIGHT / 2
    min_width = 6 * xmax / (CHART_WIDTH - left - right)  # trace amounts still read as a mark
    for i, value in enumerate(numbers):
        end = max(float(value), min_width) if value > 0 else 0.0
        rounded_bar(ax, 0, i - half, end, i + half, end='right', color=BRAND)
        ax.annotate(
            compact(value), (end, i), xytext=(pt(8), 0), textcoords='offset points',
            fontproperties=FONT, fontsize=pt(12), color=MUTED, va='center', annotation_clip=False,
        )

    return canvas.image()


def render_bar_chart_images(data: BarChartData) -> list[Image.Image]:
    """Generate one or more bar chart images from a dictionary of data.

    Data that spans more than :data:`MAX_BARS_PER_CHART` bars is paginated into
    multiple images of identical width (and one shared scale) so they can be stacked.
    """
    values = data.data
    ticks = nice_ticks(max(values.values(), default=0), bins=5)
    items = list(values.items())
    pages = [dict(items[i:i + MAX_BARS_PER_CHART]) for i in range(0, len(items), MAX_BARS_PER_CHART)] or [{}]
    return [
        _draw_bar_chart_page(page, data.title if i == 0 else '', data.subtitle if i == 0 else '', ticks)
        for i, page in enumerate(pages)
    ]


def merge_images_vertical(images: list[Image.Image]) -> Image.Image:
    """Stack the given images along the y-axis into a single image."""
    final_image = Image.new('RGB', (images[0].width, sum(image.height for image in images)))
    y_positions = 0
    for image in images:
        final_image.paste(image, (0, y_positions))
        y_positions += image.height

    return final_image


# -- Line / column series --------------------------------------------------------


def draw_series_chart(data: SeriesData) -> BytesIO:
    """A single series as a line over a 10% wash (``kind='line'``) or as columns."""
    canvas = Canvas(900, 400)
    top = canvas.header(data.title, data.subtitle)
    values = data.values
    if not values or not any(values):
        canvas.empty(top)
        return canvas.png()

    n = len(values)
    ticks = nice_ticks(max(values))
    left = PAD + max(_text_px(compact(t), 11) for t in ticks) + 12
    right = PAD + 6
    ax = canvas.axes(left, top + 14, right, PAD + 22)  # +14: room for the peak label
    ax.set_ylim(0, ticks[-1])
    ax.set_yticks(ticks)
    xs = list(range(n))
    step = max(1, math.ceil(n / 8))
    ax.set_xticks(xs[::step], [data.labels[i] if i < len(data.labels) else '' for i in xs[::step]])
    style_axes(ax, grid='y')
    ax.yaxis.set_major_formatter(lambda v, _: compact(v))
    ax.axhline(0, color=LINE_STRONG, linewidth=pt(1), zorder=2)

    peak = max(range(n), key=values.__getitem__)
    label_style = {'fontproperties': FONT, 'fontsize': pt(11.5), 'color': FOREGROUND, 'textcoords': 'offset points',
                   'annotation_clip': False, 'zorder': 6}

    if data.kind == 'bar':
        ax.set_xlim(-0.5, n - 0.5)
        slot = (canvas.width - left - right) / n
        half = min(24.0, slot * 0.6) / slot / 2
        for x, value in zip(xs, values):
            rounded_bar(ax, x - half, 0, x + half, value, end='top', color=BRAND)
        ax.annotate(compact(values[peak]), (peak, values[peak]), xytext=(0, pt(6)), ha='center', **label_style)
    else:
        ax.set_xlim(0, max(n - 1, 1))
        ax.fill_between(xs, values, color=BRAND, alpha=0.10, linewidth=0, zorder=3)
        ax.plot(xs, values, color=BRAND, linewidth=pt(2), solid_joinstyle='round', solid_capstyle='round', zorder=4)
        marked = sorted({peak, n - 1})
        ax.scatter(
            marked, [values[i] for i in marked], s=pt(10) ** 2, color=BRAND,
            edgecolor=PANEL_BG, linewidth=pt(2), zorder=5, clip_on=False,
        )
        for i in marked:
            ha = 'right' if i == n - 1 and n > 1 else 'center'
            ax.annotate(compact(values[i]), (i, values[i]), xytext=(0, pt(10)), ha=ha, **label_style)

    return canvas.png()


# -- Weekday x hour heatmap -------------------------------------------------------

_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _heat_color(value: float, vmax: float) -> str:
    return TRACK_BG if value <= 0 else to_hex(HEAT_CMAP(value / vmax))


def draw_heatmap_chart(data: HeatmapData) -> BytesIO:
    """A weekday x hour (UTC) grid of rounded cells with a Less -> More key."""
    grid = data.grid or [[0] * 24 for _ in range(7)]
    vmax = max((max(row) for row in grid), default=0) or 1

    top = _header_height(data.title, data.subtitle) + 4
    left = PAD + 38
    cell = (900 - left - PAD) / 24
    gap = 3
    height = int(top + 7 * cell + 26 + PAD)
    canvas = Canvas(900, height)
    canvas.header(data.title, data.subtitle)

    for day, row in enumerate(grid[:7]):
        y = top + day * cell
        canvas.text(PAD, y + cell / 2, _DAYS[day], size=11, color=FAINT, va='center')
        for hour, value in enumerate(row[:24]):
            canvas.box(left + hour * cell + gap / 2, y + gap / 2, cell - gap, cell - gap, _heat_color(value, vmax), radius=4)

    for hour in range(0, 24, 3):
        canvas.text(left + hour * cell + cell / 2, top + 7 * cell + 16, f'{hour:02d}', size=11, color=FAINT, ha='center')

    # Key, top right, aligned with the title.
    swatch, x = 12, canvas.width - PAD
    canvas.text(x, PAD + 15, 'More', size=11, color=FAINT, ha='right')
    x -= _text_px('More', 11) + 8 + 5 * (swatch + 3)
    for i, level in enumerate((0, 0.25, 0.5, 0.75, 1.0)):
        canvas.box(x + i * (swatch + 3), PAD + 4, swatch, swatch, _heat_color(level * vmax, vmax), radius=3)
    canvas.text(x - 8, PAD + 15, 'Less', size=11, color=FAINT, ha='right')

    return canvas.png()


# -- Presence donut ----------------------------------------------------------------


def draw_presence_chart(data: PresenceData) -> BytesIO:
    """A donut of time per status with the total in the hole and a ruled legend."""
    canvas = Canvas(900, 600)
    top = canvas.header(data.title, data.subtitle)
    total = sum(data.values)

    ax = canvas.axes(PAD + 10, top + 10, 900 / 2 + 10, PAD + 10)
    ax.set_facecolor('none')
    ax.axis('off')
    ax.pie(
        data.values if total else [1],
        colors=data.colors if total else [TRACK_BG],
        startangle=90,
        counterclock=False,
        wedgeprops={'width': 0.22, 'edgecolor': PANEL_BG, 'linewidth': pt(3)},
    )
    ax.set(aspect='equal')
    ax.text(0, 0.07, f'{total / 3600:.1f}', fontproperties=FONT, fontsize=pt(44), color=FOREGROUND, ha='center', va='center')
    ax.text(0, -0.2, 'hours tracked', fontproperties=FONT, fontsize=pt(13), color=MUTED, ha='center', va='center')

    rows = list(zip(data.labels, data.values, data.colors))
    row_h = 52
    x0, x1 = 900 / 2 + 50, canvas.width - PAD - 10
    y = top + (canvas.height - top - PAD - len(rows) * row_h) / 2
    for i, (label, value, color) in enumerate(rows):
        mid = y + row_h / 2
        canvas.dot(x0 + 5, mid, 5, color)
        canvas.text(x0 + 22, mid, label, size=15, va='center')
        share = f'{value / total * 100:.0f}%' if total else '0%'
        canvas.text(x1, mid, share, size=15, ha='right', va='center')
        canvas.text(x1 - 64, mid, f'{value / 3600:.1f} h', size=13, color=MUTED, ha='right', va='center')
        if i < len(rows) - 1:
            canvas.box(x0, y + row_h, x1 - x0, 1, LINE)
        y += row_h

    return canvas.png()


# -- Avatar collage -----------------------------------------------------------------


def draw_avatar_collage(avatars: list[bytes]) -> BytesIO:
    """Draws a square collage of the given avatars and returns a PNG buffer
    resized to stay under Discord's attachment limit."""
    xbound = math.ceil(math.sqrt(len(avatars)))
    ybound = math.ceil(len(avatars) / xbound)
    size = int(2520 / xbound)

    with Image.new('RGBA', size=(xbound * size, ybound * size), color=(0, 0, 0, 0)) as base:
        x, y = 0, 0
        for avy in avatars:
            if avy:
                with Image.open(BytesIO(avy)) as opened:
                    im = opened.resize((size, size), resample=Image.Resampling.BICUBIC, reducing_gap=2.0)
                base.paste(im, box=(x * size, y * size))
                im.close()
            if x < xbound - 1:
                x += 1
            else:
                x = 0
                y += 1

        buffer = BytesIO()
        base.save(buffer, 'png')
        buffer.seek(0)

    return resize_to_limit(buffer, 8000000)
