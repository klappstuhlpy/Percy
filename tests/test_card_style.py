from __future__ import annotations

import pytest
from PIL import Image

from app.rendering.card_style import ACCENTS, FONTS, THEMES, CardStyle, clean_style, resolve_style, style_options
from app.rendering.models import LevelCardData
from app.rendering.primitives import Font, FontManager
from app.rendering.templates import leveling


def test_clean_style_rejects_unknown_fields_and_values() -> None:
    assert clean_style({'theme': 'ink', 'accent': None, 'bar': ''}) == {'theme': 'ink'}
    with pytest.raises(ValueError, match='unknown theme'):
        clean_style({'theme': 'neon'})
    with pytest.raises(ValueError, match='unknown rank card field'):
        clean_style({'background_url': 'https://example.com/x.png'})


def test_resolve_layers_member_over_server_unless_locked() -> None:
    guild = {'theme': 'midnight', 'accent': 'sky'}
    user = {'accent': 'rose', 'font': 'inter', 'theme': 'gone-preset'}
    assert resolve_style(guild, user) == CardStyle(theme='midnight', accent='rose', font='inter')
    assert resolve_style(guild, user, locked=True) == CardStyle(theme='midnight', accent='sky')
    assert resolve_style(None, None) == CardStyle()


def test_every_font_key_names_a_font() -> None:
    for key in FONTS:
        Font[key.upper()]
    assert {o['value'] for o in style_options()['font']} == set(FONTS)


@pytest.mark.parametrize('theme', list(THEMES))
def test_every_theme_renders(theme: str) -> None:
    accent = list(ACCENTS)[list(THEMES).index(theme) % len(ACCENTS)]
    data = LevelCardData(
        avatar=_avatar(), name='Klappstuhl', total_xp=5000, rank=1, member_count=10,
        level=4, xp=40, max_xp=100, messages=12, style=CardStyle(theme=theme, accent=accent, bar='solid'),
    )
    image = Image.open(leveling.draw_level_card(data, FontManager()))
    assert image.size == (leveling.WIDTH, leveling.HEIGHT)
    assert image.getpixel((5, 5)) == Image.new('RGB', (1, 1), THEMES[theme][1].background).getpixel((0, 0))


def _avatar() -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    Image.new('RGB', (64, 64), '#888888').save(buffer, format='png')
    return buffer.getvalue()
