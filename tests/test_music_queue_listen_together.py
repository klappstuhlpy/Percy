"""Following the listen-together host to a new track must not end the session."""

from __future__ import annotations

from discord.utils import MISSING

from app.cogs.music.models import Queue


def test_reset_ends_listen_together_by_default() -> None:
    queue = Queue()
    queue.listen_together = 42

    queue.reset()

    assert queue.listen_together is MISSING


def test_reset_can_keep_the_listen_together_host() -> None:
    queue = Queue()
    queue.listen_together = 42

    queue.reset(keep_listen_together=True)

    assert queue.listen_together == 42
    assert queue.is_empty
