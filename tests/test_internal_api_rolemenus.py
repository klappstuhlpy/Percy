"""Role-menu endpoints route through the cog's operations and turn its refusals into 400s."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cogs.rolemenu.cog import RoleMenuError
from app.internal_api.routers import rolemenus as api


def _bot(cog: MagicMock) -> MagicMock:
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=cog)
    return bot


async def test_create_needs_a_text_channel() -> None:
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.create_role_menu(guild, _bot(MagicMock()), api.CreateRoleMenuBody(channel_id=1, title='Roles'))
    assert exc.value.status_code == 400


async def test_cog_refusal_becomes_a_400_with_its_message() -> None:
    cog = MagicMock()
    cog.add_role = AsyncMock(side_effect=RoleMenuError('A role menu can hold at most 25 roles.'))
    guild = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await api.add_role_menu_role(guild, _bot(cog), 3, api.AddRoleMenuRoleBody(role_id=9))
    assert exc.value.status_code == 400
    assert exc.value.detail == 'A role menu can hold at most 25 roles.'


async def test_edit_passes_only_what_changed() -> None:
    cog = MagicMock()
    cog.edit_menu = AsyncMock()
    guild = MagicMock()
    await api.patch_role_menu(guild, _bot(cog), 3, api.PatchRoleMenuBody(unique=True))
    cog.edit_menu.assert_awaited_once_with(guild, 3, title=None, description=None, unique=True)


async def test_add_role_refuses_a_role_above_the_dashboard_user(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = MagicMock(id=5)
    actor.top_role = 10
    role = MagicMock()
    role.__ge__ = lambda self, other: True  # role >= actor.top_role
    guild = MagicMock(owner_id=1)
    guild.get_role = MagicMock(return_value=role)
    monkeypatch.setattr(api, 'resolve_moderator', AsyncMock(return_value=actor))
    cog = MagicMock()
    cog.add_role = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.add_role_menu_role(guild, _bot(cog), 3, api.AddRoleMenuRoleBody(role_id=9, user_id='5'))
    assert exc.value.status_code == 403
    cog.add_role.assert_not_awaited()
