"""Tests for :mod:`app.services.purge`."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services import build_purge_predicate


@dataclass(eq=False)  # identity equality, like a real discord.User/Member
class FakeAuthor:
    bot: bool = False


@dataclass
class FakeMessage:
    """A structural stand-in for ``discord.Message`` satisfying ``PurgeMessage``."""

    author: FakeAuthor = field(default_factory=FakeAuthor)
    webhook_id: int | None = None
    interaction: object | None = None
    embeds: list[object] = field(default_factory=list)
    attachments: list[object] = field(default_factory=list)
    reactions: list[object] = field(default_factory=list)
    content: str = ""
    pinned: bool = False


def test_no_flags_matches_everything_and_requests_prompt() -> None:
    plan = build_purge_predicate()

    assert plan.require_prompt is True
    assert plan.predicate(FakeMessage()) is True
    assert plan.predicate(FakeMessage(content="anything", pinned=True)) is True


def test_any_flag_clears_the_prompt() -> None:
    plan = build_purge_predicate(embeds=True)

    assert plan.require_prompt is False


def test_bot_without_webhooks_keeps_webhooks_but_not_their_messages() -> None:
    plan = build_purge_predicate(bot=True)
    bot = FakeAuthor(bot=True)

    # Plain bot message (no webhook): removed.
    assert plan.predicate(FakeMessage(author=bot)) is True
    # Webhook message from a bot: spared, unless it is an interaction response.
    assert plan.predicate(FakeMessage(author=bot, webhook_id=42)) is False
    assert plan.predicate(FakeMessage(author=bot, webhook_id=42, interaction=object())) is True
    # Human message: spared.
    assert plan.predicate(FakeMessage(author=FakeAuthor(bot=False))) is False


def test_bot_with_webhooks_removes_any_bot_message() -> None:
    plan = build_purge_predicate(bot=True, webhooks=True)
    bot = FakeAuthor(bot=True)

    assert plan.predicate(FakeMessage(author=bot)) is True
    assert plan.predicate(FakeMessage(author=bot, webhook_id=42)) is True
    assert plan.predicate(FakeMessage(author=FakeAuthor(bot=False), webhook_id=42)) is False


def test_webhooks_only_targets_webhook_messages() -> None:
    plan = build_purge_predicate(webhooks=True)

    assert plan.predicate(FakeMessage(webhook_id=42)) is True
    assert plan.predicate(FakeMessage(webhook_id=None)) is False


def test_content_attachment_filters() -> None:
    assert build_purge_predicate(embeds=True).predicate(FakeMessage(embeds=[object()])) is True
    assert build_purge_predicate(embeds=True).predicate(FakeMessage(embeds=[])) is False
    assert build_purge_predicate(files=True).predicate(FakeMessage(attachments=[object()])) is True
    assert build_purge_predicate(reactions=True).predicate(FakeMessage(reactions=[object()])) is True


def test_emoji_filter_matches_custom_emoji_syntax() -> None:
    plan = build_purge_predicate(emoji=True)

    assert plan.predicate(FakeMessage(content="hi <:wave:123>")) is True
    assert plan.predicate(FakeMessage(content="hi \N{WAVING HAND SIGN}")) is False


def test_user_filter_compares_author_identity() -> None:
    target = FakeAuthor(bot=False)
    plan = build_purge_predicate(user=target)

    assert plan.predicate(FakeMessage(author=target)) is True
    assert plan.predicate(FakeMessage(author=FakeAuthor(bot=False))) is False


def test_substring_prefix_suffix_filters() -> None:
    assert build_purge_predicate(contains="spam").predicate(FakeMessage(content="a spam b")) is True
    assert build_purge_predicate(contains="spam").predicate(FakeMessage(content="clean")) is False
    assert build_purge_predicate(prefix="!").predicate(FakeMessage(content="!cmd")) is True
    assert build_purge_predicate(prefix="!").predicate(FakeMessage(content="cmd")) is False
    assert build_purge_predicate(suffix="?").predicate(FakeMessage(content="ok?")) is True
    assert build_purge_predicate(suffix="?").predicate(FakeMessage(content="ok")) is False


def test_delete_pinned_controls_whether_pins_are_spared() -> None:
    # Default (delete_pinned=True): the not-pinned guard is absent.
    sparing = build_purge_predicate(contains="x", delete_pinned=False)
    assert sparing.predicate(FakeMessage(content="x", pinned=True)) is False
    assert sparing.predicate(FakeMessage(content="x", pinned=False)) is True

    deleting = build_purge_predicate(contains="x", delete_pinned=True)
    assert deleting.predicate(FakeMessage(content="x", pinned=True)) is True


def test_require_all_versus_any() -> None:
    require_all = build_purge_predicate(embeds=True, files=True, require="all")
    require_any = build_purge_predicate(embeds=True, files=True, require="any")

    only_embed = FakeMessage(embeds=[object()])
    both = FakeMessage(embeds=[object()], attachments=[object()])

    assert require_all.predicate(only_embed) is False
    assert require_all.predicate(both) is True
    assert require_any.predicate(only_embed) is True
