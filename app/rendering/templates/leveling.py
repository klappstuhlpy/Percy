"""Pure drawing logic for the rank/level card. No domain models, no Discord, no I/O
beyond reading bundled assets and returning a buffer.

Composited with Pillow but styled after the klappstuhl.me admin design system shared
with the matplotlib charts (tokens in :mod:`app.rendering.templates.theme`): panel
surface with border, muted uppercase labels, foreground values and a coral-gradient
pill progress bar matching the bar charts. The member's configured font is still
used for their display name; all structural text uses the theme fonts.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageColor, ImageDraw

from app.rendering.primitives import ASSETS, FONT_MAPPING, FontManager, mask_to_circle, rounded_mask
from app.rendering.templates.theme import BRAND, BRAND_BRIGHT, FOREGROUND, MUTED, PANEL_BG, PANEL_BORDER, TRACK_BG
from app.utils import shorten_number

if TYPE_CHECKING:
    from app.rendering.models import ActiveBoost, LevelCardData

__all__ = ('draw_level_card',)

WIDTH = 1154
HEIGHT = 360
PAD = 40

AVATAR_SIZE = 150
AVATAR_POS = (PAD, 42)
CONTENT_X = 240  # text column right of the avatar

BAR_HEIGHT = 26  # same pill geometry as the bar charts
BAR_Y = 296


def _round_avatar(avatar: bytes, size: int) -> Image.Image:
    """Decode, resize and circle-mask an avatar."""
    image = Image.open(BytesIO(avatar)).convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
    return mask_to_circle(image)


def _ring(diameter: int, thickness: int, color: str, *, quality: int = 4) -> Image.Image:
    """An anti-aliased circle outline (the avatar's coral ring)."""
    big = diameter * quality
    image = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    inset = thickness * quality // 2
    ImageDraw.Draw(image).ellipse(
        (inset, inset, big - inset, big - inset), outline=color, width=thickness * quality
    )
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


def _gradient_pill(width: int, height: int) -> tuple[Image.Image, Image.Image]:
    """A coral->bright-coral gradient bar and its pill mask (matches the charts)."""
    start = np.asarray(ImageColor.getrgb(BRAND), dtype=float)
    end = np.asarray(ImageColor.getrgb(BRAND_BRIGHT), dtype=float)
    t = np.linspace(0.0, 1.0, max(width, 1))[:, None]
    row = (start[None, :] * (1 - t) + end[None, :] * t).astype(np.uint8)  # (width, 3)
    gradient = Image.fromarray(np.broadcast_to(row[None, :, :], (height, max(width, 1), 3)).copy())
    return gradient, rounded_mask((max(width, 1), height), height // 2)


def draw_level_card(data: LevelCardData, fonts: FontManager) -> BytesIO:
    """Draws a rank card for the given prepared data and returns a PNG buffer."""
    name_font_path = str(ASSETS / FONT_MAPPING[data.font.value])
    rubik = str(ASSETS / 'fonts/rubik.ttf')
    poppins = str(ASSETS / 'fonts/poppins.ttf')

    base = Image.new('RGB', (WIDTH, HEIGHT), PANEL_BG)
    draw = ImageDraw.Draw(base)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=PANEL_BORDER, width=2)

    # Avatar with a coral ring.
    ring_gap = 7
    ring = _ring(AVATAR_SIZE + 2 * ring_gap, 3, BRAND)
    base.paste(ring, (AVATAR_POS[0] - ring_gap, AVATAR_POS[1] - ring_gap), ring)
    avatar = _round_avatar(data.avatar, AVATAR_SIZE)
    base.paste(avatar, AVATAR_POS, avatar)

    # Rank block, right-aligned (drawn first so the name knows where to stop).
    right = WIDTH - PAD
    rank_text = f'#{data.rank}'
    draw.text((right, 46), 'RANK', font=fonts.get(poppins, 18), fill=MUTED, anchor='ra')
    draw.text((right, 72), rank_text, font=fonts.get(rubik, 46), fill=FOREGROUND, anchor='ra')
    draw.text((right, 130), f'of {shorten_number(data.member_count)}', font=fonts.get(rubik, 20), fill=MUTED, anchor='ra')

    rank_left = right - max(
        draw.textlength('RANK', font=fonts.get(poppins, 18)),
        draw.textlength(rank_text, font=fonts.get(rubik, 46)),
        draw.textlength(f'of {shorten_number(data.member_count)}', font=fonts.get(rubik, 20)),
    )

    # Name in the member's configured font, truncated before the rank block.
    name_font = fonts.get(name_font_path, 44)
    name = data.name
    max_name_width = rank_left - CONTENT_X - 30
    while name and draw.textlength(f'{name}…' if name != data.name else name, font=name_font) > max_name_width:
        name = name[:-1]
    if name != data.name:
        name = f'{name}…'
    draw.text((CONTENT_X, 46), name, font=name_font, fill=FOREGROUND)
    draw.text((CONTENT_X, 106), f'{data.total_xp:,} XP total', font=fonts.get(rubik, 22), fill=MUTED)

    # Stats row, metric-tile style: muted uppercase label over a foreground value.
    label_font = fonts.get(poppins, 17)
    value_font = fonts.get(rubik, 32)
    label_y, value_y = 212, 240
    xp_text = f'{shorten_number(data.xp)} / {shorten_number(data.max_xp)} XP'

    draw.text((PAD, label_y), 'LEVEL', font=label_font, fill=MUTED)
    draw.text((PAD, value_y), str(data.level), font=value_font, fill=FOREGROUND)

    draw.text((WIDTH / 2, label_y), 'XP', font=label_font, fill=MUTED, anchor='ma')
    draw.text((WIDTH / 2, value_y), xp_text, font=value_font, fill=FOREGROUND, anchor='ma')

    draw.text((right, label_y), 'MESSAGES', font=label_font, fill=MUTED, anchor='ra')
    draw.text((right, value_y), shorten_number(data.messages), font=value_font, fill=FOREGROUND, anchor='ra')

    # Active boost badges below the avatar.
    if data.boosts:
        boost_font = fonts.get(poppins, 15)
        badge_y = AVATAR_POS[1] + AVATAR_SIZE + 12
        badge_x = AVATAR_POS[0]
        for boost in data.boosts:
            label = f"+{boost.percent}% {'XP' if boost.kind == 'xp' else 'Loot'}"
            tw = int(draw.textlength(label, font=boost_font))
            bw, bh = tw + 16, 22
            badge = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
            badge_draw = ImageDraw.Draw(badge)
            badge_draw.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=bh // 2, fill=BRAND, outline=BRAND_BRIGHT)
            badge_draw.text((bw // 2, bh // 2), label, font=boost_font, fill=FOREGROUND, anchor='mm')
            base.paste(badge, (badge_x, badge_y), badge)
            badge_x += bw + 6

    # Progress bar: full-width track rail with a gradient pill, like the bar charts.
    bar_width = WIDTH - 2 * PAD
    track = Image.new('RGB', (bar_width, BAR_HEIGHT), TRACK_BG)
    base.paste(track, (PAD, BAR_Y), rounded_mask((bar_width, BAR_HEIGHT), BAR_HEIGHT // 2))

    ratio = min(max(data.xp / data.max_xp, 0.0), 1.0) if data.max_xp else 0.0
    if ratio > 0:
        fill_width = max(int(bar_width * ratio), BAR_HEIGHT)
        gradient, mask = _gradient_pill(fill_width, BAR_HEIGHT)
        base.paste(gradient, (PAD, BAR_Y), mask)

    buffer = BytesIO()
    base.save(buffer, format='png')
    buffer.seek(0)

    return buffer
