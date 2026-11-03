from __future__ import annotations

import pytest

from app.services.moderation.settings import (
    DISABLED_MESSAGE_LIMIT,
    REASON_REQUIRED_ACTIONS,
    action_notice,
    check_disabled_message,
    reason_missing,
    render_disabled_message,
    sticky_to_restore,
)


@pytest.mark.parametrize(
    ('action', 'reason', 'required', 'missing'),
    [
        ('kick', None, True, True),
        ('ban', '  ', True, True),
        ('ban', 'spam', True, False),
        ('kick', None, False, False),
        ('unban', None, True, False),  # not a punishment
        ('timeout', None, True, False),  # not in the brief's list
    ],
)
def test_reason_missing(action: str, reason: str | None, required: bool, missing: bool) -> None:
    assert reason_missing(action, reason, required) is missing


def test_reason_required_actions_are_the_brief() -> None:
    assert {'kick', 'ban', 'softban', 'tempban', 'warn', 'mute', 'tempmute'} == REASON_REQUIRED_ACTIONS


def test_render_disabled_message_only_fills_the_closed_set() -> None:
    out = render_disabled_message('{command} is off in {channel} {user._state} {{x}}', 'ping', '<#1>')
    assert out == 'ping is off in <#1> {user._state} {x}'


def test_check_disabled_message() -> None:
    assert check_disabled_message('  hi ') == 'hi'
    assert check_disabled_message('   ') is None
    assert check_disabled_message(None) is None
    assert check_disabled_message('x' * DISABLED_MESSAGE_LIMIT) is not None
    with pytest.raises(ValueError):
        check_disabled_message('x' * (DISABLED_MESSAGE_LIMIT + 1))


def test_action_notice_with_and_without_reason() -> None:
    assert action_notice('Srv', 'ban', 'spam') == 'You were banned from **Srv**. Reason: spam'
    assert action_notice('Srv', 'kick', None) == 'You were kicked from **Srv**.'
    assert action_notice('Srv', 'warn', ' ') == 'You were warned in **Srv**.'


def test_sticky_to_restore_skips_held_and_unassignable_roles() -> None:
    # 1 is held, 2 is missing and assignable, 3 was deleted or sits above Percy
    assert sticky_to_restore({1, 2, 3}, {1}, {1, 2}) == [2]
    assert sticky_to_restore(set(), {1}, {1}) == []
    assert sticky_to_restore({5}, set(), {5}) == [5]
