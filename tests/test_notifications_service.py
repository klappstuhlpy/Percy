from __future__ import annotations

import datetime
from typing import Any

from app.services.notifications import (
    ACTION_PERMISSIONS,
    REQUESTABLE_ACTIONS,
    check_request,
    notification_payload,
    request_payload,
)


def test_check_request() -> None:
    assert check_request('ban', None) is None
    assert check_request('timeout', 3600) is None
    assert check_request('mute', 60) is None
    assert check_request('purge', None)
    assert check_request('nope', None)
    assert check_request('timeout', None)
    assert check_request('timeout', 30)
    assert check_request('timeout', 2_419_201)
    assert check_request('kick', 60)


def test_requestable_excludes_purge() -> None:
    assert 'purge' not in REQUESTABLE_ACTIONS
    assert len(ACTION_PERMISSIONS) == 10


def _note(**kw: Any) -> dict[str, Any]:
    return {'id': 5, 'guild_id': None, 'kind': 'notice', 'severity': 'info', 'title': 't', 'body': '',
            'link': None, 'meta': {}, 'created_at': datetime.datetime(2026, 1, 2, 3, 4, 5), **kw}


def test_notification_payload() -> None:
    p = notification_payload(_note(), 4)
    assert p['global'] is True and p['unread'] is True
    assert p['created_at'] == '2026-01-02T03:04:05Z'
    p = notification_payload(_note(guild_id=1), 5)
    assert p['global'] is False and p['unread'] is False


def test_notification_payload_decodes_json_meta() -> None:
    assert notification_payload(_note(meta='{"a": 1}'), 0)['meta'] == {'a': 1}


def test_request_payload() -> None:
    now = datetime.datetime(2026, 1, 2)
    p = request_payload({'id': 1, 'guild_id': 2, 'requester_id': 3, 'target_id': 4, 'action': 'ban',
                         'reason': 'r', 'duration': None, 'status': 'pending', 'decided_by': None,
                         'decision_note': None, 'result': None, 'channel_id': None, 'message_id': 9,
                         'created_at': now, 'decided_at': None, 'expires_at': now})
    assert p['id'] == '1' and p['target_id'] == '4' and p['message_id'] == '9'
    assert p['decided_by'] is None and p['channel_id'] is None and p['duration'] is None
    assert p['expires_at'] == '2026-01-02T00:00:00Z' and p['decided_at'] is None
