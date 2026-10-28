from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.cogs.economy.cog import Economy
from app.cogs.leveling.models import GainConfig, LevelConfig, LevelingSpec

SPEC = LevelingSpec(1, 100, 1.5, GainConfig(1, 2))


def test_split_total_xp_roundtrips() -> None:
    for total in (0, 5, 100, 990, 10_000, 123_456):
        level, xp = SPEC.split_total_xp(total)
        assert xp >= 0 and SPEC.get_total_xp(level, xp) == total


async def test_add_xp_levels_from_returned_row_and_persists_delta() -> None:
    leveling = SimpleNamespace(
        add_xp=AsyncMock(return_value={'xp': 500, 'level': 0, 'messages': 3}), shift_level=AsyncMock()
    )
    class Fake:
        user_id, guild_id, xp, level, messages = 1, 2, 0, 0, 0
        bot = SimpleNamespace(db=SimpleNamespace(leveling=leveling))
        send_level_up_message = AsyncMock()
        update_roles = AsyncMock()
        _bump = LevelConfig._bump
        _carry = LevelConfig._carry
        max_xp = LevelConfig.max_xp
        config = SimpleNamespace(spec=SPEC)

    fake = Fake()
    await LevelConfig.add_xp(fake, 5, message=None)  # type: ignore[arg-type,misc]
    leveling.add_xp.assert_awaited_once_with(1, 2, 5, 1)
    (_, _, old_level, new_level, delta), _kw = leveling.shift_level.call_args
    assert old_level == 0 and new_level == fake.level > 0 and delta == fake.xp - 500


async def test_stale_lottery_timer_is_ignored() -> None:
    ends = datetime.datetime(2030, 1, 1)
    economy = SimpleNamespace(get_lottery=AsyncMock(return_value={'ends_at': ends}), get_lottery_entries=AsyncMock())
    fake = SimpleNamespace(bot=SimpleNamespace(db=SimpleNamespace(economy=economy)))
    stale = SimpleNamespace(args=[1, datetime.datetime(2029, 1, 1).isoformat()])
    await Economy.on_lottery_timer_complete(fake, stale)  # type: ignore[arg-type]
    economy.get_lottery_entries.assert_not_called()
