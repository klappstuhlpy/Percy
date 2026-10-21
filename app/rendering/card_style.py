"""Rank card styles: the closed preset sets and how a member's style is resolved.

Pure data and validation -- no Pillow, no Discord -- so the cog, the internal API and the
tests share one definition of what a valid style is. Styles are stored as *partial* dicts
(only the fields someone actually chose), and resolved field by field:

    built-in default  <-  the server's default  <-  the member's own choice

A server that locks its style skips the member layer entirely. Only presets exist: custom
background uploads are deliberately absent until uploaded images can be moderated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    'ACCENTS',
    'BARS',
    'DEFAULT_STYLE',
    'FIELDS',
    'FONTS',
    'THEMES',
    'CardStyle',
    'Palette',
    'clean_style',
    'resolve_style',
    'style_options',
)


class Palette(NamedTuple):
    background: str
    border: str
    track: str
    foreground: str
    muted: str


# key -> (label, palette). ``panel`` is the original card and mirrors base.css.
THEMES: dict[str, tuple[str, Palette]] = {
    'panel': ('Panel', Palette('#18181b', '#27272a', '#1e1e21', '#fafafa', '#71717a')),
    'ink': ('Ink', Palette('#09090b', '#18181b', '#18181b', '#fafafa', '#a1a1aa')),
    'midnight': ('Midnight', Palette('#0f172a', '#1e293b', '#1e293b', '#f8fafc', '#94a3b8')),
    'forest': ('Forest', Palette('#0f1f17', '#1c3326', '#173024', '#ecfdf5', '#86a897')),
    'paper': ('Paper', Palette('#fafafa', '#e4e4e7', '#e4e4e7', '#18181b', '#71717a')),
}

# key -> (label, start colour, end colour of the bar gradient)
ACCENTS: dict[str, tuple[str, str, str]] = {
    'coral': ('Coral', '#d97757', '#e8916f'),
    'sky': ('Sky', '#3b82f6', '#60a5fa'),
    'mint': ('Mint', '#10b981', '#34d399'),
    'violet': ('Violet', '#8b5cf6', '#a78bfa'),
    'gold': ('Gold', '#d4a017', '#f5c542'),
    'rose': ('Rose', '#e11d48', '#fb7185'),
}

BARS: dict[str, str] = {'gradient': 'Gradient', 'solid': 'Solid'}

# Keys match ``app.rendering.primitives.Font`` member names, lower-cased.
FONTS: dict[str, str] = {
    'rubik': 'Rubik',
    'inter': 'Inter',
    'poppins': 'Poppins',
    'karla': 'Karla',
    'whitney': 'Whitney',
    'ginto_bold': 'Ginto Bold',
    'ginto_nord_heavy': 'Ginto Nord Heavy',
    'menlo': 'Menlo',
    'arial_unicode': 'Arial Unicode',
}

FIELDS: dict[str, Mapping[str, Any]] = {'theme': THEMES, 'accent': ACCENTS, 'bar': BARS, 'font': FONTS}


@dataclass(frozen=True, slots=True)
class CardStyle:
    theme: str = 'panel'
    accent: str = 'coral'
    bar: str = 'gradient'
    font: str = 'rubik'

    @property
    def palette(self) -> Palette:
        return THEMES[self.theme][1]

    @property
    def accent_colors(self) -> tuple[str, str]:
        _, start, end = ACCENTS[self.accent]
        return start, end

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


DEFAULT_STYLE = CardStyle()


def clean_style(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate a partial style. Unknown fields or values raise :class:`ValueError`; a
    ``None``/empty value drops the field (it then follows the next layer down)."""
    cleaned: dict[str, str] = {}
    for key, value in (payload or {}).items():
        if key not in FIELDS:
            raise ValueError(f'unknown rank card field: {key}')
        if value is None or value == '':
            continue
        if value not in FIELDS[key]:
            raise ValueError(f'unknown {key}: {value}')
        cleaned[key] = value
    return cleaned


def _known(payload: Mapping[str, Any] | None) -> dict[str, str]:
    # Stored rows are filtered, never trusted: a preset removed later must not break a card.
    return {k: v for k, v in (payload or {}).items() if k in FIELDS and v in FIELDS[k]}


def resolve_style(
    guild_default: Mapping[str, Any] | None,
    user_style: Mapping[str, Any] | None,
    *,
    locked: bool = False,
) -> CardStyle:
    """The style a member's card renders with on a given server."""
    merged = {**_known(guild_default), **({} if locked else _known(user_style))}
    return CardStyle(**merged)


def style_options() -> dict[str, list[dict[str, str]]]:
    """Every preset with its label, for pickers (dashboard and Discord)."""
    def label(entry: Any) -> str:
        return entry if isinstance(entry, str) else entry[0]

    return {field: [{'value': k, 'label': label(v)} for k, v in values.items()] for field, values in FIELDS.items()}
