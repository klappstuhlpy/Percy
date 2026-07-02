"""Tests for the pure autoresponder matcher (:mod:`app.cogs.autoresponder.engine`).

No Discord involved -- the matcher only sees message text and a configured trigger,
which is exactly what makes the on-message hot path testable in isolation.
"""

from __future__ import annotations

from app.cogs.automation.engine import is_valid_regex, matches

# -- contains --------------------------------------------------------------


def test_contains_matches_substring_case_insensitively() -> None:
    assert matches('Well HELLO there', 'hello', 'contains') is True


def test_contains_respects_case_when_requested() -> None:
    assert matches('say Hello', 'hello', 'contains', ignore_case=False) is False
    assert matches('say hello', 'hello', 'contains', ignore_case=False) is True


# -- exact -----------------------------------------------------------------


def test_exact_matches_whole_message_only() -> None:
    assert matches('ping', 'ping', 'exact') is True
    assert matches('  ping  ', 'ping', 'exact') is True  # surrounding whitespace ignored
    assert matches('ping pong', 'ping', 'exact') is False


# -- startswith ------------------------------------------------------------


def test_startswith_matches_leading_phrase() -> None:
    assert matches('!help me please', '!help', 'startswith') is True
    assert matches('please !help', '!help', 'startswith') is False


# -- regex -----------------------------------------------------------------


def test_regex_searches_pattern() -> None:
    assert matches('order #1234 placed', r'#\d+', 'regex') is True
    assert matches('order placed', r'#\d+', 'regex') is False


def test_invalid_regex_never_matches_instead_of_raising() -> None:
    # A malformed pattern must not break the message handler.
    assert matches('anything', '[', 'regex') is False
    assert is_valid_regex('[') is False
    assert is_valid_regex(r'\d+') is True


# -- guards ----------------------------------------------------------------


def test_empty_content_or_trigger_does_not_match() -> None:
    assert matches('', 'hello', 'contains') is False
    assert matches('hello', '', 'contains') is False
