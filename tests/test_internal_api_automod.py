"""AutoMod preset endpoints: validation, and that writes go through the cog's shared ``save_preset``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cogs.automation import automod
from app.internal_api.routers import automod as api


def _guild(*, manage: bool = True) -> MagicMock:
    guild = MagicMock()
    guild.id = 1
    guild.me.guild_permissions.manage_guild = manage
    guild.fetch_automod_rules = AsyncMock(return_value=[])
    guild.get_role = lambda i: MagicMock(id=i) if i < 1000 else None
    guild.get_channel = lambda i: MagicMock(id=i) if i < 1000 else None
    return guild


@pytest.fixture
def save(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=42)
    monkeypatch.setattr(automod, 'save_preset', mock)
    return mock


async def _put(guild: MagicMock, preset: str, **body: object) -> dict:
    return await api.put_automod_preset(guild, MagicMock(), preset, api.PutPresetBody(**body))  # type: ignore[arg-type]


async def test_unknown_preset_is_refused(save: AsyncMock) -> None:
    with pytest.raises(HTTPException) as exc:
        await _put(_guild(), 'nope', enabled=True)
    assert exc.value.status_code == 400
    save.assert_not_awaited()


async def test_missing_manage_server_is_refused(save: AsyncMock) -> None:
    with pytest.raises(HTTPException) as exc:
        await _put(_guild(manage=False), 'links', enabled=True)
    assert exc.value.detail == api.MISSING_PERMISSION
    save.assert_not_awaited()


async def test_keywords_on_a_regex_preset_are_refused(save: AsyncMock) -> None:
    with pytest.raises(HTTPException) as exc:
        await _put(_guild(), 'links', enabled=True, keywords=['bad'])
    assert 'keyword' in exc.value.detail
    save.assert_not_awaited()


async def test_overlong_keyword_is_refused(save: AsyncMock) -> None:
    with pytest.raises(HTTPException):
        await _put(_guild(), 'bad_words', enabled=True, keywords=['x' * 61])
    save.assert_not_awaited()


async def test_unknown_role_is_refused(save: AsyncMock) -> None:
    with pytest.raises(HTTPException) as exc:
        await _put(_guild(), 'links', enabled=True, exempt_role_ids=[5000])
    assert '5000' in exc.value.detail
    save.assert_not_awaited()


async def test_save_path_gets_the_edited_preset(save: AsyncMock) -> None:
    guild = _guild()
    await _put(guild, 'bad_words', enabled=True, exempt_role_ids=[7, 7], exempt_channel_ids=[9],
               keywords=[' foo ', '', 'bar', 'foo'])
    save.assert_awaited_once()
    _bot, guild_id, preset = save.await_args.args
    assert guild_id == 1
    assert preset['name'] == 'Percy Rule Bad Words'
    assert preset['enabled'] is True
    assert preset['id'] == 0
    assert [r.id for r in preset['exempt_roles']] == [7]
    assert [c.id for c in preset['exempt_channels']] == [9]
    assert preset['trigger'].keyword_filter == ['foo', 'bar']
    # The shared class-level default must not have been touched.
    assert automod.AutoModPresets.bad_words['trigger'].keyword_filter == []


async def test_get_without_permission_returns_defaults() -> None:
    guild = _guild(manage=False)
    out = await api.get_automod(guild, MagicMock())
    assert out['missing_permission'] is True
    assert [p['key'] for p in out['presets']] == list(automod.AutoModPresets.KEYS)
    assert all(not p['exists'] for p in out['presets'])
    guild.fetch_automod_rules.assert_not_awaited()


async def test_save_preset_creates_and_links_the_rule() -> None:
    bot = MagicMock()
    bot.http.create_auto_moderation_rule = AsyncMock(return_value={'id': '55'})
    config = MagicMock()
    config.linked_automod_rules = set()
    config.update = AsyncMock()
    bot.db.get_guild_config = AsyncMock(return_value=config)

    preset = automod.AutoModPresets.fresh('links')
    assert preset is not None
    rule_id = await automod.save_preset(bot, 1, preset, actor='tester')

    assert rule_id == 55
    kwargs = bot.http.create_auto_moderation_rule.await_args.kwargs
    assert kwargs['exempt_roles'] == [] and kwargs['exempt_channels'] == []
    assert kwargs['name'] == 'Percy Rule Links'
    config.update.assert_awaited_once_with(linked_automod_rules=['Percy Rule Links'])
