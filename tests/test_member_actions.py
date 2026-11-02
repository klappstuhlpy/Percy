from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cogs.moderation.member_actions import MemberActionError, apply_member_action


def _bot(**config: object) -> MagicMock:
    bot = MagicMock()
    defaults = {'mod_require_reason': False, 'mod_default_ban_delete_days': 0, 'mod_dm_on_action': False}
    bot.db.get_guild_config = AsyncMock(return_value=SimpleNamespace(**(defaults | config)))
    return bot


async def test_timeout_without_duration_is_a_400() -> None:
    member = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(administrator=False))
    guild = MagicMock()
    guild.get_member.return_value = member
    moderator = SimpleNamespace(id=1, top_role=10, guild_permissions=SimpleNamespace(administrator=True))
    guild.owner_id = 1
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(_bot(), guild, moderator, 2, action='timeout', duration=None)  # type: ignore[arg-type]
    assert exc.value.status == 400


async def test_unknown_member_is_a_404() -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    with pytest.raises(MemberActionError) as exc:
        await apply_member_action(_bot(), guild, MagicMock(), 2, action='kick')
    assert exc.value.status == 404


async def test_required_reason_refuses_a_bare_kick_and_a_blank_one() -> None:
    guild = MagicMock()
    guild.get_member.return_value = SimpleNamespace(id=2)
    for reason in (None, '   '):
        with pytest.raises(MemberActionError) as exc:
            await apply_member_action(_bot(mod_require_reason=True), guild, MagicMock(), 2, action='kick', reason=reason)
        assert exc.value.status == 400
        assert 'reason' in exc.value.detail


async def test_required_reason_does_not_touch_other_actions() -> None:
    guild = MagicMock()
    guild.get_member.return_value = None
    with pytest.raises(MemberActionError) as exc:  # reaches the member lookup: the reason gate let it through
        await apply_member_action(_bot(mod_require_reason=True), guild, MagicMock(), 2, action='timeout')
    assert exc.value.status == 404
