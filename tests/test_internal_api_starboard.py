"""Starboard endpoint: refuses to turn on without a channel, writes through the cog's cache."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cogs.starboard.models import StarboardConfig
from app.internal_api.routers import starboard as api


def _config(**overrides: object) -> StarboardConfig:
    base = {'guild_id': 1, 'channel_id': None, 'threshold': 3, 'emoji': '⭐', 'self_star': False, 'enabled': False,
                'ignored_channel_ids': frozenset(), 'max_age_hours': 0, 'allow_nsfw': False}
    return StarboardConfig(**{**base, **overrides})  # type: ignore[arg-type]


def _bot(current: StarboardConfig | None) -> tuple[MagicMock, MagicMock]:
    cog = MagicMock()
    cog.get_config = AsyncMock(return_value=current)
    cog.update_config = AsyncMock(return_value=_config(enabled=True, channel_id=5))
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=cog)
    return bot, cog


async def test_enabling_without_a_channel_is_refused() -> None:
    bot, cog = _bot(_config())
    with pytest.raises(HTTPException) as exc:
        await api.patch_starboard(MagicMock(), bot, api.PatchStarboardBody(enabled=True))
    assert 'channel' in exc.value.detail
    cog.update_config.assert_not_awaited()


async def test_ignored_channels_are_deduplicated_and_written_through_the_cog() -> None:
    bot, cog = _bot(_config(channel_id=5))
    guild = MagicMock()
    guild.id = 1
    await api.patch_starboard(guild, bot, api.PatchStarboardBody(ignored_channel_ids=[9, 7, 9]))
    cog.update_config.assert_awaited_once_with(1, ignored_channel_ids=[7, 9])
