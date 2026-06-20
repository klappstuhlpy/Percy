"""Pure trigger-matching logic for autoresponders (no ``discord`` imports).

Kept Discord-free so it can be unit-tested directly: given a message's text and a
configured trigger, decide whether the autoresponder should fire. Compiled regexes are
cached because the same patterns are checked against every message in a guild.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

__all__ = ('MATCH_TYPES', 'MatchType', 'is_valid_regex', 'matches')

MatchType = Literal['exact', 'contains', 'startswith', 'regex']

#: The supported match strategies, surfaced for command choices/validation.
MATCH_TYPES: tuple[MatchType, ...] = ('exact', 'contains', 'startswith', 'regex')


@lru_cache(maxsize=512)
def _compile(pattern: str, ignore_case: bool) -> re.Pattern[str] | None:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def is_valid_regex(pattern: str) -> bool:
    """Whether ``pattern`` compiles as a regular expression."""
    return _compile(pattern, False) is not None


def matches(content: str, trigger: str, match_type: MatchType, *, ignore_case: bool = True) -> bool:
    """Return whether ``content`` satisfies ``trigger`` under ``match_type``.

    ``exact``/``contains``/``startswith`` are plain string comparisons (optionally
    case-insensitive); ``regex`` searches with the compiled pattern. An invalid regex
    never matches rather than raising, so a bad pattern can't break the message handler.
    """
    if not content or not trigger:
        return False

    if match_type == 'regex':
        pattern = _compile(trigger, ignore_case)
        return pattern is not None and pattern.search(content) is not None

    haystack = content.casefold() if ignore_case else content
    needle = trigger.casefold() if ignore_case else trigger

    if match_type == 'exact':
        return haystack.strip() == needle.strip()
    if match_type == 'startswith':
        return haystack.lstrip().startswith(needle)
    # 'contains'
    return needle in haystack
