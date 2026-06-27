"""Tests for the AI moderation alert embed (:mod:`app.cogs.moderation.ai_alert`).

The interactive view is exercised via Discord and isn't unit-tested (like the rest of the
view layer); here we pin the embed builder — that it surfaces the verdict and message
context a moderator needs to act.
"""

from __future__ import annotations

from app.cogs.moderation.ai_alert import build_ai_moderation_embed
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


def test_embed_handles_empty_content() -> None:
    embed = build_ai_moderation_embed(
        FakeMessage(''),  # type: ignore[arg-type]
        ModerationVerdict(flagged=True, category='spam', reason='', confidence=0.8),
    )
    fields = _fields(embed)
    assert fields['Message'] == '*no text content*'
    # Empty reason falls back to a default rather than rendering blank.
    assert fields['Reason']
