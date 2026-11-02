"""Pure warn-policy rules: expiry counting and threshold selection."""

from __future__ import annotations

import datetime

from app.cogs.modlog.warns import (
    WarnThreshold,
    active_warn_count,
    active_warn_points,
    resolve_warn_reason,
    select_threshold,
    warn_expiry,
)

NOW = datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC)
DAY = datetime.timedelta(days=1)


def test_expired_warns_do_not_count() -> None:
    naive = NOW.replace(tzinfo=None)
    assert active_warn_count([None, naive + DAY, naive - DAY, naive], NOW) == 2


def test_warn_expiry() -> None:
    assert warn_expiry(NOW, None) is None
    assert warn_expiry(NOW, 0) is None
    assert warn_expiry(NOW, 30) == NOW + 30 * DAY


def test_threshold_fires_only_on_exact_count() -> None:
    rules = [WarnThreshold(3, 'timeout', 3600), WarnThreshold(5, 'kick')]
    assert select_threshold(rules, 2) is None
    assert select_threshold(rules, 3) == rules[0]
    assert select_threshold(rules, 4) is None  # no re-escalation for the 4th warn
    assert select_threshold(rules, 5) == rules[1]


def test_points_sum_only_active_warns() -> None:
    naive = NOW.replace(tzinfo=None)
    rows = [(None, 3), (naive + DAY, 2), (naive - DAY, 50), (naive, 7)]
    assert active_warn_points(rows, NOW) == 5


def test_threshold_fires_when_a_heavy_warn_crosses_it() -> None:
    rules = [WarnThreshold(3, 'timeout', 3600), WarnThreshold(5, 'kick')]
    assert select_threshold(rules, 4, previous=1) == rules[0]  # jumped over 3, still fires
    assert select_threshold(rules, 6, previous=1) == rules[1]  # crossed both: the harshest wins
    assert select_threshold(rules, 4, previous=3) is None  # already past 3 before this warn
    assert select_threshold(rules, 2, previous=1) is None


PRESETS = {'spam': (3, 'Spamming'), 'bare': (2, None)}


def test_reason_preset_match_is_case_insensitive_and_appends_extra_text() -> None:
    assert resolve_warn_reason('SPAM', PRESETS) == ('Spamming', 3)
    assert resolve_warn_reason('spam in #general', PRESETS) == ('Spamming - in #general', 3)
    assert resolve_warn_reason('bare', PRESETS) == ('bare', 2)  # no description: keep the name


def test_unknown_or_missing_reason_is_one_point() -> None:
    assert resolve_warn_reason('being rude', PRESETS) == ('being rude', 1)
    assert resolve_warn_reason('spammer', PRESETS) == ('spammer', 1)  # whole word only
    assert resolve_warn_reason(None, PRESETS) == (None, 1)
    assert resolve_warn_reason('  ', PRESETS) == ('  ', 1)
