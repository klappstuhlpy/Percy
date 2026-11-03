"""Pure drawing logic for the quote image: the avatar fading into a panel, the quote beside it."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.rendering.primitives import Font, FontManager
from app.rendering.templates.theme import FOREGROUND, MUTED, PANEL_BG

if TYPE_CHECKING:
    from PIL.ImageFont import FreeTypeFont

    from app.rendering.models import QuoteData

__all__ = ('draw_quote',)

WIDTH, HEIGHT = 1250, 500
PAD = 40
MAX_FONT_SIZE = 46
MIN_FONT_SIZE = 24
NAME_FONT_SIZE = 30
LINE_SPACING = 1.25


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: FreeTypeFont, max_width: float) -> list[str]:
    """Greedy word wrap by rendered width; a word wider than a line is broken by characters."""
    lines: list[str] = []
    for paragraph in text.splitlines() or ['']:
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}' if line else word
            if draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ''
            for char in word:
                if line and draw.textlength(line + char, font=font) > max_width:
                    lines.append(line)
                    line = ''
                line += char
        lines.append(line)
    return lines


def draw_quote(data: QuoteData, fonts: FontManager) -> BytesIO:
    """Draws a quote image for the given prepared data and returns a PNG buffer."""
    image = Image.new('RGB', (WIDTH, HEIGHT), PANEL_BG)

    with Image.open(BytesIO(data.avatar)) as opened:
        avatar = opened.convert('RGBA')
    avatar = avatar.resize((round(HEIGHT * avatar.width / avatar.height), HEIGHT), Image.Resampling.LANCZOS)
    # Horizontal fade: opaque on the left edge, transparent where the text column starts.
    avatar.putalpha(Image.linear_gradient('L').rotate(-90).resize(avatar.size))
    image.paste(avatar, (0, 0), avatar)

    draw = ImageDraw.Draw(image)
    left = min(avatar.width, WIDTH // 2) + PAD
    right = WIDTH - PAD
    center_x = (left + right) / 2
    name_font = fonts.get(Font.RUBIK, NAME_FONT_SIZE)
    name_block = NAME_FONT_SIZE * 2

    # Shrink the quote until it fits the column above the author line.
    size = MAX_FONT_SIZE
    while True:
        font = fonts.get(data.font, size)
        lines = _wrap(draw, data.text, font, right - left)
        line_height = round(size * LINE_SPACING)
        if len(lines) * line_height <= HEIGHT - 2 * PAD - name_block or size <= MIN_FONT_SIZE:
            break
        size -= 2

    max_lines = (HEIGHT - 2 * PAD - name_block) // line_height
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + '…'

    top = (HEIGHT - len(lines) * line_height - name_block) / 2
    for i, line in enumerate(lines):
        draw.text((center_x, top + i * line_height + line_height / 2), line, font=font, fill=FOREGROUND, anchor='mm')

    name_y = top + len(lines) * line_height + name_block / 2
    draw.text((center_x, name_y), f'— {data.author_name}', font=name_font, fill=MUTED, anchor='mm')

    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer
