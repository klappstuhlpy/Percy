"""``public_command_payload``: the extra fields the website's commands page reads."""
from __future__ import annotations

import discord

from app.core import PermissionTemplate, command, cooldown, group
from app.internal_api.routers.stats import public_command_payload


@group('thing', aliases=['th'], hybrid=True, description='A group.', extras={'example': 'thing'})
async def _thing(ctx) -> None:
    """Long help text."""


@_thing.command('add', description='Add one.', user_permissions=PermissionTemplate.admin)
@cooldown(2, 30.0)
async def _add(ctx, name: str) -> None:
    """Add a thing."""


@command('plain', examples=['plain 1'], bot_permissions=[discord.Permissions.manage_roles])
async def _plain(ctx) -> None:
    pass


def test_group_and_subcommand_payload() -> None:
    top = public_command_payload(_thing, 'Test')
    assert top['aliases'] == ['th']
    assert top['slash'] is True
    assert top['example'] == 'thing'
    assert top['parent'] is None
    assert top['help'] == 'Long help text.'
    assert top['cooldown'] is None

    sub = public_command_payload(_add, 'Test')
    assert sub['name'] == 'thing add'
    assert sub['parent'] == 'thing'
    assert sub['cooldown'] == {'rate': 2, 'per': 30.0}
    assert 'administrator' in sub['user_permissions']


def test_prefix_only_falls_back_to_examples() -> None:
    p = public_command_payload(_plain, 'Test')
    assert p['slash'] is False
    assert p['example'] == 'plain 1'
    assert 'manage_roles' in p['bot_permissions']
