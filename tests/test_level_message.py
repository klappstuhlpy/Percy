from __future__ import annotations

from app.services.level_message import render_level_up

VALUES = {'user': 'klapp', 'user.mention': '<@1>', 'level': '7', 'server': 'Percy HQ'}


def test_known_placeholders_are_replaced() -> None:
    assert render_level_up('GG {user.mention}, level {level} in {server}!', VALUES) == 'GG <@1>, level 7 in Percy HQ!'


def test_attribute_paths_never_resolve() -> None:
    # str.format(user=member) would have walked into the client and printed the bot token.
    template = '{user._state.http.token} {user.__class__} {level.real}'
    assert render_level_up(template, VALUES) == template


def test_escaped_braces_and_unknown_names_stay_as_typed() -> None:
    assert render_level_up('{{level}} {nope} {', VALUES) == '{level} {nope} {'
