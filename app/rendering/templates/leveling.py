"""Pure drawing logic for the rank/level card. No domain models, no Discord, no I/O
beyond reading bundled assets and returning a buffer."""

from __future__ import annotations

from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.rendering.primitives import ASSETS, FONT_MAPPING, FontManager, get_dominant_color, get_text_dimensions
from app.utils import shorten_number

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL.ImageFont import FreeTypeFont

    from app.rendering.models import LevelCardData

__all__ = ('draw_level_card',)


def _create_rounded_rectangle_mask(size: tuple[int, int], radius: int, alpha: int = 255) -> Image.Image:
    """Creates a rounded rectangle mask."""
    factor = 5
    radius = radius * factor
    image = Image.new('RGBA', (size[0] * factor, size[1] * factor), (0, 0, 0, 0))

    corner = Image.new('RGBA', (radius, radius), (0, 0, 0, 0))
    draw = ImageDraw.Draw(corner)
    draw.pieslice(((0, 0), (radius * 2, radius * 2)), 180, 270, fill=(50, 50, 50, alpha + 55))

    mx, my = (size[0] * factor, size[1] * factor)

    image.paste(corner, (0, 0), corner)
    image.paste(corner.rotate(90), (0, my - radius), corner.rotate(90))
    image.paste(corner.rotate(180), (mx - radius, my - radius), corner.rotate(180))
    image.paste(corner.rotate(270), (mx - radius, 0), corner.rotate(270))

    draw = ImageDraw.Draw(image)
    draw.rectangle((radius, 0, mx - radius, my), fill=(50, 50, 50, alpha))
    draw.rectangle((0, radius, mx, my - radius), fill=(50, 50, 50, alpha))
    image = image.resize(size, Image.Resampling.LANCZOS)

    return image


def _create_outlined_rounded_rectangle(
        size: tuple[int, int],
        radius: int,
        thickness: int,
        fill: tuple,
        outline: tuple
) -> tuple[Image.Image, Image.Image]:
    """Creates an outlined rounded rectangle."""
    with Image.new('RGB', (size[0] + thickness, size[1] + thickness), outline) as outline_image:
        with Image.new('RGB', size, fill) as fill_image:
            outline_image.paste(
                fill_image, (thickness // 2, thickness // 2), _create_rounded_rectangle_mask(size, radius))

        return outline_image, _create_rounded_rectangle_mask(outline_image.size, radius + (thickness // 2))


def _get_color_alpha(
        foreground: tuple, alpha: float, background: tuple[int, int, int] = (34, 40, 49)
) -> tuple[int, ...] | tuple[int, int, int]:
    color = []
    for f, b in zip(foreground, background):
        color.append(int(f * alpha + b * (1 - alpha)))

    return tuple(color)


def _format_round_avatar(avatar: bytes) -> Image.Image:
    """Formats the avatar to a round image."""
    image = Image.open(BytesIO(avatar)).resize((196, 196), Image.Resampling.BOX)

    mask = Image.new('L', (196, 196), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 196, 196), fill=255)

    round_avatar = Image.new('RGBA', (196, 196))
    round_avatar.paste(image, (0, 0), mask=mask)

    return round_avatar


def draw_level_card(data: LevelCardData, fonts: FontManager) -> BytesIO:
    """Draws a rank card for the given prepared data and returns a PNG buffer."""
    get_font: Callable[[int], FreeTypeFont] = partial(fonts.get, str(ASSETS / FONT_MAPPING[data.font.value]))

    base = Image.open(ASSETS / 'rank_card.png').copy()
    avatar = _format_round_avatar(data.avatar)

    base.paste(avatar, (38, 38), avatar)

    user_canvas = ImageDraw.Draw(base)

    user_canvas.text(
        (252, 62),
        data.name,
        (235, 235, 235),
        font=get_font(48),
    )

    total_xp_text = f'{data.total_xp:,} XP'
    user_canvas.text(
        (252, 114),
        total_xp_text,
        _get_color_alpha((216, 216, 216), 0.8),
        font=get_font(28),
    )

    rank_text = f'Rank #{data.rank}'
    rank_width = user_canvas.textlength(rank_text, font=get_font(48))
    user_canvas.text(
        (
            base.width - rank_width - 38,
            62
        ),
        rank_text,
        (235, 235, 235),
        font=get_font(48),
    )

    members = f'of {shorten_number(data.member_count)}'
    members_width = user_canvas.textlength(members, font=get_font(28))
    user_canvas.text(
        (
            base.width - members_width - 38,
            114
        ),
        members,
        _get_color_alpha((216, 216, 216), 0.8),
        font=get_font(28),
    )

    color = get_dominant_color(avatar)
    empty_bar, empty_bar_mask = _create_outlined_rounded_rectangle(
        (862, 42),
        10,
        4,
        _get_color_alpha(color, 0.3),
        color
    )
    base.paste(empty_bar, (252, 168), empty_bar_mask)

    if multiplier := abs(data.xp / data.max_xp):
        progress_bar = Image.new('RGB', (round(862 * multiplier), 44), color=color)
        base.paste(
            progress_bar,
            (252, 168),
            _create_rounded_rectangle_mask(progress_bar.size, 10)
        )

    level_bg, level_bg_mask = _create_outlined_rounded_rectangle(
        (192, 60),
        20,
        4,
        (57, 62, 70),
        _get_color_alpha(color, 0.5)
    )
    level_canvas = ImageDraw.Draw(level_bg)

    level_text = 'Level'
    level_text_width, level_text_height = get_text_dimensions(level_text, font=get_font(32))
    level_number = str(data.level)
    level_number_width, level_number_height = get_text_dimensions(level_number, font=get_font(36))

    text_offset_x = int((192 - (level_text_width + level_number_width + 8)) / 2)
    text_offset_y = int((55 - max(level_text_height, level_number_height)) / 2)

    level_canvas.text(
        (text_offset_x, text_offset_y),
        level_text,
        (216, 216, 216),
        font=get_font(32),
    )
    level_canvas.text(
        (
            text_offset_x + level_text_width + 8,
            text_offset_y + level_text_height - level_number_height - 2
        ),
        level_number,
        (235, 235, 235),
        font=get_font(36),
    )

    base.paste(level_bg, (38, 256), level_bg_mask)

    experience_bg, experience_bg_mask = _create_outlined_rounded_rectangle(
        (260, 60),
        20,
        4,
        (57, 62, 70),
        _get_color_alpha(color, 0.5)
    )
    exp_canvas = ImageDraw.Draw(experience_bg)
    exp_text = f'{shorten_number(data.xp)} XP / {shorten_number(data.max_xp)}'

    font, y = get_font(28), 14
    if (text_size := font.getlength(exp_text)) > 190:
        font, y = get_font(24), 18
        text_size = font.getlength(exp_text)

    exp_canvas.text(
        (
            int((212 - text_size) / 2) + 24,
            y
        ),
        exp_text,
        (235, 235, 235),
        font=font,
    )

    base.paste(experience_bg, (252, 256), experience_bg_mask)

    messages_bg, messages_bg_mask = _create_outlined_rounded_rectangle(
        (268, 60),
        20,
        4,
        (57, 62, 70),
        _get_color_alpha(color, 0.5)
    )
    message_canvas = ImageDraw.Draw(messages_bg)
    msg_count = shorten_number(data.messages)
    msg_text = 'Messages'

    count_font, text_font, count_offset, text_offset = get_font(28), get_font(24), 14, 16
    if (text_size := (count_font.getlength(msg_count) + 12 + text_font.getlength(msg_text))) > 200:
        cfont, tfont, count_offset, text_offset = get_font(22), get_font(20), 16, 18
        text_size = cfont.getlength(msg_count) + 12 + tfont.getlength(msg_text)

    offset = int((200 - text_size) / 2)

    message_canvas.text(
        (
            offset + 40,
            count_offset
        ),
        msg_count,
        (235, 235, 235),
        font=count_font,
    )
    message_canvas.text(
        (
            offset + 48 + count_font.getlength(msg_count),
            text_offset
        ),
        msg_text,
        (216, 216, 216),
        font=text_font,
    )

    base.paste(messages_bg, (846, 256), messages_bg_mask)

    buffer = BytesIO()
    base.save(buffer, format='png')
    buffer.seek(0)

    return buffer
