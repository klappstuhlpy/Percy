"""Pure drawing logic.

Every function here takes already-prepared data (see :mod:`app.rendering.models`)
plus a shared :class:`~app.rendering.primitives.FontManager`, and returns a
``BytesIO`` buffer. They never import ``discord``, touch the database, or perform
domain look-ups — which keeps them deterministic and unit-testable. The
:class:`~app.rendering.service.RenderingService` is the only intended caller.
"""

from app.rendering.templates.captcha import generate_captcha
from app.rendering.templates.charts import (
    draw_avatar_collage,
    draw_presence_chart,
    merge_images_vertical,
    render_bar_chart_images,
)
from app.rendering.templates.color import draw_color_swatch
from app.rendering.templates.leveling import draw_level_card
from app.rendering.templates.music import draw_equalizer
from app.rendering.templates.quote import draw_quote

__all__ = (
    'draw_avatar_collage',
    'draw_color_swatch',
    'draw_equalizer',
    'draw_level_card',
    'draw_presence_chart',
    'draw_quote',
    'generate_captcha',
    'merge_images_vertical',
    'render_bar_chart_images',
)
