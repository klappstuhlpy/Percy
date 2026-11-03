"""Shared visual theme and layout canvas for the rendered artifacts.

The neutral tokens mirror the Percy dashboard design system
(``dashboard/static/css/common/tokens.css``, dark theme) and must stay in sync with it.
Charts keep Percy's coral ``--brand`` as their data colour: a rendered image is a brand
artifact, not an interactive control, so the violet ``--accent`` does not apply.

Every chart is laid out in *logical* pixels on a :class:`Canvas` and rasterised at
:data:`SCALE` x, so text stays crisp on high-density screens. The look is deliberately
quiet (see the dataviz mark specs): one sans face, a sentence-case title over a muted
subtitle, hairline solid gridlines one step off the surface, thin flat marks with a 4px
rounded data end, and values labelled selectively rather than on every point.
Fonts are loaded from the bundled assets via ``FontProperties(fname=...)`` so
rendering never depends on system font lookup.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Literal

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, PathPatch
from matplotlib.path import Path
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import IdentityTransform
from PIL import Image

from app.rendering.primitives import Font

if TYPE_CHECKING:
    from matplotlib.axes import Axes

__all__ = (
    'BRAND',
    'BRAND_BRIGHT',
    'FAINT',
    'FONT',
    'FOREGROUND',
    'LINE',
    'LINE_STRONG',
    'MUTED',
    'PANEL_BG',
    'SCALE',
    'TRACK_BG',
    'Canvas',
    'compact',
    'figure_to_png',
    'image_to_png',
    'nice_ticks',
    'pt',
    'rounded_bar',
    'style_axes',
)

# Design tokens mirroring dashboard/static/css/common/tokens.css (dark)
PANEL_BG = '#1a1a1d'  # --surface-2
TRACK_BG = '#232327'  # --surface-3
LINE = '#26262a'  # --line: hairline grid, panel border
LINE_STRONG = '#3a3a40'  # the zero baseline, one step louder than the grid
FOREGROUND = '#f0f1f3'  # --text
MUTED = '#a1a1a8'  # --text-muted
FAINT = '#7a7a82'  # --text-faint: tick labels
BRAND = '#d97757'  # --brand (coral)
BRAND_BRIGHT = '#e8916f'  # brighter coral for the top of the sequential ramp

FONT = FontProperties(fname=Font.INTER.path)

SCALE = 2  # logical px -> output px
PAD = 24  # outer padding in logical px


def pt(px: float) -> float:
    """Logical pixels to points (the figure is 100 logical px per inch)."""
    return px * 0.72


def compact(value: float) -> str:
    """1,234 under ten thousand, then 12.3K / 4.5M; trailing ``.0`` trimmed."""
    sign = '-' if value < 0 else ''
    value = abs(value)
    for limit, suffix in ((1e9, 'B'), (1e6, 'M'), (1e4, 'K')):
        if value >= limit:
            scaled = value / (limit if suffix != 'K' else 1e3)
            return f'{sign}{scaled:.1f}'.rstrip('0').rstrip('.') + suffix
    if float(value).is_integer():
        return f'{sign}{int(value):,}'
    return f'{sign}{value:,.2f}'.rstrip('0').rstrip('.')


def nice_ticks(vmax: float, *, bins: int = 4) -> list[float]:
    """Clean tick positions from 0 that reach at least ``vmax``."""
    ticks = [float(t) for t in MaxNLocator(nbins=bins, steps=[1, 2, 2.5, 5, 10]).tick_values(0, max(vmax, 1))]
    return [t for t in ticks if t >= 0]


def image_to_png(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.save(buffer, format='png')
    buffer.seek(0)
    return buffer


def figure_to_png(figure: Figure) -> BytesIO:
    """Rasterise an opaque figure straight to an RGB PNG buffer."""
    return image_to_png(figure_to_image(figure).convert('RGB'))


def figure_to_image(figure: Figure) -> Image.Image:
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    return Image.frombuffer('RGBA', canvas.get_width_height(), canvas.buffer_rgba())


def style_axes(ax: Axes, *, grid: Literal['x', 'y', 'none'] = 'y') -> None:
    """Recessive chrome: no spines, no tick marks, faint labels, hairline solid grid."""
    ax.set_facecolor('none')
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, colors=FAINT, labelsize=pt(11), pad=pt(8))
    for label in (*ax.get_xticklabels(), *ax.get_yticklabels()):
        label.set_fontproperties(FONT)
        label.set_fontsize(pt(11))
    if grid != 'none':
        ax.grid(axis=grid, color=LINE, linewidth=pt(1), linestyle='-')
    ax.set_axisbelow(True)


def rounded_bar(
    ax: Axes, x0: float, y0: float, x1: float, y1: float, *, end: Literal['right', 'top'], color: str, radius: float = 4
) -> None:
    """A bar in data coordinates with a rounded data end and a square baseline.

    Built in display space so the corners are true pixel radii whatever the axis scales.
    Call after the axis limits are final.
    """
    (ax0, ay0), (ax1, ay1) = ax.transData.transform([(x0, y0), (x1, y1)])
    left, right = sorted((ax0, ax1))
    bottom, top = sorted((ay0, ay1))
    width, height = right - left, top - bottom
    if width <= 0 or height <= 0:
        return

    m, l, c, close = Path.MOVETO, Path.LINETO, Path.CURVE3, Path.CLOSEPOLY
    if end == 'right':
        r = min(radius * SCALE, width, height / 2)
        verts = [(left, bottom), (right - r, bottom), (right, bottom), (right, bottom + r),
                 (right, top - r), (right, top), (right - r, top), (left, top), (left, bottom)]
        codes = [m, l, c, c, l, c, c, l, close]
    else:
        r = min(radius * SCALE, height, width / 2)
        verts = [(left, bottom), (right, bottom), (right, top - r), (right, top), (right - r, top),
                 (left + r, top), (left, top), (left, top - r), (left, bottom)]
        codes = [m, l, l, c, c, l, c, c, close]

    ax.add_artist(PathPatch(Path(verts, codes), facecolor=color, linewidth=0, transform=IdentityTransform(), zorder=3))


class Canvas:
    """A dark panel figure addressed in logical pixels, origin top-left."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.figure = Figure(figsize=(width / 100, height / 100), dpi=100 * SCALE, facecolor=PANEL_BG)
        self.figure.patch.set_edgecolor(LINE)
        self.figure.patch.set_linewidth(pt(2))

    def _xy(self, x: float, y: float) -> tuple[float, float]:
        return x * SCALE, (self.height - y) * SCALE

    def text(
        self, x: float, y: float, text: str, *, size: float, color: str = FOREGROUND,
        ha: str = 'left', va: str = 'baseline',
    ) -> None:
        self.figure.text(
            *self._xy(x, y), text, fontproperties=FONT, fontsize=pt(size), color=color,
            ha=ha, va=va, transform=IdentityTransform(),
        )

    def box(self, x: float, y: float, w: float, h: float, color: str, *, radius: float = 0) -> None:
        bx, by = self._xy(x, y + h)
        style = f'round,pad=0,rounding_size={radius * SCALE}' if radius else 'square,pad=0'
        self.figure.add_artist(
            FancyBboxPatch((bx, by), w * SCALE, h * SCALE, boxstyle=style, facecolor=color, linewidth=0,
                           transform=IdentityTransform())
        )

    def dot(self, x: float, y: float, radius: float, color: str) -> None:
        self.box(x - radius, y - radius, radius * 2, radius * 2, color, radius=radius)

    def header(self, title: str, subtitle: str = '') -> float:
        """Sentence-case title over an optional muted subtitle; returns the y below it."""
        self.text(PAD, PAD + 15, title, size=17)
        if subtitle:
            self.text(PAD, PAD + 37, subtitle, size=12.5, color=MUTED)
            return PAD + 60
        return PAD + 36

    def axes(self, left: float, top: float, right: float, bottom: float) -> Axes:
        """An axes inset by the given logical-pixel margins."""
        w, h = self.width, self.height
        return self.figure.add_axes(((left / w), bottom / h, (w - left - right) / w, (h - top - bottom) / h))

    def empty(self, top: float, message: str = 'No data yet') -> None:
        self.text(self.width / 2, (top + self.height) / 2, message, size=13, color=FAINT, ha='center', va='center')

    def png(self) -> BytesIO:
        return figure_to_png(self.figure)

    def image(self) -> Image.Image:
        return figure_to_image(self.figure).convert('RGB')
