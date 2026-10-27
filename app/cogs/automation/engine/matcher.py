"""Pure trigger-matching logic for autoresponders (no ``discord`` imports).

Kept Discord-free so it can be unit-tested directly: given a message's text and a
configured trigger, decide whether the autoresponder should fire. Compiled regexes are
cached because the same patterns are checked against every message in a guild.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

# ``regex`` rather than ``re``: it takes a match timeout, and a trigger is a pattern any server
# manager can write, evaluated on the event loop against every message.
import regex

__all__ = ('MATCH_TYPES', 'MatchType', 'is_valid_regex', 'matches')

MatchType = Literal['exact', 'contains', 'startswith', 'regex']

#: The supported match strategies, surfaced for command choices/validation.
MATCH_TYPES: tuple[MatchType, ...] = ('exact', 'contains', 'startswith', 'regex')

#: Seconds a regex trigger may spend on one message before it counts as "no match", so a
#: catastrophic-backtracking pattern like ``(a+)+$`` cannot freeze the whole bot.
REGEX_TIMEOUT = 0.05


@lru_cache(maxsize=512)
def _compile(pattern: str, ignore_case: bool) -> regex.Pattern[str] | None:
    flags = regex.IGNORECASE if ignore_case else 0
    try:
        return regex.compile(pattern, flags)
    except regex.error:
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
        if pattern is None:
            return False
        try:
            return pattern.search(content, timeout=REGEX_TIMEOUT) is not None
        except TimeoutError:
            return False

    haystack = content.casefold() if ignore_case else content
    needle = trigger.casefold() if ignore_case else trigger

    if match_type == 'exact':
        return haystack.strip() == needle.strip()
    if match_type == 'startswith':
        return haystack.lstrip().startswith(needle)
    # 'contains'
    return needle in haystack
