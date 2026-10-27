"""The modlog/warn Components V2 cards build without a bot and stay inside Discord's limits."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import discord
import pytest

from app.cogs.modlog.models import CaseType, ModerationCase
from app.cogs.modlog.ui import (
    CaseHistoryView,
    build_case_view,
    build_warn_dm_view,
    build_warn_result_view,
    case_accent,
    ordinal,
)
from app.core.components_v2 import Accent

MAX_COMPONENTS = 40
MAX_TEXT = 4000


class FakeUser:
    def __init__(self, user_id: int, name: str = 'someone') -> None:
        self.id = user_id
        self.name = name
        self.mention = f'<@{user_id}>'
        self.display_avatar = SimpleNamespace(url=f'https://cdn.discordapp.com/embed/avatars/{user_id % 5}.png')

    def __str__(self) -> str:
        return self.name


GUILD = SimpleNamespace(id=1, name='Test *Server*', icon=SimpleNamespace(url='https://cdn.discordapp.com/icons/1/a.png'))


def _case(action: str = 'warn', reason: str | None = 'spam', index: int = 4) -> ModerationCase:
    return ModerationCase(
        id=index, guild_id=1, index=index, action=action, target_id=7, moderator_id=8, reason=reason,
        log_message_id=None, created_at=datetime.datetime(2026, 1, 1),
    )


def _text(view: discord.ui.LayoutView) -> str:
    return '\n'.join(c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


def _assert_limits(view: discord.ui.LayoutView) -> None:
    assert view.total_children_count <= MAX_COMPONENTS
    assert view.content_length() <= MAX_TEXT
    view.to_components()  # serialises without raising


@pytest.mark.parametrize('action', [t.value for t in CaseType] + ['mystery'])
async def test_case_card_for_every_type(action: str) -> None:
    expires = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    view = build_case_view(_case(action), target=FakeUser(7), moderator=FakeUser(8), expires=expires)  # type: ignore[arg-type]
    _assert_limits(view)
    text = _text(view)
    assert 'Case #4' in text and 'Expires' in text and '<t:' in text


async def test_case_card_without_resolved_users_falls_back_to_mentions() -> None:
    view = build_case_view(_case(reason=None))
    _assert_limits(view)
    text = _text(view)
    assert '<@7>' in text and '<@8>' in text and 'No reason provided' in text


async def test_long_reason_is_truncated_inside_the_text_budget() -> None:
    view = build_case_view(_case(reason='x' * 5000), target=FakeUser(7))  # type: ignore[arg-type]
    _assert_limits(view)
    assert '\N{HORIZONTAL ELLIPSIS}' in _text(view)


async def test_multiline_reason_is_fully_quoted() -> None:
    text = _text(build_case_view(_case(reason='line one\nline two')))
    assert '> line one\n> line two' in text


async def test_warn_dm_and_result_cards() -> None:
    dm = build_warn_dm_view(GUILD, 'x' * 5000, case_index=3, warn_count=2)  # type: ignore[arg-type]
    _assert_limits(dm)
    assert '2nd' in _text(dm) and 'Case #3' in _text(dm)
    _assert_limits(build_warn_dm_view(SimpleNamespace(id=1, name='g', icon=None), None))  # type: ignore[arg-type]

    result = build_warn_result_view(FakeUser(7), 3, warn_count=11, dm_delivered=False)  # type: ignore[arg-type]
    _assert_limits(result)
    assert '11th' in _text(result) and 'Could not DM' in _text(result)


async def test_history_pages_stay_within_limits() -> None:
    cases = [_case(reason='r' * 1000, index=i) for i in range(23, 0, -1)]
    view = CaseHistoryView(entries=cases, per_page=5)
    view.extras.update(user=FakeUser(7), total=40)
    for page in range(view.total_pages):
        view._current_page = page
        await view.build()
        _assert_limits(view)
    assert 'newest 23 of 40 cases' in _text(view)


def test_accents_and_ordinals() -> None:
    assert case_accent(CaseType.BAN) is Accent.error
    assert case_accent(CaseType.WARN) is Accent.warning
    assert case_accent(CaseType.UNBAN) is Accent.success
    assert case_accent(None) is Accent.neutral
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)] == [
        '1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '101st', '111th'
    ]
