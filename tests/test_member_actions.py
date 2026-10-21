from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action


async def test_timeout_without_duration_is_a_400() -> None:
    member = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=False))
    guild = MagicMock()
    guild.get_member.return_value = member
    moderator = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    guild.owner_id = 1
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(MagicMock(), guild, moderator, 2, action='timeout', duration=None)  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_unknown_member_is_a_404() -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(MagicMock(), guild, MagicMock(), 2, action='kick')
    assert exc.value.status == 404
