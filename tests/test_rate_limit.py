"""The message rate limit behind anti-spam actually trips."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

from app.utils.helpers import RateLimit

START = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=datetime.UTC)


def _message(seconds: float, content: str = "JOIN NOW", author: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        created_at=START + datetime.timedelta(seconds=seconds),
        content=content,
        channel=SimpleNamespace(id=10),
        author=SimpleNamespace(id=author),
    )


def _by_content() -> RateLimit:
    return RateLimit(5, 15.0, key=lambda msg: (msg.channel.id, msg.content))


def test_burst_of_identical_messages_is_limited() -> None:
    limit = _by_content()

    results = [limit.is_ratelimited(_message(i * 0.5)) for i in range(8)]  # type: ignore[arg-type]

    assert results[:5] == [False] * 5
    assert any(results[5:])


def test_slow_identical_messages_are_not_limited() -> None:
    limit = _by_content()

    results = [limit.is_ratelimited(_message(i * 4.0)) for i in range(10)]  # type: ignore[arg-type]

    assert not any(results)


def test_different_text_is_tracked_separately() -> None:
    limit = _by_content()

    results = [limit.is_ratelimited(_message(i * 0.5, content=f"hello {i}")) for i in range(8)]  # type: ignore[arg-type]

    assert not any(results)


def test_tagged_limit_returns_the_spammers() -> None:
    limit = RateLimit(5, 15.0, key=lambda msg: msg.channel.id, tagger=lambda msg: msg.author.id)

    results = [limit.is_ratelimited(_message(i * 0.5, author=i % 2)) for i in range(8)]  # type: ignore[arg-type]

    tripped = [r for r in results if r]
    assert tripped
    assert set(tripped[0]) <= {0, 1}
