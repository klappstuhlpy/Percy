"""guild.py response models keep the exact pre-typing wire shape (FastAPI drops undeclared keys)."""
from __future__ import annotations

import pytest

from app.internal_api.routers import guild_models as m

CH = {'id': '1', 'name': 'general', 'type': 'text'}
RO = {'id': '2', 'name': 'Mod', 'color': 255}
FLAGS = dict.fromkeys(['assistant', 'router', 'moderation', 'sentinel', 'music', 'polls', 'giveaways', 'tags', 'reminders'], False)
AI = {
    'flags': {**FLAGS, 'router': True},
    'overrides': [{'channel': CH, 'channel_id': '1', 'controlled': {**FLAGS, 'music': True}, 'enabled': FLAGS}],
}
GUILD = {
    'ai': AI, 'id': 123456789012345678, 'name': 'S', 'icon_url': None, 'member_count': 10,
    'flags': {'audit_log': True, 'raid': False, 'alerts': True, 'sentinel': False, 'mentions': False},
    'audit_log_channel': CH, 'poll_channel': None, 'poll_ping_role': RO, 'poll_reason_channel': None,
    'mention_count': None, 'ignored_entities': [{'id': '3', 'type': 'role', 'name': 'x'}], 'mute_role': None,
    'alert_channel': CH, 'mod_log_channel': None, 'message_log_channel': None, 'voice_log_channel': None,
    'audit_log_flags': {'member_ban': True}, 'music_panel_channel': None, 'use_music_panel': False,
    'mod_dm_on_action': False, 'mod_require_reason': True, 'mod_reinvite_on_unban': False,
    'mod_default_ban_delete_days': 1, 'disabled_command_message': None,
    'prefixes': ['p.'], 'is_new_config': False,
}
SAMPLES = [
    (m.GuildInfo, GUILD),
    (m.AiConfig, AI),
    (m.AiAskResponse, {'available': True, 'answer': 'hi', 'suggestions': [{'label': 'p.ping', 'command': 'ping'}]}),
    (m.AiAskResponse, {'available': False, 'reason': 'off', 'answer': None, 'suggestions': []}),
    (m.Role, {'id': '2', 'name': 'Mod', 'color': 255, 'position': 3, 'permissions': 2**40, 'mentionable': False,
              'managed': False, 'hoist': True, 'icon_url': None, 'assignable': True}),
    (m.Channel, {'id': '1', 'name': 'g', 'type': 'text', 'position': 0, 'category_id': None, 'can_post': True}),
    (m.SentinelInfo, {'channel': CH, 'role': RO, 'message': 5, 'starter_role': None, 'bypass_action': 'kick',
                      'rate': '5/10', 'started_at': '2026-01-01T00:00:00', 'member_count': 2, 'needs_setup': False}),
    (m.GuildHealth, {'features': [{'key': 'audit_log', 'label': 'Audit log', 'status': 'ok', 'problems': []}],
                     'attention': 0}),
]


@pytest.mark.parametrize(('model', 'sample'), SAMPLES)
def test_round_trip_keeps_every_key(model, sample) -> None:
    # exclude_unset mirrors ai_ask's route flag: an absent ``reason`` stays absent.
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True, exclude_unset=True) == sample
