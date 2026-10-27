"""Pure warn-policy rules: expiry counting and threshold selection."""

from __future__ import annotations

import datetime

from app.cogs.modlog.warns import WarnThreshold, active_warn_count, select_threshold, warn_expiry

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
