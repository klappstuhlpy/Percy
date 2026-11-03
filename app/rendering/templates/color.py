"""Pure drawing logic for a solid colour swatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.rendering.primitives import Font, FontManager
from app.rendering.templates.theme import image_to_png

if TYPE_CHECKING:
    from io import BytesIO

    from app.rendering.models import ColorSwatchData

__all__ = ('draw_color_swatch',)

SIZE = 256


def _is_color_dark(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return (r * 299 + g * 587 + b * 114) / 1000 < 128


def draw_color_swatch(data: ColorSwatchData, fonts: FontManager) -> BytesIO:
    """Draws a 256x256 colour swatch, optionally captioned (shrunk to fit), and returns a PNG buffer."""
    image = Image.new('RGB', (SIZE, SIZE), data.rgb)

    if data.text:
        draw = ImageDraw.Draw(image)
        size = 28
        while size > 12 and draw.textlength(data.text, font=fonts.get(Font.RUBIK, size)) > SIZE - 24:
            size -= 2
        fill = 'white' if _is_color_dark(data.rgb) else 'black'
        draw.text((SIZE / 2, SIZE / 2), data.text, font=fonts.get(Font.RUBIK, size), fill=fill, anchor='mm')

    return image_to_png(image)
