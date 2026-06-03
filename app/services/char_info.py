"""Unicode character introspection.

Extracted from the ``meta`` cog's ``charinfo`` command: turning a character into its
codepoint, escape sequence, Unicode name and reference URL is pure logic, so it lives
here (Discord-free, testable). The cog keeps input validation and markdown formatting.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

__all__ = (
    'MAX_CHARACTERS',
    'CharInfo',
    'get_char_info',
)

#: The most characters ``charinfo`` will describe in one invocation.
MAX_CHARACTERS = 50


@dataclass(slots=True)
class CharInfo:
    """Structured Unicode facts about a single character."""

    char: str
    codepoint: str  # lowercase hex, e.g. '1f600'
    escape: str     # Python escape, e.g. r'é' or r'\U0001f600'
    name: str       # Unicode name, or '' if the character has none
    url: str        # compart.com reference URL


def get_char_info(char: str) -> CharInfo:
    """Build a :class:`CharInfo` for ``char`` (assumed to be a single character)."""
    digit = f'{ord(char):x}'
    escape = rf'\u{digit:>04}' if len(digit) <= 4 else rf'\U{digit:>08}'
    url = f'https://www.compart.com/en/unicode/U+{digit:>04}'
    return CharInfo(
        char=char,
        codepoint=digit,
        escape=escape,
        name=unicodedata.name(char, ''),
        url=url,
    )
