"""Tests for the AI moderation alert embed (:mod:`app.cogs.moderation.ai_alert`).

The interactive view is exercised via Discord and isn't unit-tested (like the rest of the
view layer); here we pin the embed builder — that it surfaces the verdict and message
context a moderator needs to act.
"""

from __future__ import annotations

from app.cogs.moderation.ai_alert import (
    AIModerationButton,
    build_ai_moderation_embed,
    build_ai_moderation_view,
)
from app.services.ai import ModerationVerdict


class FakeAuthor:
    id = 42
    mention = '<@42>'
    avatar = None  # get_asset_url returns "" for a non-discord object with no avatar

    def __str__(self) -> str:
        return 'BadUser#0001'


class FakeChannel:
    mention = '<#100>'


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.author = FakeAuthor()
        self.channel = FakeChannel()
        self.content = content
        self.jump_url = 'https://discord.com/channels/1/100/200'


def _fields(embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


def test_embed_surfaces_verdict_and_context() -> None:
    message = FakeMessage('some rude and offensive text')
    verdict = ModerationVerdict(flagged=True, category='harassment', reason='Rude language', confidence=1.0)

    embed = build_ai_moderation_embed(message, verdict)  # type: ignore[arg-type]
    fields = _fields(embed)

    assert fields['Category'] == '`harassment`'
    assert fields['Confidence'] == '`100%`'
    assert 'Rude language' in fields['Reason']
    assert '42' in fields['User']
    assert 'some rude and offensive text' in fields['Message']
    assert 'discord.com/channels/1/100/200' in (embed.description or '')


def test_view_has_five_persistent_buttons() -> None:
    view = build_ai_moderation_view(target_id=42, channel_id=100, message_id=200)
    ids = [child.custom_id for child in view.children]
    assert len(ids) == 5
    assert 'aimod:delete:42:100:200' in ids
    assert 'aimod:ban:42:100:200' in ids
    assert 'aimod:dismiss:42:100:200' in ids
    assert view.timeout is None  # persistent


def test_button_custom_id_roundtrips_through_template() -> None:
    button = AIModerationButton('ban', 42, 100, 200)
    match = AIModerationButton.__discord_ui_compiled_template__.match(button.custom_id)
    assert match is not None
    assert match['action'] == 'ban'
    assert (int(match['target']), int(match['channel']), int(match['message'])) == (42, 100, 200)


def test_embed_handles_empty_content() -> None:
    embed = build_ai_moderation_embed(
        FakeMessage(''),  # type: ignore[arg-type]
        ModerationVerdict(flagged=True, category='spam', reason='', confidence=0.8),
    )
    fields = _fields(embed)
    assert fields['Message'] == '*no text content*'
    # Empty reason falls back to a default rather than rendering blank.
    assert fields['Reason']
