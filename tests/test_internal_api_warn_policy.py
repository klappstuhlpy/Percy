"""Warn-policy and pardon endpoints: shared validation, contract shape, 404s."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cogs.modlog.warns import threshold_error
from app.internal_api.routers import moderation as api


def _bot(thresholds: list[dict] | None = None, days: int | None = None) -> MagicMock:
    bot = MagicMock()
    cases = bot.db.cases
    cases.get_warn_thresholds = AsyncMock(return_value=thresholds or [])
    cases.get_warn_expiry_days = AsyncMock(return_value=days)
    cases.set_warn_threshold = AsyncMock()
    cases.set_warn_expiry_days = AsyncMock()
    cases.remove_warn_threshold = AsyncMock(return_value=True)
    bot.get_cog = MagicMock(return_value=None)
    return bot


def _guild() -> MagicMock:
    guild = MagicMock()
    guild.id = 1
    return guild


@pytest.mark.parametrize(
    ('count', 'action', 'seconds', 'ok'),
    [
        (3, 'timeout', 3600, True),
        (3, 'timeout', None, False),  # timeout needs a duration
        (3, 'mute', None, True),  # permanent mute
        (3, 'kick', 60, False),  # kick takes none
        (3, 'ban', None, True),
        (0, 'ban', None, False),
        (3, 'warn', None, False),
    ],
)
def test_threshold_error(count: int, action: str, seconds: int | None, ok: bool) -> None:
    assert (threshold_error(count, action, seconds) is None) is ok


async def test_get_payload_shape() -> None:
    bot = _bot([{'warn_count': 3, 'action': 'kick', 'duration_seconds': None}], days=30)
    out = await api.get_warn_policy(bot, _guild())
    assert out == {
        'expiry_days': 30,
        'thresholds': [{'warn_count': 3, 'action': 'kick', 'duration_seconds': None}],
        'actions': ['timeout', 'mute', 'kick', 'ban'],
    }


async def test_patch_zero_means_never_and_bounds_are_enforced() -> None:
    bot = _bot()
    await api.patch_warn_policy(bot, _guild(), api.WarnExpiryBody(expiry_days=0))
    bot.db.cases.set_warn_expiry_days.assert_awaited_once_with(1, None)
    with pytest.raises(HTTPException) as exc:
        await api.patch_warn_policy(bot, _guild(), api.WarnExpiryBody(expiry_days=4000))
    assert exc.value.status_code == 400


async def test_put_threshold_validates_then_writes() -> None:
    bot = _bot()
    with pytest.raises(HTTPException) as exc:
        await api.put_warn_threshold(3, bot, _guild(), api.WarnThresholdBody(action='timeout'))
    assert exc.value.status_code == 400
    bot.db.cases.set_warn_threshold.assert_not_awaited()
    await api.put_warn_threshold(3, bot, _guild(), api.WarnThresholdBody(action='Timeout', duration_seconds=3600))
    bot.db.cases.set_warn_threshold.assert_awaited_once_with(1, 3, 'timeout', 3600)


async def test_delete_threshold_404_when_missing() -> None:
    bot = _bot()
    bot.db.cases.remove_warn_threshold = AsyncMock(return_value=False)
    with pytest.raises(HTTPException) as exc:
        await api.delete_warn_threshold(3, bot, _guild())
    assert exc.value.status_code == 404


async def test_pardon_404_when_not_an_active_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    moderator = MagicMock(id=9)
    monkeypatch.setattr(api, 'resolve_moderator', AsyncMock(return_value=moderator))
    bot = _bot()
    bot.db.cases.pardon_case = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.pardon_case(5, bot, _guild(), api.PardonBody(moderator_id=9))
    assert exc.value.status_code == 404
    bot.db.cases.pardon_case.assert_awaited_once_with(1, 5, 9)


async def test_pardon_returns_the_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, 'resolve_moderator', AsyncMock(return_value=MagicMock(id=9)))
    bot = _bot()
    bot.db.cases.pardon_case = AsyncMock(return_value={
        'id': 1, 'guild_id': 1, 'case_index': 5, 'action': 'warn', 'target_id': 2, 'moderator_id': 9,
        'reason': 'x', 'log_message_id': None, 'created_at': datetime.datetime(2026, 1, 1),
        'expires_at': None, 'pardoned_at': datetime.datetime(2026, 1, 2),
    })
    guild = _guild()
    guild.get_member.return_value = None
    bot.get_user.return_value = None
    out = await api.pardon_case(5, bot, guild, api.PardonBody(moderator_id=9))
    assert out['case_index'] == 5
    assert out['action'] == 'warn'
