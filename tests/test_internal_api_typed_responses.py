"""Stats, outbound webhooks, backup templates, role menus and starboard keep their wire shape.

FastAPI drops keys a response model does not declare, so every model must round-trip a realistic
sample unchanged (see dashboard/crates/percy-client/GENERATION.md). Samples mirror the literals
the handlers returned before they were typed.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.internal_api.routers import backup, rolemenus, starboard, stats, subscriptions

CHANNEL = {'id': '123', 'name': 'general', 'type': 'text'}

STARBOARD_SETTINGS = {
    'configured': True, 'enabled': True, 'channel': CHANNEL, 'threshold': 3, 'emoji': '⭐',
    'self_star': False, 'ignored_channels': [CHANNEL, None], 'max_age_hours': 0, 'allow_nsfw': False,
}
SUBSCRIPTION = {
    'id': 4, 'url': 'https://example.com/hook', 'events': ['member_join'], 'label': None, 'enabled': True,
    'secret': None, 'created_at': '2026-11-01T10:00:00', 'last_delivery_at': None, 'last_status': 200,
    'failure_count': 0,
}
GAME = {'game': 'poker', 'label': 'Poker', 'icon': None, 'played': 10, 'won': 4, 'players': 3, 'profit': -20}
PLAYER = {
    'user_id': '5', 'username': 'ann', 'avatar_url': None, 'played': 10, 'won': 4, 'winrate': 0.4, 'profit': 50,
}

SAMPLES = [
    (starboard.StarboardInfo, {**STARBOARD_SETTINGS, 'top': [
        {'stars': 5, 'author': None, 'channel': CHANNEL, 'jump_url': 'https://discord.com/channels/1/2/3'}]}),
    (starboard.StarboardSaved, {'ok': True, **STARBOARD_SETTINGS}),
    (rolemenus.RoleMenusResponse, {'max_roles': 25, 'menus': [{
        'id': 1, 'title': 'Pings', 'description': None, 'unique': False, 'channel': CHANNEL,
        'jump_url': None, 'roles': [{
            'role_id': '9', 'name': None, 'color': 0, 'emoji': None, 'label': 'News', 'assignable': False}],
    }]}),
    (rolemenus.RoleMenuCreated, {'ok': True, 'id': 2, 'message': 'posted'}),
    (rolemenus.RoleMenuNotice, {'ok': True, 'message': 'Added @x.'}),
    (subscriptions.WebhookSubscriptionsResponse, {'subscriptions': [SUBSCRIPTION]}),
    (subscriptions.WebhookCreateResponse, {'ok': True, 'subscription': {**SUBSCRIPTION, 'secret': 'abc'}}),
    (subscriptions.WebhookEventsResponse, {'events': ['member_join', 'member_leave']}),
    (subscriptions.WebhookDeliveriesResponse, {'deliveries': [{
        'event': 'ping', 'success': False, 'status_code': None, 'attempts': 3, 'error': 'timeout',
        'created_at': '2026-11-01T10:00:00'}]}),
    (subscriptions.WebhookTestResponse, {'ok': False, 'result': {'success': False, 'attempts': 3, 'error': 'x'}}),
    (backup.TemplatesResponse, {
        'public': [{'slug': 'abc', 'name': 'A', 'description': None, 'downloads': 2, 'created_at': None}],
        'own': [{'slug': 'abc', 'name': 'A', 'description': 'd', 'downloads': 2, 'created_at': None, 'public': True}],
    }),
    (stats.GuildStats, {
        'member_count': None, 'online_count': 1, 'bot_count': 1, 'human_count': 0, 'channel_count': 2,
        'role_count': 3, 'emoji_count': 4, 'boost_count': 0, 'boost_tier': 0, 'total_commands': 7,
        'top_commands': [{'command': 'ping', 'uses': 7}], 'created_at': '2020-01-01T00:00:00+00:00',
        'owner_id': '1', 'owner_name': None,
    }),
    (stats.GuildOverview, {
        'guild': {'id': 1, 'name': 'g', 'icon_url': None, 'member_count': None},
        'stats': {
            'online_count': 1, 'bot_count': 1, 'channel_count': 2, 'role_count': 3, 'emoji_count': 4,
            'boost_count': 0, 'boost_tier': 0, 'total_commands': 7, 'recent_cases': 2},
        'bot': {'version': '2.25.0', 'guild_count': 2, 'user_count': 9, 'command_count': 100, 'latency_ms': 41.5},
        'features': {'leveling': True, 'economy': True, 'music': False, 'sentinel': False, 'audit_log': True},
    }),
    (stats.GamesStats, {'games': [GAME], 'top_players': [PLAYER]}),
    (stats.BotStats, {
        'version': '2.25.0', 'guild_count': 2, 'user_count': 9, 'channel_count': 20, 'total_commands_used': 5,
        'cog_count': 30, 'command_count': 100, 'latency_ms': 41.5, 'uptime_seconds': 1234.5,
        'ai': {'reachable': True, 'models': ['m']},
    }),
    (stats.BotStats, {
        'version': '2.25.0', 'guild_count': 2, 'user_count': 9, 'channel_count': 20, 'total_commands_used': 5,
        'cog_count': 30, 'command_count': 100, 'latency_ms': 41.5, 'uptime_seconds': 1234.5, 'ai': None,
    }),
    (stats.ChangelogResponse, {'current_version': '2.25.0', 'entries': [{
        'version': '2.25.0', 'date': '2026-11-01', 'sections': [{'name': 'Added', 'slug': 'added', 'entries': ['x']}],
    }]}),
]


@pytest.mark.parametrize(('model', 'sample'), SAMPLES, ids=[f'{m.__name__}-{i}' for i, (m, _) in enumerate(SAMPLES)])
def test_model_round_trips_sample(model, sample) -> None:
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True, exclude_unset=True) == sample


async def test_templates_public_rows_keep_no_public_key() -> None:
    row = {'slug': 's', 'name': 'n', 'description': None, 'downloads': 0, 'created_at': datetime.datetime(2026, 1, 1)}
    bot = MagicMock()
    bot.db.templates.list_public = AsyncMock(return_value=[row])
    bot.db.templates.list_for_guild = AsyncMock(return_value=[{**row, 'public': False}])
    body = (await backup.list_templates(MagicMock(id=1), bot, limit=50)).model_dump(mode='json', exclude_unset=True)
    assert body['public'][0] == {**row, 'created_at': '2026-01-01T00:00:00'}
    assert body['own'][0]['public'] is False


async def test_events_wire_shape() -> None:
    assert (await subscriptions.list_events()).model_dump(mode='json')['events'] == sorted(
        subscriptions.WEBHOOK_EVENTS)
