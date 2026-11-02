"""Response models of members / moderation / notifications round-trip their pre-typing wire shape.

FastAPI silently drops keys a response model does not declare, so each sample below is the full JSON the
handler sends today and ``model_validate -> model_dump`` must give it back unchanged.
"""

from __future__ import annotations

import pytest

from app.internal_api.routers import members as m
from app.internal_api.routers import moderation as mod
from app.internal_api.routers import notifications as n

ROLE = {'id': '5', 'name': 'Mod', 'color': 255}
CASE = {
    'case_index': 3, 'action': 'warn', 'target_id': '2', 'target_name': 'bob', 'moderator_id': '9',
    'moderator_name': 'amy', 'reason': 'x', 'created_at': '2026-01-01T00:00:00', 'pardoned': False,
}
STATS = {'total_commands': 4, 'first_command_at': '2026-01-01T00:00:00', 'top_commands': [{'command': 'ping', 'uses': 4}]}
TAG = {'id': 1, 'name': 't', 'uses': 2}
REQUEST = {
    'id': '7', 'guild_id': '1', 'requester_id': '2', 'requester_name': 'amy', 'target_id': '3', 'target_name': None,
    'action': 'timeout', 'reason': 'spam', 'duration': 600, 'status': 'pending', 'decided_by': None,
    'decided_by_name': None, 'decision_note': None, 'result': None, 'channel_id': '8', 'message_id': '9',
    'created_at': '2026-01-01T00:00:00Z', 'decided_at': None, 'expires_at': '2026-01-02T00:00:00Z',
}
IDENTITY = {
    'id': '1', 'name': 'a', 'display_name': 'A', 'avatar_url': 'u', 'joined_at': None,
    'created_at': '2020-01-01T00:00:00+00:00', 'roles': [ROLE], 'top_role': 'Mod', 'top_role_color': 255,
    'join_position': 4, 'member_count': 10, 'boosting_since': None,
}

SAMPLES: list[tuple[type, dict]] = [
    (m.MembersResponse, {'members': [{
        'id': '1', 'name': 'a', 'display_name': 'A', 'avatar_url': 'u', 'joined_at': '2020-01-01T00:00:00+00:00',
        'roles': ['5'], 'bot': False,
    }], 'total': 1}),
    (m.MemberDetail, {
        **IDENTITY, 'bot': False, 'in_guild': True, 'mutual_guilds': 2, 'permissions': 8,
        'public_flags': ['hypesquad'], 'status': 'online', 'muted': False, 'mute_role_configured': True,
        'timed_out_until': None,
        'leveling': {'level': 2, 'xp': 5, 'messages': 9, 'rank': 1},
        'cases': [{k: CASE[k] for k in (
            'case_index', 'action', 'reason', 'moderator_id', 'moderator_name', 'created_at')}],
        'case_count': 1, 'warning_count': 1, 'command_stats': STATS, 'last_seen': None,
        'names': {'usernames': [{'name': 'a', 'changed_at': 't'}], 'nicknames': []},
        'avatar_count': 3, 'owned_tags': [TAG],
    }),
    (m.MemberSelf, {
        **IDENTITY, 'leveling': {'level': 2, 'xp': 5, 'total_xp': 50, 'messages': 9, 'rank': 1},
        'economy': {'cash': 1, 'bank': 2, 'total': 3}, 'command_stats': STATS, 'owned_tags': [TAG],
    }),
    (m.MemberMessages, {'messages': [{
        'id': '1', 'channel_id': '2', 'channel_name': 'gen', 'content': 'hi', 'created_at': 't', 'edited_at': None,
        'jump_url': 'j', 'attachments': ['a'], 'embed_count': 0,
    }], 'scanned_channels': 3, 'partial': True}),
    (m.AvatarHistoryResponse, {'avatars': [{'image': 'AAAA', 'changed_at': None}], 'total': 1}),
    (m.MemberRolesResult, {'ok': True}),
    (mod.CasesResponse, {'cases': [CASE], 'total': 1}),
    (mod.RecentCasesResponse, {'cases': [CASE]}),
    (mod.CaseActionResponse, {'ok': True, 'case': CASE}),
    (mod.CaseDeleteResponse, {'ok': True, 'case_index': 3}),
    (mod.BulkActionResponse, {'ok': True, 'successes': 1, 'failures': [{'user_id': '2', 'error': 'no'}]}),
    (mod.ActivityResponse, {'activity': [{'day': '2026-01-01', 'count': 2}], 'days': 365}),
    (mod.WarnPolicy, {
        'expiry_days': None, 'thresholds': [{'warn_count': 3, 'action': 'mute', 'duration_seconds': 60}],
        'reasons': [{'name': 'spam', 'points': 3, 'description': None}], 'actions': ['kick'],
    }),
    (n.NotificationList, {'items': [{
        'id': 4, 'kind': 'k', 'severity': 'info', 'title': 't', 'body': 'b', 'link': None, 'meta': {'channel_id': '5'},
        'created_at': '2026-01-01T00:00:00Z', 'global': False, 'unread': True,
    }], 'unread': 1, 'last_read_id': 3}),
    (n.NotificationSummary, {'unread': 1, 'pending_requests': 2}),
    (n.NotificationsReadResult, {'ok': True, 'unread': 0}),
    (n.ActionRequestList, {'items': [REQUEST]}),
    (n.RequestSettings, {
        'channel': {'id': '1', 'name': 'req', 'type': 'text'}, 'role_ids': ['5'], 'roles': [ROLE],
    }),
    (n.RequestDecision, {'ok': False, 'status': 'denied', 'message': 'm', 'request': REQUEST}),
    (n.RequestDecision, {'ok': False, 'status': '', 'message': 'm', 'request': None}),
]


@pytest.mark.parametrize(('model', 'sample'), SAMPLES, ids=lambda v: v.__name__ if isinstance(v, type) else '')
def test_round_trip(model: type, sample: dict) -> None:
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample  # type: ignore[attr-defined]


def test_member_action_omits_unset_extras() -> None:
    # Dropped keys must stay dropped: the route excludes None so a kick is still just {ok, action}.
    route = next(r for r in m.router.routes if getattr(r, 'path', '').endswith('/members/{user_id}/action'))
    assert route.response_model_exclude_none  # type: ignore[attr-defined]
    full = {'ok': True, 'action': 'timeout', 'until': '2026-01-01T00:00:00+00:00'}
    assert m.MemberActionResult.model_validate(full).model_dump(exclude_none=True) == full
    purge = {'ok': True, 'action': 'purge', 'deleted': 0}
    assert m.MemberActionResult.model_validate(purge).model_dump(exclude_none=True) == purge
