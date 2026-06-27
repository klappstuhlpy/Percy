"""Tests for the ``?ask`` reply-chain reconstruction (:mod:`app.cogs.automation.assistant`).

The conversational assistant rebuilds an entire multi-turn chat by walking a Discord reply
chain upward. These tests pin that reconstruction (role tagging, invocation stripping,
ordering, and the context budget) with lightweight fakes — no bot or model required.
"""

from __future__ import annotations

import pytest

from app.cogs.automation import assistant as assistant_mod
from app.cogs.automation.assistant import ASSISTANT_MARKER, AssistantMixin


class FakeUser:
    def __init__(self, uid: int, *, bot: bool = False) -> None:
        self.id = uid
        self.bot = bot


class FakeRef:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id
        self.resolved = None  # force the fetch path (our fakes aren't discord.Message)


class FakeChannel:
    def __init__(self, registry: dict[int, FakeMessage]) -> None:
        self._registry = registry

    async def fetch_message(self, mid: int) -> FakeMessage:
        return self._registry[mid]


class FakeMessage:
    def __init__(self, mid: int, content: str, author: FakeUser, channel: FakeChannel, parent_id: int | None) -> None:
        self.id = mid
        self.content = content
        self.author = author
        self.channel = channel
        self.reference = FakeRef(parent_id) if parent_id is not None else None


class FakeCommand:
    def __init__(self, qualified_name: str, *, hidden: bool = False, enabled: bool = True) -> None:
        self.qualified_name = qualified_name
        self.hidden = hidden
        self.enabled = enabled


class FakeBot:
    def __init__(self, commands: dict[str, FakeCommand] | None = None) -> None:
        self.user = FakeUser(999, bot=True)
        self._commands = commands or {}

    async def get_prefix(self, message: FakeMessage) -> list[str]:
        return ['?', '<@999>']

    def get_command(self, name: str) -> FakeCommand | None:
        return self._commands.get(name)


def build_mixin(commands: dict[str, FakeCommand] | None = None) -> AssistantMixin:
    mixin = AssistantMixin()
    mixin.bot = FakeBot(commands)  # type: ignore[assignment]
    return mixin


def lone_message(content: str = '') -> FakeMessage:
    return FakeMessage(1, content, FakeUser(1), FakeChannel({}), None)


def build_chain() -> tuple[AssistantMixin, FakeMessage]:
    """A 5-message thread; returns the mixin and the latest (current) user message."""
    bot_user = FakeUser(999, bot=True)
    human = FakeUser(1)
    registry: dict[int, FakeMessage] = {}
    channel = FakeChannel(registry)

    def add(mid: int, content: str, author: FakeUser, parent: int | None) -> FakeMessage:
        msg = FakeMessage(mid, content, author, channel, parent)
        registry[mid] = msg
        return msg

    add(10, '?ask hi there', human, None)
    add(11, f'{ASSISTANT_MARKER}\nHello! How can I help?', bot_user, 10)
    add(12, 'tell me more', human, 11)
    add(13, f'{ASSISTANT_MARKER}\nSure — here is more.', bot_user, 12)
    latest = add(14, 'thanks!', human, 13)

    return build_mixin(), latest


async def test_history_reconstructs_full_thread_in_order() -> None:
    mixin, latest = build_chain()
    history = await mixin._gather_history(latest)
    assert history == [
        {'role': 'user', 'content': 'hi there'},  # `?ask ` invocation stripped
        {'role': 'assistant', 'content': 'Hello! How can I help?'},  # marker stripped
        {'role': 'user', 'content': 'tell me more'},
        {'role': 'assistant', 'content': 'Sure — here is more.'},
    ]


async def test_history_excludes_the_current_message() -> None:
    mixin, latest = build_chain()
    history = await mixin._gather_history(latest)
    assert all(turn['content'] != 'thanks!' for turn in history)


async def test_history_empty_for_a_fresh_ask() -> None:
    mixin, _ = build_chain()
    root = FakeMessage(99, '?ask first question', FakeUser(1), FakeChannel({}), None)
    assert await mixin._gather_history(root) == []


@pytest.mark.parametrize(
    ('content', 'expected'),
    [
        ('?ask hello world', 'hello world'),
        ('?ai hello world', 'hello world'),
        ('?chat hello world', 'hello world'),
        ('just a plain reply', 'just a plain reply'),  # no prefix -> unchanged
        ('?asking is not the command', '?asking is not the command'),  # boundary: 'ask' != 'asking'
    ],
)
async def test_strip_invocation(content: str, expected: str) -> None:
    mixin = build_mixin()
    msg = FakeMessage(1, content, FakeUser(1), FakeChannel({}), None)
    assert await mixin._strip_invocation(msg) == expected


def test_is_assistant_message_requires_marker_and_bot_author() -> None:
    mixin = build_mixin()
    chan = FakeChannel({})
    ours = FakeMessage(1, f'{ASSISTANT_MARKER}\nhi', FakeUser(999, bot=True), chan, None)
    other_bot = FakeMessage(2, f'{ASSISTANT_MARKER}\nhi', FakeUser(5, bot=True), chan, None)
    human = FakeMessage(3, f'{ASSISTANT_MARKER}\nspoofed', FakeUser(1), chan, None)
    assert mixin._is_assistant_message(ours) is True
    assert mixin._is_assistant_message(other_bot) is False
    assert mixin._is_assistant_message(human) is False


async def test_extract_commands_from_backticks() -> None:
    mixin = build_mixin({'blackjack': FakeCommand('blackjack')})
    answer = 'Sure! Run `?blackjack` to start a round.'
    assert await mixin._extract_commands(answer, lone_message()) == ['?blackjack']


async def test_extract_commands_from_bare_token() -> None:
    mixin = build_mixin({'blackjack': FakeCommand('blackjack')})
    answer = 'Just type ?blackjack and follow the prompts.'
    assert await mixin._extract_commands(answer, lone_message()) == ['?blackjack']


async def test_extract_commands_ignores_unknown_and_hidden() -> None:
    mixin = build_mixin({'secret': FakeCommand('secret', hidden=True)})
    answer = 'Try `?frobnicate` or `?secret`.'  # one unknown, one hidden
    assert await mixin._extract_commands(answer, lone_message()) == []


async def test_extract_commands_deduplicates() -> None:
    mixin = build_mixin({'blackjack': FakeCommand('blackjack')})
    answer = 'Use `?blackjack`. Yes, `?blackjack` is the one!'
    assert await mixin._extract_commands(answer, lone_message()) == ['?blackjack']


async def test_extract_commands_handles_multiword_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assistant_mod, 'MAX_ACTION_BUTTONS', 2)
    cmds = {name: FakeCommand(name) for name in ('tag create', 'poll', 'giveaway', 'remind')}
    mixin = build_mixin(cmds)
    answer = 'Options: `?tag create`, `?poll`, `?giveaway`, `?remind`.'
    actions = await mixin._extract_commands(answer, lone_message())
    assert actions == ['?tag create', '?poll']  # multiword resolved, capped at 2


async def test_history_respects_char_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # Budget fits exactly the nearest turn ("Sure — here is more." = 20 chars) but not the
    # next one ("tell me more" = 12), so the walk stops after one turn.
    monkeypatch.setattr(assistant_mod, 'MAX_CONTEXT_CHARS', 25)
    mixin, latest = build_chain()
    history = await mixin._gather_history(latest)
    assert history == [{'role': 'assistant', 'content': 'Sure — here is more.'}]
