"""Notification/request endpoints: handlers called directly with a fake bot/guild/repo."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import config
from app.cogs.notifications.decide import RequestDecision
from app.internal_api.dependencies import verify_token
from app.internal_api.routers import notifications as api


@pytest.fixture
def env() -> tuple[MagicMock, MagicMock, MagicMock]:
    guild = MagicMock()
    guild.id = 1
    guild.get_member = MagicMock(side_effect=lambda i: MagicMock(display_name=f'user{i}') if i in (10, 11) else None)
    guild.get_role = MagicMock(side_effect=lambda i: None if i == 99 else MagicMock())
    guild.get_channel = MagicMock(return_value=None)
    repo = MagicMock()
    repo.unread_count = AsyncMock(return_value=3)
    repo.pending_count = AsyncMock(return_value=2)
    repo.update_request_settings = AsyncMock()
    bot = MagicMock()
    bot.db.notifications = repo
    return bot, guild, repo


async def test_summary(env: tuple[MagicMock, MagicMock, MagicMock]) -> None:
    bot, guild, _ = env
    assert await api.notifications_summary(guild, bot, 10) == {'unread': 3, 'pending_requests': 2}


async def test_missing_bearer_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, 'internal_api_token', 'secret')
    with pytest.raises(HTTPException) as exc:
        await verify_token(None)
    assert exc.value.status_code == 401
    assert any(d.dependency is verify_token for d in api.router.dependencies)


async def test_decide_maps_http_status(env: tuple[MagicMock, MagicMock, MagicMock], monkeypatch: pytest.MonkeyPatch) -> None:
    bot, guild, _ = env
    monkeypatch.setattr(
        api, 'decide_request', AsyncMock(return_value=RequestDecision(False, 'approved', 'already', None, 409))
    )
    resp = await api.decide(guild, bot, 5, api.DecideBody(moderator_id=10, decision='approve'))
    assert resp.status_code == 409
    assert json.loads(resp.body) == {'ok': False, 'status': 'approved', 'message': 'already', 'request': None}


async def test_decide_unknown_moderator_is_404(env: tuple[MagicMock, MagicMock, MagicMock]) -> None:
    bot, guild, _ = env
    with pytest.raises(HTTPException) as exc:
        await api.decide(guild, bot, 5, api.DecideBody(moderator_id=77, decision='deny'))
    assert exc.value.status_code == 404


async def test_patch_settings_unknown_role_is_400(env: tuple[MagicMock, MagicMock, MagicMock]) -> None:
    bot, guild, repo = env
    with pytest.raises(HTTPException) as exc:
        await api.patch_request_settings(guild, bot, api.RequestSettingsBody(role_ids=[99]))
    assert exc.value.status_code == 400
    repo.update_request_settings.assert_not_awaited()
