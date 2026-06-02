"""Pure drawing logic for a solid colour swatch."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from app.rendering.primitives import ASSETS

if TYPE_CHECKING:
    from app.rendering.models import ColorSwatchData

__all__ = ('draw_color_swatch',)


def _is_color_dark(rgb: tuple[int, int, int]) -> bool:
    """Check if a colour is dark."""
    r, g, b = rgb
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return brightness < 128


def draw_color_swatch(data: ColorSwatchData) -> BytesIO:
    """Draws a 256x256 colour swatch, optionally captioned, and returns a PNG buffer."""
    image = Image.new('RGB', (256, 256), data.rgb)
    draw = ImageDraw.Draw(image)

    if data.text:
        font = ImageFont.truetype(str(ASSETS / 'fonts/rubrik.ttf'), size=28)
        text_color = 'white' if _is_color_dark(data.rgb) else 'black'

        _, _, w, h = draw.textbbox((0, 0), data.text, font=font)
        draw.text(((256 - w) / 2, (256 - h) / 2), data.text, font=font, fill=text_color)

    buffer = BytesIO()
    image.save(buffer, 'png')
    buffer.seek(0)
    return buffer
