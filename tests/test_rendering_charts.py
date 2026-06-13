"""Tests for the chart templates (:mod:`app.rendering.templates.charts` and the
equalizer in :mod:`app.rendering.templates.music`).

These render real PNGs from canned data, so you can generate a poker odds
analysis chart without playing a game (or an equalizer graph without a music
session). The rendered images are written to ``tests/artifacts/`` for visual
inspection:

    poetry run pytest tests/test_rendering_charts.py -q

then open e.g. ``tests/artifacts/poker_odds_analysis.png``.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from app.rendering.models import ActiveBoost, BarChartData, LevelCardData, PresenceData
from app.rendering.primitives import FontManager
from app.rendering.templates import charts, leveling, music

ARTIFACTS = Path(__file__).parent / 'artifacts'

# Hand-strength distributions exactly as the poker engine produces them:
# `engine.analysis[stage][1][seat]` maps NAMED_HAND categories to percentages
# (see `_hand_strength_analysis` in app/cogs/games/engine/poker.py). The titles
# mirror TITLE_MAP in poker_ui.py's `_on_analysis`.
POKER_ODDS_STAGES = [
    BarChartData(
        data={
            'High Card': 17.41,
            'One Pair': 43.84,
            'Two Pairs': 23.5,
            'Three of a Kind': 4.83,
            'Straight': 3.1,
            'Flush': 2.96,
            'Full House': 3.95,
            'Four of a Kind': 0.33,
            'Straight Flush': 0.05,
            'Royal Flush': 0.03,
        },
        title='Seat #1 - Hand Strength Analysis | Pre-Flop',
    ),
    BarChartData(
        data={
            'High Card': 8.12,
            'One Pair': 39.97,
            'Two Pairs': 31.4,
            'Three of a Kind': 7.51,
            'Straight': 4.62,
            'Flush': 5.18,
            'Full House': 2.87,
            'Four of a Kind': 0.33,
        },
        title='Flop',
    ),
    BarChartData(
        data={
            'One Pair': 30.0,
            'Two Pairs': 42.5,
            'Three of a Kind': 12.5,
            'Full House': 15.0,
        },
        title='Turn',
    ),
]


def _save(image: Image.Image, name: str) -> Path:
    ARTIFACTS.mkdir(exist_ok=True)
    path = ARTIFACTS / name
    image.save(path)
    return path


def test_poker_odds_analysis_chart() -> None:
    """Render the full poker odds analysis exactly as `_on_analysis` does."""
    images: list[Image.Image] = []
    for spec in POKER_ODDS_STAGES:
        pages = charts.render_bar_chart_images(spec)
        assert len(pages) == 1  # each stage has at most 10 hands -> one page
        assert pages[0].width == charts.CHART_WIDTH_PX
        images.extend(pages)

    merged = charts.merge_images_vertical(images)
    assert merged.width == charts.CHART_WIDTH_PX
    assert merged.height == sum(image.height for image in images)

    path = _save(merged, 'poker_odds_analysis.png')
    print(f'\npoker odds analysis chart written to {path}')


def test_bar_chart_paginates_and_keeps_global_scale() -> None:
    data = BarChartData(data={f'command {i}': 100 - i * 2 for i in range(40)}, title='Command Usage')

    pages = charts.render_bar_chart_images(data)

    assert len(pages) == 3  # 40 bars / 18 per page
    assert all(page.width == charts.CHART_WIDTH_PX for page in pages)
    # Pages with more bars are taller.
    assert pages[0].height > pages[-1].height

    path = _save(charts.merge_images_vertical(pages), 'bar_chart_paginated.png')
    print(f'\npaginated bar chart written to {path}')


def test_bar_chart_handles_empty_data() -> None:
    pages = charts.render_bar_chart_images(BarChartData(data={}, title='Empty'))

    assert len(pages) == 1
    assert pages[0].width == charts.CHART_WIDTH_PX


def test_presence_donut_chart() -> None:
    data = PresenceData(
        labels=['Online', 'Offline', 'DND', 'Idle'],
        values=[86400, 43200, 7200, 14400],
        colors=['#43b581', '#747f8d', '#f04747', '#fba31c'],
    )

    buffer = charts.draw_presence_chart(data)
    image = Image.open(buffer)

    assert image.format == 'PNG'
    assert image.size == (1800, 1200)

    path = _save(image, 'presence_donut.png')
    print(f'\npresence donut written to {path}')


def test_presence_donut_chart_with_no_data() -> None:
    # All-zero durations must not crash (renders an empty track ring).
    data = PresenceData(labels=['Online'], values=[0], colors=['#43b581'])

    buffer = charts.draw_presence_chart(data)

    assert Image.open(buffer).format == 'PNG'


def test_equalizer_chart() -> None:
    """Render a bass-boost style curve over Lavalink's 15 bands."""
    gains = [0.25, 0.32, 0.28, 0.18, 0.08, 0.0, -0.05, -0.1, -0.08, -0.02, 0.0, 0.05, 0.12, 0.2, 0.24]
    assert len(gains) == len(music.BAND_LABELS)

    buffer = music.draw_equalizer(gains)
    image = Image.open(buffer)

    assert image.format == 'PNG'
    assert image.size == (music.WIDTH_PX, music.HEIGHT_PX)

    path = _save(image, 'equalizer.png')
    print(f'\nequalizer chart written to {path}')


def test_equalizer_chart_flat() -> None:
    buffer = music.draw_equalizer([0.0] * 15)

    assert Image.open(buffer).format == 'PNG'


def _synthetic_avatar() -> bytes:
    """A stand-in avatar so the card renders without fetching from Discord."""
    image = Image.new('RGB', (256, 256), '#5865F2')
    draw = ImageDraw.Draw(image)
    draw.ellipse((64, 48, 192, 176), fill='#fafafa')
    draw.rectangle((64, 176, 192, 256), fill='#fafafa')
    buffer = BytesIO()
    image.save(buffer, format='png')
    return buffer.getvalue()


def test_level_card() -> None:
    """Render a rank card from canned data without a Discord member."""
    data = LevelCardData(
        avatar=_synthetic_avatar(),
        name='Klappstuhl',
        total_xp=1_234_567,
        rank=3,
        member_count=12_345,
        level=42,
        xp=3_400,
        max_xp=5_000,
        messages=8_201,
        boosts=[ActiveBoost(kind="XP", percent=50), ActiveBoost(kind="XP 2 boost", percent=100), ActiveBoost(kind="test3", percent=5)],
    )

    buffer = leveling.draw_level_card(data, FontManager())
    image = Image.open(buffer)

    assert image.format == 'PNG'
    assert image.size == (leveling.WIDTH, leveling.HEIGHT)

    path = _save(image, 'level_card.png')
    print(f'\nlevel card written to {path}')


def test_level_card_truncates_long_names_and_handles_zero_xp() -> None:
    data = LevelCardData(
        avatar=_synthetic_avatar(),
        name='An Extremely Long Display Name That Cannot Possibly Fit On The Card',
        total_xp=0,
        rank=12_000,
        member_count=12_345,
        level=0,
        xp=0,
        max_xp=100,
        messages=0,
    )

    buffer = leveling.draw_level_card(data, FontManager())

    assert Image.open(buffer).size == (leveling.WIDTH, leveling.HEIGHT)


def test_equalizer_clamps_gains_and_handles_odd_band_counts() -> None:
    # Out-of-range gains are clamped to Lavalink's [-0.25, 1.0]; band counts other
    # than 15 fall back to numeric labels. Neither may crash.
    buffer = music.draw_equalizer([5.0, -5.0, 0.5, 0.0, 1.5])

    image = Image.open(buffer)
    assert image.format == 'PNG'
    assert image.size == (music.WIDTH_PX, music.HEIGHT_PX)
