"""Shared pytest fixtures.

These provide lightweight mocks of the :class:`~app.core.Bot` and
:class:`~app.database.base.Database` objects so that data-access code
(repositories) can be tested in isolation, without a real PostgreSQL pool or a
running Discord connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_bot() -> MagicMock:
    """A stand-in for the :class:`~app.core.Bot` instance."""
    return MagicMock(name='Bot')


@pytest.fixture
def mock_db(mock_bot: MagicMock) -> MagicMock:
    """A stand-in for :class:`~app.database.base.Database`.

    The pool helpers (``execute``/``fetch``/``fetchrow``/``fetchval``) are
    :class:`AsyncMock`\\s so tests can assert on the SQL and arguments a
    repository forwards to them. ``acquire`` returns an async context manager
    yielding a connection mock, mirroring ``async with db.acquire() as conn``.
    The cached config getter exposes an ``invalidate`` mock so cache-busting can
    be asserted.
    """
    db = MagicMock(name='Database')
    db.bot = mock_bot

    db.execute = AsyncMock(return_value='DELETE 0')
    db.fetch = AsyncMock(return_value=[])
    db.fetchrow = AsyncMock(return_value=None)
    db.fetchval = AsyncMock(return_value=None)

    # ``async with db.acquire() as conn: ...``
    conn = AsyncMock(name='Connection')
    acquire_cm = MagicMock(name='AcquireContext')
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    db.acquire = MagicMock(return_value=acquire_cm)
    db.connection = conn  # exposed for convenience in assertions

    # Cached getters expose ``.invalidate`` for cache busting.
    db.get_guild_config = MagicMock(name='get_guild_config')
    db.get_guild_config.invalidate = MagicMock(name='get_guild_config.invalidate')

    # Signal hub for signal-based cache invalidation.
    db.signals = MagicMock(name='CacheSignalHub')
    db.signals.fire = MagicMock(return_value=1)

    return db
