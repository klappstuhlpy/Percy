"""Small, dependency-free text helpers shared by the Discord-free service layer.

Kept separate from :mod:`app.utils.formats` on purpose: that module imports ``discord``,
and services (``app/services/``) must stay importable without it.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ('slugify',)

_SLUG_STRIP_RE = re.compile(r'[^a-z0-9]+')
_APOSTROPHE_RE = re.compile(r"['’]")


def slugify(title: str) -> str:
    """Lowercase, ASCII, hyphen-separated slug (``"Avengers: Endgame"`` -> ``"avengers-endgame"``).

    Non-ASCII characters are transliterated where possible (``"Pokémon"`` -> ``"pokemon"``),
    apostrophes are dropped rather than turned into a hyphen (``"Marvel's Daredevil"`` ->
    ``"marvels-daredevil"``), and every other run of non-alphanumeric characters collapses to
    a single hyphen with leading/trailing hyphens stripped.

    May return ``""`` for a title with no ASCII alphanumerics at all -- callers that have a
    stable external id fall back to it in that case, so a *stored* slug is never empty even
    though this pure function can be.
    """
    normalized = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
    normalized = _APOSTROPHE_RE.sub('', normalized)
    return _SLUG_STRIP_RE.sub('-', normalized.lower()).strip('-')
