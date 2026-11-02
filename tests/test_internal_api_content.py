"""Content endpoints keep their pre-typing wire shape now that they return pydantic models.

FastAPI drops keys a response model does not declare, so each sample below is the full JSON the
handler emitted before typing; a missing key in the model fails the round-trip.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

from app.internal_api.routers import content
from app.internal_api.routers import content_models as m

NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)

SAMPLES = {
    m.PollsResponse: {
        'polls': [{
            'id': 1, 'channel_id': '2', 'message_id': '3', 'question': 'Q?', 'description': '', 'options': ['a', 'b'],
            'image_url': '', 'color': '', 'published': NOW.isoformat(), 'expires': None, 'ended': False,
            'total_votes': 4, 'hide_results': False, 'role_id': None,
        }],
        'total': 1,
    },
    m.GiveawaysResponse: {
        'giveaways': [{
            'id': 1, 'channel_id': '2', 'message_id': '3', 'author_id': '4', 'title': 'Prize', 'description': 'N/A',
            'winners_count': 1, 'entries': 5, 'ended': True, 'ends_at': NOW.isoformat(), 'required_role_id': '9',
            'min_account_days': 0, 'min_join_days': 7,
        }],
        'total': 1,
    },
    m.TagsResponse: {
        'total': 1, 'total_uses': 10,
        'tags': [{'id': 1, 'name': 't', 'owner_id': '2', 'owner_name': None, 'uses': 3, 'created_at': NOW.isoformat()}],
        'top_creators': [{'user_id': '2', 'username': 'bob', 'count': 1}],
    },
    m.TagDetail: {
        'id': 1, 'name': 't', 'content': 'c', 'owner_id': None, 'owner_name': None, 'uses': 0, 'created_at': None,
    },
    m.ComicsResponse: {
        'feeds': [{
            'id': 1, 'brand': 'MARVEL', 'channel_id': '2', 'format': 'Summary', 'day': 3, 'ping': None, 'pin': False,
            'next_pull': NOW.isoformat(),
        }],
    },
    m.TempChannelsResponse: {
        'entries': [{
            'channel_id': '1', 'channel_name': 'hub', 'format': '{user}',
            'active_channels': [{'channel_id': '2', 'channel_name': 'x', 'user_count': 2}], 'total_users': 2,
        }],
    },
    m.LockdownsResponse: {'entries': [{'channel_id': '1', 'channel_name': 'general'}]},
    m.EmojiStatsResponse: {
        'total_uses': 9, 'distinct_emojis': 1,
        'entries': [{'emoji_id': '1', 'emoji_name': 'pog', 'emoji_url': None, 'total': 9}], 'total': 1,
    },
}


def test_models_round_trip_without_dropping_keys() -> None:
    for model, sample in SAMPLES.items():
        assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample, model.__name__


async def test_emoji_stats_handler_shape() -> None:
    bot = MagicMock()
    bot.db.emoji_stats.get_guild_summary = AsyncMock(return_value={'Count': 9, 'Emoji': 1})
    bot.db.emoji_stats.get_top_guild_emojis = AsyncMock(return_value=[{'emoji_id': 1, 'total': 9}])
    bot.get_emoji = MagicMock(return_value=MagicMock(url='https://x/e.png'))
    bot.get_emoji.return_value.name = 'pog'
    body = await content.get_emoji_stats(MagicMock(id=1), bot, limit=50, offset=0)
    expected = {**SAMPLES[m.EmojiStatsResponse]}
    expected['entries'] = [{**expected['entries'][0], 'emoji_url': 'https://x/e.png'}]
    assert body.model_dump(mode='json') == expected


async def test_polls_handler_shape() -> None:
    bot = MagicMock()
    bot.db.polls.get_for_guild = AsyncMock(return_value=[{
        'id': 1, 'channel_id': 2, 'message_id': 3, 'published': NOW, 'expires': None,
        'metadata': {'kwargs': {'content': 'Q?', 'options': [{'content': 'a'}, {'content': 'b'}], 'running': True, 'votes': 4}},
    }])
    body = (await content.get_polls(MagicMock(id=1), bot, limit=50, offset=0)).model_dump(mode='json')
    assert body == SAMPLES[m.PollsResponse]
