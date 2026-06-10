"""Shared visual theme for the matplotlib chart templates.

The design tokens mirror the klappstuhl.me admin design system (the ``:root``
variables in ``klappstuhl_me/static/css/base.css``) and must stay in sync with it.
Fonts are loaded from the bundled assets via ``FontProperties(fname=...)`` so
rendering never depends on system font lookup.
"""

from __future__ import annotations

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle
from PIL import Image

from app.rendering.primitives import ASSETS

__all__ = (
    'BRAND',
    'BRAND_BRIGHT',
    'FOREGROUND',
    'GRID',
    'MENLO',
    'MUTED',
    'PANEL_BG',
    'PANEL_BORDER',
    'POPPINS',
    'RUBIK',
    'TRACK_BG',
    'add_panel_title',
    'figure_to_image',
    'panel_figure',
)

# Design tokens mirroring klappstuhl_me/static/css/base.css
PANEL_BG = '#18181b'  # --box
PANEL_BORDER = '#27272a'  # --box-border
TRACK_BG = '#1e1e21'  # --box-shade
FOREGROUND = '#fafafa'  # --foreground
MUTED = '#71717a'  # --text-muted
BRAND = '#d97757'  # --branding (coral)
BRAND_BRIGHT = '#e8916f'  # --branding-bright
GRID = (0.5, 0.5, 0.5, 0.15)  # the admin charts' grid stroke rgba(127,127,127,0.15)

# Menlo sits in the site's own font-family fallback chain right after JetBrains Mono.
MENLO = FontProperties(fname=str(ASSETS / 'fonts/menlo.ttf'))
RUBIK = FontProperties(fname=str(ASSETS / 'fonts/rubik.ttf'))
POPPINS = FontProperties(fname=str(ASSETS / 'fonts/poppins.ttf'))


def figure_to_image(figure: Figure) -> Image.Image:
    """Rasterise a figure through the Agg canvas into a Pillow image."""
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    return Image.frombuffer('RGBA', canvas.get_width_height(), canvas.buffer_rgba())


def panel_figure(width_px: int, height_px: int, dpi: int) -> Figure:
    """A figure styled as an admin dashboard panel: dark surface, 1px border."""
    figure = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor=PANEL_BG)
    figure.patch.set_edgecolor(PANEL_BORDER)
    figure.patch.set_linewidth(2)
    return figure


def add_panel_title(figure: Figure, title: str, *, width_px: int, height_px: int) -> None:
    """Coral accent tick + muted uppercase title, like the site's ``.tui-title`` glyph."""
    pad = 32
    title_y = 1 - 52 / height_px
    figure.add_artist(
        Rectangle(
            (pad / width_px, title_y - 9 / height_px), 5 / width_px, 20 / height_px,
            transform=figure.transFigure, facecolor=BRAND,
        )
    )
    figure.text(
        (pad + 16) / width_px, title_y, title.upper(),
        fontproperties=POPPINS, fontsize=14.5, color=MUTED, va='center',
    )
