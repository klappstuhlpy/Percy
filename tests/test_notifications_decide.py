from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord

from app.cogs.moderation.member_actions import MemberActionError
from app.cogs.notifications import decide

if TYPE_CHECKING:
    import pytest

REQUESTER, MOD, TARGET = 10, 20, 30


class FakeRepo:
    def __init__(self, action: str = 'ban') -> None:
        self.row = {
            'id': 1, 'guild_id': 5, 'requester_id': REQUESTER, 'target_id': TARGET, 'action': action,
            'reason': 'spam', 'duration': None, 'status': 'pending', 'decided_by': None, 'decision_note': None,
            'result': None, 'channel_id': None, 'message_id': None,
        }  # fmt: skip
        self.notifications: list[dict] = []

    async def get_request(self, guild_id: int, request_id: int) -> dict | None:
        return dict(self.row) if request_id == self.row['id'] else None

    async def claim_request(self, guild_id: int, request_id: int, *, status: str, decided_by: int, note: str | None) -> dict | None:
        if self.row['status'] != 'pending':
            return None
        self.row.update(status=status, decided_by=decided_by, decision_note=note)
        return dict(self.row)

    async def finish_request(self, request_id: int, *, status: str, result: str | None) -> None:
        self.row.update(status=status, result=result)

    async def add(self, guild_id: int | None, **fields: object) -> None:
        self.notifications.append(fields)


def _setup(monkeypatch: pytest.MonkeyPatch, action: str = "ban", *, execute: AsyncMock | None = None) -> tuple:
    repo = FakeRepo(action)
    bot = SimpleNamespace(db=SimpleNamespace(notifications=repo), fetch_user=AsyncMock())
    guild = MagicMock(id=5)
    guild.get_member.return_value = None
    executor = execute or AsyncMock(return_value={})
    monkeypatch.setattr(decide, 'apply_member_action', executor)
    return bot, guild, repo, executor


def _mod(user_id: int = MOD, **perms: bool) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, guild_permissions=discord.Permissions(**perms))


async def test_self_decision_is_400(monkeypatch) -> None:
    bot, guild, repo, executor = _setup(monkeypatch)
    out = await decide.decide_request(bot, guild, 1, _mod(REQUESTER, administrator=True), approve=True)  # type: ignore[arg-type]
    assert out.http_status == 400
    assert repo.row['status'] == 'pending'
    executor.assert_not_called()


async def test_missing_permission_is_403_and_status_unchanged(monkeypatch) -> None:
    bot, guild, repo, executor = _setup(monkeypatch)
    out = await decide.decide_request(bot, guild, 1, _mod(kick_members=True), approve=True)  # type: ignore[arg-type]
    assert out.http_status == 403
    assert 'Ban Members' in out.message
    assert repo.row['status'] == 'pending'
    executor.assert_not_called()


async def test_unknown_is_404(monkeypatch) -> None:
    bot, guild, _, _ = _setup(monkeypatch)
    out = await decide.decide_request(bot, guild, 99, _mod(administrator=True), approve=True)  # type: ignore[arg-type]
    assert out.http_status == 404


async def test_second_approve_is_409_and_executes_once(monkeypatch) -> None:
    bot, guild, _, executor = _setup(monkeypatch)
    mod = _mod(ban_members=True)
    first = await decide.decide_request(bot, guild, 1, mod, approve=True)  # type: ignore[arg-type]
    second = await decide.decide_request(bot, guild, 1, mod, approve=True)  # type: ignore[arg-type]
    assert (first.ok, first.status, first.http_status) == (True, 'approved', 200)
    assert second.http_status == 409
    assert second.message == 'This request was already approved.'
    executor.assert_awaited_once()


async def test_executor_error_marks_failed(monkeypatch) -> None:
    bot, guild, repo, _ = _setup(monkeypatch, execute=AsyncMock(side_effect=MemberActionError(403, 'Too high')))
    out = await decide.decide_request(bot, guild, 1, _mod(ban_members=True), approve=True)  # type: ignore[arg-type]
    assert (out.ok, out.status) == (False, 'failed')
    assert repo.row['status'] == 'failed'
    assert repo.row['result'] == 'Too high'
    assert repo.notifications[0]['kind'] == 'request_decided'


async def test_deny_does_not_execute(monkeypatch) -> None:
    bot, guild, repo, executor = _setup(monkeypatch)
    out = await decide.decide_request(bot, guild, 1, _mod(ban_members=True), approve=False, note='no')  # type: ignore[arg-type]
    assert out.status == 'denied'
    assert repo.row['decision_note'] == 'no'
    executor.assert_not_called()


async def test_notify_never_raises() -> None:
    bot = SimpleNamespace(db=SimpleNamespace(notifications=SimpleNamespace(add=AsyncMock(side_effect=RuntimeError))))
    await decide.notify(bot, 1, kind='notice', title='x')  # type: ignore[arg-type]



async def test_unexpected_executor_error_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, guild, repo, _ = _setup(monkeypatch, execute=AsyncMock(side_effect=RuntimeError('boom')))
    out = await decide.decide_request(bot, guild, 1, _mod(ban_members=True), approve=True)  # type: ignore[arg-type]
    assert (out.ok, out.status) == (False, 'failed')
    assert repo.row['status'] == 'failed'
    assert repo.row['result'] == 'unexpected error'
