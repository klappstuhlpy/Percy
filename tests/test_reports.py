from __future__ import annotations

import datetime

from app.services.moderation.reports import (
    EXCERPT_LIMIT,
    HOURLY_LIMIT,
    NOT_SET_UP,
    check_report_rate,
    report_channel_error,
    report_meta,
)

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
    from app.services.guild.notifications import ACTION_PERMISSIONS
    from app.services.moderation.reports import REPORT_ACTIONS

    for action, permission in REPORT_ACTIONS.values():
        if action:
            assert ACTION_PERMISSIONS[action] == permission


def test_report_channel_required() -> None:
    assert report_channel_error(configured=True, exists=True, can_post=True) is None
    for kw in (
        {"configured": False, "exists": False, "can_post": False},
        {"configured": True, "exists": False, "can_post": False},
        {"configured": True, "exists": True, "can_post": False},
    ):
        assert report_channel_error(**kw) == NOT_SET_UP


def test_can_post_report_needs_view_send_and_embed() -> None:
    from app.services.moderation.reports import can_post_report

    assert can_post_report(view_channel=True, send_messages=True, embed_links=True)
    assert not can_post_report(view_channel=True, send_messages=True, embed_links=False)
    assert not can_post_report(view_channel=False, send_messages=True, embed_links=True)
    assert not can_post_report(view_channel=True, send_messages=False, embed_links=True)
