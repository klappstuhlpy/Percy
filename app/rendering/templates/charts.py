"""Pure drawing logic for the data charts: horizontal bar charts and the presence
donut chart (matplotlib, Agg) plus the avatar collage (Pillow composition).

The charts follow the klappstuhl.me admin design system (``static/css/base.css``
tokens in the Rust project): dark panel surfaces with a subtle border, muted
uppercase titles, the coral brand accent and a monospaced font. Bars are drawn on
full-width "track" rails like the admin container table's inline meters.

Chart figures use the object-oriented matplotlib API (:class:`~matplotlib.figure.Figure`
with an explicit Agg canvas) instead of ``pyplot``, which holds global state and is not
thread-safe under ``asyncio.to_thread``. Fonts are loaded from the bundled assets via
``FontProperties(fname=...)`` so rendering never depends on system font lookup.
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.textpath import TextPath
from PIL import Image

from app.rendering.primitives import resize_to_limit
from app.rendering.templates.theme import (
    BRAND,
    BRAND_BRIGHT,
    FOREGROUND,
    MENLO,
    MUTED,
    PANEL_BG,
    RUBIK,
    TRACK_BG,
    add_panel_title,
    figure_to_image,
    panel_figure,
)

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.font_manager import FontProperties

    from app.rendering.models import BarChartData, PresenceData

__all__ = (
    'draw_avatar_collage',
    'draw_presence_chart',
    'merge_images_vertical',
    'render_bar_chart_images',
)

CHART_DPI = 100
CHART_WIDTH_PX = 1920
ROW_HEIGHT_PX = 52
BAR_HEIGHT_PX = 26  # pill bars: corner radius = half of this
MAX_BARS_PER_CHART = 18

# Subtle left-to-right gradient for the value bars (coral -> bright coral).
BRAND_GRADIENT = LinearSegmentedColormap.from_list('brand', [BRAND, BRAND_BRIGHT])
_GRADIENT_ROW = np.linspace(0.0, 1.0, 256).reshape(1, -1)


def _format_value(value: int | float) -> str:
    """Format a bar value with thousands grouping, trimming trailing zeros."""
    if float(value).is_integer():
        return f'{int(value):,}'
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def _text_width_px(text: str, prop: FontProperties, size_pt: float, dpi: int) -> float:
    """Measure rendered text width in pixels without a canvas."""
    if not text:
        return 0.0
    return TextPath((0, 0), text, prop=prop, size=size_pt).get_extents().width * dpi / 72


def _pill(x: float, y: float, width: float, height: float, radius_x: float, aspect: float) -> FancyBboxPatch:
    """A rounded-end bar in data coordinates with pixel-circular corners.

    ``radius_x`` is the corner radius expressed in x-data units; ``aspect`` is the
    x/y pixels-per-data-unit ratio that makes the corners round on screen.
    """
    return FancyBboxPatch(
        (x, y), width, height,
        boxstyle=f'round,pad=0,rounding_size={min(radius_x, width / 2)}',
        mutation_aspect=aspect,
        linewidth=0,
    )


def _draw_bar_chart_page(values: dict[str, int | float], title: str, max_value: float) -> Image.Image:
    """Draw a single page of the horizontal bar chart."""
    pad = 32
    title_px = 104 if title else 40
    rows = len(values)
    height_px = max(rows, 1) * ROW_HEIGHT_PX + title_px + pad

    figure = panel_figure(CHART_WIDTH_PX, height_px, CHART_DPI)

    labels = list(values)
    bar_values = list(values.values())
    xmax = max(max_value, 1)

    # Manual axes geometry: rounded corners need exact pixel scales, so the label
    # and value columns are measured up front instead of letting a layout engine
    # reserve them. The value column sits right of the track so bars never run
    # underneath the numbers.
    label_w = max((_text_width_px(label, RUBIK, 14, CHART_DPI) for label in labels), default=0.0)
    value_w = max((_text_width_px(_format_value(v), RUBIK, 13.5, CHART_DPI) for v in bar_values), default=0.0)
    value_col = value_w + 26
    ax_left = pad + label_w + 28
    ax_width = CHART_WIDTH_PX - ax_left - pad - value_col
    ax_height = max(rows, 1) * ROW_HEIGHT_PX

    ax: Axes = figure.add_axes(
        (ax_left / CHART_WIDTH_PX, pad / height_px, ax_width / CHART_WIDTH_PX, ax_height / height_px)
    )
    ax.set_facecolor(PANEL_BG)
    ax.set_xlim(0, xmax)
    ax.set_ylim(max(rows, 1) - 0.5, -0.5)  # inverted: first dict entry on top
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks(list(range(rows)))
    ax.set_yticklabels(labels, fontproperties=RUBIK, fontsize=14, color=FOREGROUND)
    ax.tick_params(axis='y', length=0, pad=16)

    # Pixel-exact pill geometry.
    px_per_x = ax_width / xmax
    px_per_y = float(ROW_HEIGHT_PX)
    aspect = px_per_x / px_per_y
    bar_h = BAR_HEIGHT_PX / px_per_y  # in y-data units
    radius_x = (BAR_HEIGHT_PX / 2) / px_per_x  # in x-data units

    for i, value in enumerate(bar_values):
        track = _pill(0, i - bar_h / 2, xmax, bar_h, radius_x, aspect)
        track.set_facecolor(TRACK_BG)
        track.set_zorder(2)
        ax.add_patch(track)

        if value > 0:
            # Trace amounts still render as a full pill "dot" instead of a hairline.
            bar_w = max(float(value), BAR_HEIGHT_PX / px_per_x)
            bar = _pill(0, i - bar_h / 2, bar_w, bar_h, radius_x, aspect)
            bar.set_facecolor('none')
            bar.set_zorder(3)
            ax.add_patch(bar)
            gradient = ax.imshow(
                _GRADIENT_ROW,
                extent=(0, bar_w, i - bar_h / 2, i + bar_h / 2),
                aspect='auto',
                cmap=BRAND_GRADIENT,
                interpolation='bicubic',
                zorder=3,
            )
            gradient.set_clip_path(bar)

        # Value in its own right-aligned column past the end of the track.
        ax.text(
            xmax + value_col / px_per_x, i, _format_value(value),
            fontproperties=RUBIK, fontsize=13.5, color=FOREGROUND, ha='right', va='center', zorder=5,
        )

    if title:
        add_panel_title(figure, title, width_px=CHART_WIDTH_PX, height_px=height_px)

    image = figure_to_image(figure).convert('RGB')
    figure.clear()
    return image


def render_bar_chart_images(data: BarChartData) -> list[Image.Image]:
    """Generate one or more bar chart images from a dictionary of data.

    Data that spans more than :data:`MAX_BARS_PER_CHART` bars is paginated into
    multiple images of identical width so they can be stacked afterwards.
    """
    values = data.data
    max_value = max(values.values(), default=0)

    items = list(values.items())
    pages = [dict(items[i:i + MAX_BARS_PER_CHART]) for i in range(0, len(items), MAX_BARS_PER_CHART)] or [{}]

    return [_draw_bar_chart_page(page, data.title, max_value) for page in pages]


def merge_images_vertical(images: list[Image.Image]) -> Image.Image:
    """Stack the given images along the y-axis into a single image."""
    final_image = Image.new('RGB', (images[0].width, sum(image.height for image in images)))
    y_positions = 0
    for image in images:
        final_image.paste(image, (0, y_positions))
        y_positions += image.height

    return final_image


def draw_presence_chart(data: PresenceData) -> BytesIO:
    """Draws a presence donut chart for the given prepared data and returns a PNG buffer."""
    total = sum(data.values) or 1
    has_data = sum(data.values) > 0

    figure = panel_figure(1800, 1200, dpi=200)

    figure.text(0.05, 0.915, data.title.upper(), fontproperties=MENLO, fontsize=13, color=MUTED)

    ax: Axes = figure.add_axes((0.045, 0.05, 0.46, 0.74))
    ax.set_facecolor('none')
    ax.pie(
        data.values if has_data else [1],
        colors=data.colors if has_data else [TRACK_BG],
        startangle=90,
        counterclock=False,
        wedgeprops={'width': 0.26, 'edgecolor': PANEL_BG, 'linewidth': 3},
        radius=1.0,
    )
    ax.set(aspect='equal')

    # Big total in the donut hole, metric-tile style.
    ax.text(0, 0.06, f'{total / 3600:.1f}', fontproperties=MENLO, fontsize=30, color=FOREGROUND, ha='center', va='center')
    ax.text(0, -0.22, 'HOURS TOTAL', fontproperties=MENLO, fontsize=10, color=MUTED, ha='center', va='center')

    # Legend rows on the right: marker + label, with hours · percent underneath.
    legend_x = 0.58
    legend_y = 0.72
    for value, color, label in zip(data.values, data.colors, data.labels):
        figure.add_artist(
            Rectangle((legend_x, legend_y - 0.014), 0.016, 0.036, transform=figure.transFigure, facecolor=color)
        )
        figure.text(
            legend_x + 0.032, legend_y, label, fontproperties=MENLO, fontsize=13, color=FOREGROUND, va='center'
        )
        figure.text(
            legend_x + 0.032, legend_y - 0.055, f'{value / 3600:.1f} h · {value / total * 100:.1f}%',
            fontproperties=MENLO, fontsize=11, color=MUTED, va='center',
        )
        legend_y -= 0.16

    image = figure_to_image(figure)
    figure.clear()

    buffer = BytesIO()
    image.save(buffer, format='png')
    buffer.seek(0)
    return buffer


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
                im = Image.open(BytesIO(avy)).resize((size, size), resample=Image.Resampling.BICUBIC)
                base.paste(im, box=(x * size, y * size))
            if x < xbound - 1:
                x += 1
            else:
                x = 0
                y += 1

        buffer = BytesIO()
        base.save(buffer, 'png')
        buffer.seek(0)

    return resize_to_limit(buffer, 8000000)
