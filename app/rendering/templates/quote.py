"""Pure drawing logic for the quote image."""

from __future__ import annotations

import textwrap
from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.rendering.primitives import ASSETS, FONT_MAPPING, FontManager, get_text_dimensions

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL.ImageFont import FreeTypeFont

    from app.rendering.models import QuoteData

__all__ = ('draw_quote',)

QUOTE_FONT_SIZE = 46
NAME_FONT_SIZE = 34


def _build_base_image(avatar: bytes) -> tuple[Image.Image, int]:
    """Creates the base image with the gradient-masked avatar; returns the image
    and the right-most avatar column used to lay out the text."""
    image = Image.new('RGB', (1250, 500), 'black')
    avatar_image = Image.open(BytesIO(avatar)).convert('RGBA')

    aspect_ratio = avatar_image.width / avatar_image.height
    new_height = image.height
    new_width = int(new_height * aspect_ratio)

    avatar_image = avatar_image.resize((new_width, new_height))

    mask = Image.new('L', avatar_image.size)
    mask_data = []
    last_x = 0
    for _y in range(avatar_image.height):
        for x in range(avatar_image.width):
            mask_data.append(int(255 * (1 - (x / avatar_image.width))))
            last_x = x

    mask.putdata(mask_data)
    avatar_image.putalpha(mask)
    image.paste(avatar_image, (0, 0), avatar_image)

    return image, last_x


def _draw_text(image: Image.Image, data: QuoteData, get_font: Callable[[int], FreeTypeFont], avatar_last_x: int) -> Image.Image:
    """Draws the quote text and author name on the image."""
    draw = ImageDraw.Draw(image)

    quote_font = get_font(QUOTE_FONT_SIZE)
    name_font = get_font(NAME_FONT_SIZE)

    left_boundary = avatar_last_x + 40
    right_boundary = image.width - 40
    max_width = (right_boundary - left_boundary) // QUOTE_FONT_SIZE * 2

    lines = textwrap.wrap(data.text, width=max_width)
    total_text_height = sum(get_text_dimensions(line, quote_font)[1] for line in lines)

    start_y = (image.height - total_text_height) // 2
    center_x = left_boundary + (right_boundary - left_boundary) // 2

    for i, line in enumerate(lines):
        _line_width, line_height = get_text_dimensions(line, quote_font)
        y = start_y + i * line_height
        if i == len(lines) - 1:
            y += total_text_height - len(lines) * line_height
        draw.text((center_x, y), line, font=quote_font, fill='white', anchor='mm')

    name_text = f'- {data.author_name}'
    name_y = start_y + total_text_height + 10
    draw.text((center_x, name_y), name_text, font=name_font, fill='white', anchor='mm')

    return image


def draw_quote(data: QuoteData, fonts: FontManager) -> BytesIO:
    """Draws a quote image for the given prepared data and returns a PNG buffer."""
    get_font: Callable[[int], FreeTypeFont] = partial(fonts.get, str(ASSETS / FONT_MAPPING[data.font.value]))

    image, avatar_last_x = _build_base_image(data.avatar)
    image = _draw_text(image, data, get_font, avatar_last_x)

    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer
