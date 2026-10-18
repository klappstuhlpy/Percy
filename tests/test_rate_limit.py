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


def test_a_burst_trips_once_then_starts_a_fresh_window() -> None:
    limit = _by_content()

    results = [limit.is_ratelimited(_message(0.0)) for _ in range(11)]  # type: ignore[arg-type]

    # Trips on the 6th instant copy, then needs another full burst before tripping again.
    assert results == [False] * 5 + [True] + [False] * 5


def _tagged(min_hits: int) -> RateLimit:
    return RateLimit(5, 15.0, key=lambda msg: msg.channel.id, tagger=lambda msg: msg.author.id, min_hits=min_hits)


def test_tagged_trip_names_nobody_when_nobody_repeated_themselves() -> None:
    limit = _tagged(2)

    results = [limit.is_ratelimited(_message(i * 0.5, author=i)) for i in range(6)]  # type: ignore[arg-type]

    assert results[:5] == [False] * 5
    assert results[5] == []


def test_tagged_trip_names_only_the_repeat_sender() -> None:
    limit = _tagged(2)
    authors = [1, 2, 3, 1, 4, 5]

    results = [limit.is_ratelimited(_message(i * 0.5, author=a)) for i, a in enumerate(authors)]  # type: ignore[arg-type]

    assert results[5] == [1]


def test_tagged_hits_older_than_the_window_do_not_count() -> None:
    limit = _tagged(2)

    limit.is_ratelimited(_message(0.0, author=1))  # type: ignore[arg-type]
    # Keep the channel busy (so the bucket never drains) until the first message is out of the window.
    for i in range(1, 6):
        limit.is_ratelimited(_message(i * 3.0, author=100 + i))  # type: ignore[arg-type]
    tripped = [limit.is_ratelimited(_message(16.0 + i * 0.1, author=200 + i)) for i in range(4)]  # type: ignore[arg-type]
    tripped.append(limit.is_ratelimited(_message(16.5, author=1)))  # type: ignore[arg-type]

    assert tripped[:4] == [False] * 4
    assert tripped[4] == []  # it tripped, but author 1's first message is 16.5 s old
