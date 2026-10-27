"""Poll vote logic: counting, switching, clearing, option removal and the vote race."""

from __future__ import annotations

import asyncio
import datetime
import types
from typing import Any

import pytest

from app.cogs.polls.cog import Polls
from app.cogs.polls.models import Poll, percentages


def _row(*, entries: list[tuple[int, int]] | None = None, **kwargs: Any) -> dict[str, Any]:
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    options = [{"index": i, "content": c, "votes": 0} for i, c in enumerate(kwargs.pop("options", ["A", "B", "C"]))]
    return {
        "id": 1,
        "message_id": 10,
        "channel_id": 20,
        "guild_id": 30,
        "published": now,
        "expires": now,
        "entries": entries or [],
        "metadata": {"args": [], "kwargs": {"question": "Q?", "options": options, "running": True, **kwargs}},
    }


def _poll(**kwargs: Any) -> Poll:
    return Poll(cog=types.SimpleNamespace(bot=None), record=_row(**kwargs))


def test_percentages_always_sum_to_100() -> None:
    assert percentages([1, 1, 1]) == [34, 33, 33]
    assert sum(percentages([2, 3, 7, 11])) == 100
    assert percentages([0, 0]) == [0, 0]
    assert percentages([5, 0]) == [100, 0]


def test_counts_are_derived_from_entries() -> None:
    # Stored counters drifted (votes=0); entries are the truth. The vote for option 9
    # points at nothing and must not lock its voter out.
    poll = _poll(entries=[(1, 0), (2, 0), (3, 2), (4, 9)])
    assert [o["votes"] for o in poll.options] == [2, 0, 1]
    assert poll.votes == 3
    assert poll.get_entry(4) is None


def test_vote_change_and_clear() -> None:
    poll = _poll()
    assert poll.cast_vote(1, 0) is None
    assert poll.cast_vote(1, 2) == 0  # switching moves the vote, never double counts
    assert poll.votes == 1
    assert [o["votes"] for o in poll.options] == [0, 0, 1]
    assert poll.clear_vote(1) == 2
    assert poll.votes == 0
    assert poll.clear_vote(1) is None
    with pytest.raises(ValueError):
        poll.cast_vote(1, 7)


def test_vote_for_option_a_is_a_vote() -> None:
    # Index 0 is falsy: the info button / legacy select used to treat it as "no vote".
    poll = _poll(entries=[(1, 0)])
    assert poll.get_entry(1) == (1, 0)


def test_remove_option_moves_votes_with_their_option() -> None:
    poll = _poll(entries=[(1, 0), (2, 1), (3, 2)])
    poll.remove_option(poll.options[0])
    assert [o["content"] for o in poll.options] == ["B", "C"]
    # B's voter still votes B, C's voter still votes C (both re-indexed); A's vote is gone.
    assert poll.entries == {(2, 0), (3, 1)}
    assert [o["votes"] for o in poll.options] == [1, 1]


def test_edited_question_and_bad_colour() -> None:
    poll = _poll(content="Edited?", color="not a colour")
    assert poll.question == "Edited?"  # ``edit()`` / dashboard store it under ``content``
    assert poll.color is not None  # a free-text colour no longer makes the poll unloadable


def test_hidden_results_and_role_gate() -> None:
    poll = _poll(entries=[(1, 0)], hide_results=True, role_id=99)
    assert poll.results_hidden
    assert "Results are shown" in poll.to_fields(extras=False)[0]["value"]
    assert not poll.can_vote(types.SimpleNamespace(id=5, roles=[types.SimpleNamespace(id=1)]))
    assert poll.can_vote(types.SimpleNamespace(id=5, roles=[types.SimpleNamespace(id=99)]))

    poll.kwargs["running"] = False
    assert not poll.results_hidden  # revealed once the poll ends
    assert "1" in poll.to_fields(extras=False)[0]["value"]


async def test_concurrent_votes_are_not_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two voters at once: each write rewrites the whole row, so without the lock one is lost."""
    stored = {"entries": []}

    async def get_guild_poll(_guild_id: int, _poll_id: int) -> Poll:
        return _poll(entries=list(stored["entries"]))

    async def fake_edit(self: Poll, **_: Any) -> Poll:
        await asyncio.sleep(0)  # yield, as the real UPDATE round-trip does
        stored["entries"] = sorted(self.entries)
        return self

    monkeypatch.setattr(Poll, "edit", fake_edit)
    cog = Polls.__new__(Polls)
    cog._poll_locks = {}
    cog.get_guild_poll = get_guild_poll  # type: ignore[method-assign]

    users = [types.SimpleNamespace(id=i, roles=[]) for i in range(1, 21)]
    results = await asyncio.gather(*(cog.submit_vote(30, 1, user, user.id % 3) for user in users))

    assert all(outcome == "voted" for _, outcome in results)
    assert len(stored["entries"]) == 20


async def test_submit_vote_refuses_ended_and_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    row: dict[str, Any] = {"running": True, "role_id": 99}

    async def get_guild_poll(_guild_id: int, _poll_id: int) -> Poll:
        return _poll(**row)

    cog = Polls.__new__(Polls)
    cog._poll_locks = {}
    cog.get_guild_poll = get_guild_poll  # type: ignore[method-assign]

    outsider = types.SimpleNamespace(id=1, roles=[])
    assert (await cog.submit_vote(30, 1, outsider, 0))[1] == "forbidden"
    assert (await cog.submit_vote(30, 1, outsider, None))[1] == "novote"
    row["running"] = False
    assert (await cog.submit_vote(30, 1, outsider, 0))[1] == "ended"


async def test_history_of_others_needs_manage_messages() -> None:
    sent: list[str] = []

    async def send_error(msg: str) -> None:
        sent.append(msg)

    perms = types.SimpleNamespace(manage_messages=False)
    ctx = types.SimpleNamespace(
        guild=types.SimpleNamespace(id=1),
        author=types.SimpleNamespace(id=1, guild_permissions=perms),
        send_error=send_error,
    )

    async def get_guild_polls(_gid: int) -> list[Poll]:
        return [_poll(), ]

    cog = types.SimpleNamespace(get_guild_polls=get_guild_polls)
    await Polls.polls_history.callback(cog, ctx, types.SimpleNamespace(id=2))  # type: ignore[arg-type]
    assert sent and "Manage Messages" in sent[0]
