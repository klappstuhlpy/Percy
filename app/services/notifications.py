"""Pure rules and payload shaping for guild notifications and moderator -> admin action requests.

Discord-free; the SQL lives in :mod:`app.database.repositories.notifications`.
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    'ACTION_PERMISSIONS',
    'DEDUPE_WINDOW',
    'KINDS',
    'REQUESTABLE_ACTIONS',
    'REQUEST_STATUSES',
    'REQUEST_TTL',
    'RETENTION',
    'SEVERITIES',
    'check_request',
    'notification_payload',
    'request_payload',
)

KINDS = ('missing_permission', 'action_request', 'request_decided', 'webhook_disabled', 'notice')
SEVERITIES = ('info', 'warning', 'critical')
REQUEST_STATUSES = ('pending', 'approved', 'denied', 'cancelled', 'failed', 'expired')
REQUEST_TTL = datetime.timedelta(hours=24)
RETENTION = datetime.timedelta(days=90)
DEDUPE_WINDOW = datetime.timedelta(hours=1)

# Each action and the permission the acting moderator needs for it -- the same one the matching
# command requires (warn mirrors the ``warn`` command's Kick Members).
ACTION_PERMISSIONS: dict[str, str] = {
    'kick': 'kick_members',
    'ban': 'ban_members',
    'unban': 'ban_members',
    'softban': 'ban_members',
    'warn': 'kick_members',
    'mute': 'manage_roles',
    'unmute': 'manage_roles',
    'timeout': 'moderate_members',
    'untimeout': 'moderate_members',
    'purge': 'manage_messages',
}
# Purge reads live channel history and has no single target; unban's target is no longer a member.
REQUESTABLE_ACTIONS = frozenset(ACTION_PERMISSIONS) - {'purge', 'unban'}

_TIMEOUT_MIN, _TIMEOUT_MAX = 60, 2_419_200  # Discord caps a timeout at 28 days


def check_request(action: str, duration: int | None) -> str | None:
    """Returns an error message for an invalid request, or ``None`` when it is acceptable."""
    if action not in REQUESTABLE_ACTIONS:
        return f'"{action}" cannot be requested.'
    if duration is not None and action not in ('timeout', 'mute'):
        return 'A duration only applies to timeout and mute.'
    if action == 'timeout' and (duration is None or not _TIMEOUT_MIN <= duration <= _TIMEOUT_MAX):
        return 'A timeout needs a duration between 60 seconds and 28 days.'
    return None


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() + 'Z' if value else None


def _id(value: int | None) -> str | None:
    return None if value is None else str(value)


def notification_payload(row: Mapping[str, Any], last_read_id: int) -> dict[str, Any]:
    meta = row['meta']
    if isinstance(meta, str):  # asyncpg hands JSONB back as text unless a codec is registered
        meta = json.loads(meta)
    return {
        'id': row['id'],
        'kind': row['kind'],
        'severity': row['severity'],
        'title': row['title'],
        'body': row['body'],
        'link': row['link'],
        'meta': meta or {},
        'created_at': _iso(row['created_at']),
        'global': row['guild_id'] is None,
        'unread': row['id'] > last_read_id,
    }


def request_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        'id': str(row['id']),
        'guild_id': str(row['guild_id']),
        'requester_id': str(row['requester_id']),
        'target_id': str(row['target_id']),
        'action': row['action'],
        'reason': row['reason'],
        'duration': row['duration'],
        'status': row['status'],
        'decided_by': _id(row['decided_by']),
        'decision_note': row['decision_note'],
        'result': row['result'],
        'channel_id': _id(row['channel_id']),
        'message_id': _id(row['message_id']),
        'created_at': _iso(row['created_at']),
        'decided_at': _iso(row['decided_at']),
        'expires_at': _iso(row['expires_at']),
    }
