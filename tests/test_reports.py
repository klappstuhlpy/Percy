from __future__ import annotations

import datetime

from app.services.reports import EXCERPT_LIMIT, HOURLY_LIMIT, check_report_rate, report_meta

NOW = datetime.datetime(2026, 1, 1, 12, 0)


def ago(minutes: int) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


def test_first_report_allowed() -> None:
    assert check_report_rate([], 1, NOW) is None


def test_same_target_blocked_within_ten_minutes_only() -> None:
    assert check_report_rate([(1, ago(9))], 1, NOW) is not None
    assert check_report_rate([(1, ago(11))], 1, NOW) is None
    assert check_report_rate([(2, ago(1))], 1, NOW) is None


def test_hourly_cap() -> None:
    recent = [(100 + i, ago(20 + i)) for i in range(HOURLY_LIMIT)]
    assert check_report_rate(recent, 1, NOW) is not None
    assert check_report_rate(recent[:-1], 1, NOW) is None
    assert check_report_rate([(100 + i, ago(61 + i)) for i in range(HOURLY_LIMIT)], 1, NOW) is None


def test_meta_shaping() -> None:
    meta = report_meta(reporter_id=1, target_id=2, reason="r", channel_id=3, message_url="u", content="x" * 500)
    assert meta["reporter_id"] == "1" and meta["target_id"] == "2" and meta["channel_id"] == "3"
    assert len(meta["excerpt"]) == EXCERPT_LIMIT
    assert "excerpt" not in report_meta(reporter_id=1, target_id=2, reason="r")


def test_report_permissions_match_action_permissions() -> None:
    from app.services.notifications import ACTION_PERMISSIONS
    from app.services.reports import REPORT_ACTIONS

    for action, permission in REPORT_ACTIONS.values():
        if action:
            assert ACTION_PERMISSIONS[action] == permission
