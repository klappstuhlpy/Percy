"""Tests for pure config-boundary validation helpers in ``config.py``.

Only the standalone helpers are tested here -- the rest of ``config.py`` is a plain module
executed at import time from real env vars, not something to re-exercise per-branch.
"""

from __future__ import annotations

import pytest

from config import _parse_regions


def test_parse_regions_defaults_when_unset() -> None:
    assert _parse_regions(None) == ('US', 'GB', 'DE')


def test_parse_regions_uppercases_and_strips() -> None:
    assert _parse_regions(' us, gb ,de') == ('US', 'GB', 'DE')


def test_parse_regions_rejects_non_two_character_codes() -> None:
    with pytest.raises(ValueError, match='USA'):
        _parse_regions('USA')
