"""Users-router response models keep their pre-typing wire shape.

FastAPI drops keys a response model does not declare, so each realistic sample must survive
``validate -> dump`` unchanged (see dashboard/crates/percy-client/GENERATION.md).
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.internal_api.routers import users as api

SAMPLES = {
    api.UserGuild: {
        'id': '1', 'name': 'G', 'icon_url': None, 'member_count': 5, 'owner': True, 'manageable': False,
        'music_playing': True,
    },
    api.UserAvatar: {'avatar_url': 'https://x/a.png', 'username': 'bob'},
    api.UserSettings: {'timezone': None, 'track_presence': True, 'track_history': False},
    api.UserHistory: {
        'usernames': [{'name': 'a', 'changed_at': '2026-01-01T00:00:00'}],
        'nicknames': [{'name': 'b', 'changed_at': None}],
        'avatars': [{'image': 'AAA=', 'changed_at': '2026-01-01T00:00:00'}],
        'presence': [{'status': 'online', 'status_before': None, 'changed_at': None}],
    },
    api.RankCard: {
        'style': {'accent': 'rose'}, 'guild_default': {}, 'locked': False,
        'effective': {'theme': 'ink'}, 'options': {'theme': [{'value': 'ink', 'label': 'Ink'}]},
    },
    api.RemindersResponse: {
        'reminders': [{'id': 1, 'expires': '2026-01-01T00:00:00Z', 'message': 'm', 'repeats': None, 'shared_with': 0}]
    },
    api.PlaylistSummary: {'id': '1', 'name': 'P', 'track_count': 2, 'thumbnail': None, 'cover': 'https://x/c'},
    api.PlaylistDetail: {
        'id': '1', 'name': 'P', 'thumbnail': None, 'cover': None,
        'tracks': [{'name': 'n', 'url': 'u', 'author': None, 'artwork': None}],
    },
}


@pytest.mark.parametrize('model', list(SAMPLES), ids=lambda m: m.__name__)
def test_model_roundtrips(model) -> None:
    sample = SAMPLES[model]
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample


async def test_history_handler_shape() -> None:
    when = datetime.datetime(2026, 1, 1)
    bot = MagicMock()
    bot.db.stats.get_item_history = AsyncMock(return_value=[{'item_value': 'a', 'changed_at': when}])
    bot.db.stats.get_avatar_history = AsyncMock(return_value=[{'avatar': b'abc', 'changed_at': None}])
    bot.db.stats.get_presence_history = AsyncMock(
        return_value=[{'status': 'idle', 'status_before': 'online', 'changed_at': when}]
    )
    body = (await api.get_user_history(1, bot, avatar_limit=24)).model_dump(mode='json')
    assert body['usernames'] == [{'name': 'a', 'changed_at': '2026-01-01T00:00:00'}]
    assert body['avatars'] == [{'image': 'YWJj', 'changed_at': None}]
    assert body['presence'][0] == {'status': 'idle', 'status_before': 'online', 'changed_at': '2026-01-01T00:00:00'}


async def test_reminders_and_settings_handler_shape() -> None:
    bot = MagicMock()
    when = datetime.datetime(2026, 1, 1)
    bot.db.timers.get_user_reminders = AsyncMock(return_value=[(3, when, None, 'every day', 2)])
    assert (await api.get_user_reminders(1, bot)).model_dump(mode='json') == {
        'reminders': [{'id': 3, 'expires': '2026-01-01T00:00:00Z', 'message': '', 'repeats': 'every day', 'shared_with': 2}]
    }
    bot.db.get_user_config = AsyncMock(return_value=MagicMock(timezone='UTC', track_presence=True, track_history=True))
    assert (await api.get_user_settings(1, bot)).model_dump(mode='json') == {
        'timezone': 'UTC', 'track_presence': True, 'track_history': True,
    }
