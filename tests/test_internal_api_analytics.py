"""Analytics endpoints keep their pre-typing wire shape now that they return pydantic models.

The models are the source of truth for percy-client's generated types (see
dashboard/crates/percy-client/GENERATION.md), so these keys are exactly what the dashboard
deserialises. Handlers are called directly, like the other internal-API tests.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

from app.internal_api.routers import analytics


def _env() -> tuple[MagicMock, MagicMock]:
    joined = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    guild = MagicMock(id=1, member_count=500, members=[MagicMock(joined_at=joined)])
    stats = MagicMock()
    stats.get_command_series = AsyncMock(return_value=[])
    stats.get_command_total = AsyncMock(side_effect=[10, 14])  # current window, then both windows
    stats.get_command_usage = AsyncMock(return_value=[{'command': 'ping', 'uses': 7}])
    bot = MagicMock()
    bot.db.stats = stats
    return guild, bot


async def test_series_wire_shape() -> None:
    guild, bot = _env()
    body = (await analytics.get_series(guild, bot, metric='commands', range_='24h', granularity=None)).model_dump(mode='json')
    assert set(body) == {'metric', 'range', 'granularity', 'total', 'points'}
    assert (body['metric'], body['range'], body['granularity'], body['total']) == ('commands', '24h', 'hour', 0)
    assert body['points'] and all(set(p) == {'bucket', 'value'} and p['value'] == 0 for p in body['points'])


async def test_summary_wire_shape() -> None:
    guild, bot = _env()
    body = (await analytics.get_summary(guild, bot, range_='7d')).model_dump(mode='json')
    assert body == {
        'range': '7d',
        'commands': {'value': 10, 'delta': 6},
        'new_members': {'value': 1, 'delta': 1},
        'member_count': 500,
        'top_commands': [{'command': 'ping', 'uses': 7}],
    }
