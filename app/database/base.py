from __future__ import annotations

import asyncio
import contextlib
import datetime
import enum
import json
import logging
import time
from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import asyncpg
import dateutil.tz
import discord
from discord.utils import MISSING

from sshtunnel import SSHTunnelForwarder

from app.database.repositories import (
    AdminRepository,
    AniListRepository,
    AutoRespondersRepository,
    CasesRepository,
    ComicsRepository,
    EconomyRepository,
    EmojiStatsRepository,
    GameStatsRepository,
    GiveawaysRepository,
    GuildsRepository,
    HighlightsRepository,
    IncidentsRepository,
    LevelingRepository,
    ModerationRepository,
    MusicSessionsRepository,
    PlaylistsRepository,
    PollsRepository,
    RoleMenusRepository,
    StarboardRepository,
    StatCountersRepository,
    StatsRepository,
    TagsRepository,
    TempChannelsRepository,
    TimersRepository,
    UsersRepository,
    VotesRepository,
)
from app.utils import BaseFlags, CancellableQueue, cache, flag_value
from app.utils.query_tracker import QueryTracker
from app.utils.signals import CacheSignalHub
from config import DatabaseConfig, Emojis

from .migrations import MigrationRunner

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Iterator, Sequence
    from typing import Self, TypeVar

    from app.core import Bot

    DatabaseT = TypeVar("DatabaseT", bound="_Database")

__all__ = (
    "Balance",
    "BaseRecord",
    "Database",
    "GuildConfig",
    "Sentinel",
    "UserConfig",
)


log = logging.getLogger(__name__)


def _encode_jsonb(value: Any) -> str:
    return json.dumps(value)


def _decode_jsonb(value: str) -> Any:
    return json.loads(value)


def _one_line(query: str) -> str:
    """Collapse a query to a single trimmed line for logging."""
    return " ".join(query.split())[:200]


@dataclass(frozen=True, slots=True)
class PoolConnectionState:
    """A snapshot of a single pooled connection (for diagnostics)."""

    generation: int
    in_use: bool
    is_closed: bool


@dataclass(frozen=True, slots=True)
class PoolStats:
    """A point-in-time view of the connection pool, for health/diagnostics."""

    size: int
    idle: int
    in_use: int
    min_size: int
    max_size: int
    waiting: int
    generation: int
    connections: tuple[PoolConnectionState, ...]


class _Database:
    """Owns the asyncpg connection pool, timed query helpers, and cache-invalidation signals.

    The pool is created lazily on a background task started in ``__init__``; callers await
    :meth:`wait` (which the bot does once at startup) before issuing queries. Every query
    goes through :meth:`_observe`, which records timing into :attr:`query_tracker` and logs
    failures with the offending statement — a single, consistent execution path for all SQL.

    The :attr:`signals` hub coordinates cache invalidation: repositories and
    :class:`BaseRecord` mutations fire named signals (e.g. ``"guild_config_changed"``),
    which automatically bust the memoized ``get_guild_config`` / ``get_user_config`` /
    ``get_guild_sentinel`` cached getters on :class:`Database`.

    Attributes
    ----------
    bot: Bot
        The bot instance.
    loop: asyncio.AbstractEventLoop
        The event loop used for database operations.
    query_tracker: QueryTracker
        Records per-query timing and surfaces slow queries.
    signals: CacheSignalHub
        Cache-invalidation signal hub shared with repositories and BaseRecord instances.
    """

    __slots__ = ("_connect_task", "_internal_pool", "_ready", "_ssh_tunnel", "bot", "loop", "query_tracker", "signals")

    if TYPE_CHECKING:
        loop: asyncio.AbstractEventLoop
        _connect_task: asyncio.Task
        _ssh_tunnel: SSHTunnelForwarder | None
        query_tracker: QueryTracker
        signals: CacheSignalHub

    def __init__(self, bot: Bot, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.bot = bot
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_running_loop()
        self.signals = CacheSignalHub()
        self.query_tracker = QueryTracker()
        self._internal_pool: asyncpg.Pool | None = None
        self._ssh_tunnel: SSHTunnelForwarder | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._connect_task: asyncio.Task = self.loop.create_task(self._connect())

    async def _connect(self) -> None:
        """Builds the pool and applies pending migrations; shuts the bot down on failure."""
        try:
            await self._open_ssh_tunnel()
            self._internal_pool = await self.create_pool()
            async with self.acquire() as conn:
                await MigrationRunner().upgrade(conn)  # type: ignore[arg-type]
        except Exception:
            log.critical("Failed to connect to PostgreSQL; shutting down the bot.", exc_info=True)
            await self.bot.close()
        else:
            pool = self._internal_pool
            log.info("PostgreSQL pool ready (min=%d, max=%d).", pool.get_min_size(), pool.get_max_size())
        finally:
            # Always release waiters: on failure ``pool`` stays ``None`` and query helpers
            # raise a clear error rather than hanging on ``_ready``.
            self._ready.set()

    async def _open_ssh_tunnel(self) -> None:
        """Opens an SSH tunnel to the database host when running in beta mode with SSH config set."""
        import config as app_config

        if not app_config.beta or not DatabaseConfig.ssh_host:
            return

        self._ssh_tunnel = SSHTunnelForwarder(
            (DatabaseConfig.ssh_host, DatabaseConfig.ssh_port),
            ssh_username=DatabaseConfig.ssh_user,
            ssh_pkey=DatabaseConfig.ssh_key_path,
            ssh_private_key_password=DatabaseConfig.ssh_key_passphrase,
            remote_bind_address=(DatabaseConfig.host, DatabaseConfig.port),
        )
        await asyncio.to_thread(self._ssh_tunnel.start)

        DatabaseConfig.host = '127.0.0.1'
        DatabaseConfig.port = self._ssh_tunnel.local_bind_port
        log.info("SSH tunnel open: localhost:%d -> %s:%d", self._ssh_tunnel.local_bind_port, DatabaseConfig.ssh_host, 5432)

    @classmethod
    async def create_pool(cls) -> asyncpg.Pool:
        """Creates the connection pool, registering a JSONB text codec on each connection."""

        async def init(con: asyncpg.Connection) -> None:
            await con.set_type_codec("jsonb", schema="pg_catalog", encoder=_encode_jsonb, decoder=_decode_jsonb)

        return await asyncpg.create_pool(init=init, **DatabaseConfig.pool_kwargs())  # type: ignore[arg-type]

    async def close(self) -> None:
        """Closes the connection pool and SSH tunnel (if active)."""
        if self._internal_pool is not None:
            await self._internal_pool.close()
            self._internal_pool = None
        if self._ssh_tunnel is not None:
            self._ssh_tunnel.stop()
            self._ssh_tunnel = None

    async def wait(self: DatabaseT) -> DatabaseT:
        """|coro| Waits for the pool to finish connecting and returns the database instance."""
        await self._connect_task
        return self

    @property
    def pool(self) -> asyncpg.Pool:
        """The live connection pool, or a clear error if accessed before it is ready."""
        if self._internal_pool is None:
            raise RuntimeError("Database pool is not initialised yet — await Database.wait() first.")
        return self._internal_pool

    async def _ensure_ready(self) -> None:
        """Cheap fast-path gate so a query issued before :meth:`wait` blocks until the pool exists."""
        if self._internal_pool is None:
            await self._ready.wait()

    @contextlib.contextmanager
    def _observe(self, query: str) -> Generator[None, None, None]:
        """Times a query into the tracker and logs the statement if it errors."""
        start = time.perf_counter()
        try:
            yield
        except asyncpg.PostgresError:
            log.error("Query failed after %.1fms: %s", (time.perf_counter() - start) * 1000.0, _one_line(query))
            raise
        finally:
            self.query_tracker.record(query, (time.perf_counter() - start) * 1000.0)

    def acquire(self, *, timeout: float | None = None) -> asyncpg.pool.PoolAcquireContext:
        """Acquires a pooled connection (use ``async with``), e.g. for multi-statement transactions."""
        return self.pool.acquire(timeout=timeout)  # type: ignore[arg-type]

    def release(self, conn: asyncpg.Connection, *, timeout: float | None = None) -> Awaitable[None]:
        return self.pool.release(conn, timeout=timeout)  # type: ignore[arg-type]

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        await self._ensure_ready()
        with self._observe(query):
            return await self.pool.execute(query, *args, timeout=timeout)

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[Any]:
        await self._ensure_ready()
        with self._observe(query):
            return await self.pool.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> asyncpg.Record | None:
        await self._ensure_ready()
        with self._observe(query):
            return await self.pool.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float | None = None) -> Any:
        await self._ensure_ready()
        with self._observe(query):
            return await self.pool.fetchval(query, *args, column=column, timeout=timeout)

    def pool_stats(self) -> PoolStats:
        """Returns a structured snapshot of the pool for the health report.

        Encapsulates the only access to asyncpg's pool internals (there is no public API for
        per-connection generation/in-use/closed state or the acquire-waiter count), so callers
        get a stable, typed view instead of poking private attributes themselves.
        """
        pool = self.pool
        connections = tuple(
            PoolConnectionState(
                generation=holder._generation,
                in_use=holder._in_use is not None,
                is_closed=holder._con is None or holder._con.is_closed(),
            )
            for holder in pool._holders
        )
        return PoolStats(
            size=pool.get_size(),
            idle=pool.get_idle_size(),
            in_use=pool.get_size() - pool.get_idle_size(),
            min_size=pool.get_min_size(),
            max_size=pool.get_max_size(),
            waiting=len(pool._queue._getters),
            generation=pool._generation,
            connections=connections,
        )


class Database(_Database):
    guilds: GuildsRepository
    users: UsersRepository
    polls: PollsRepository
    leveling: LevelingRepository
    moderation: ModerationRepository
    tags: TagsRepository
    stats: StatsRepository
    incidents: IncidentsRepository
    giveaways: GiveawaysRepository
    emoji_stats: EmojiStatsRepository
    game_stats: GameStatsRepository
    highlights: HighlightsRepository
    temp_channels: TempChannelsRepository
    playlists: PlaylistsRepository
    music_sessions: MusicSessionsRepository
    admin: AdminRepository
    timers: TimersRepository
    comics: ComicsRepository
    starboard: StarboardRepository
    cases: CasesRepository
    economy: EconomyRepository
    rolemenu: RoleMenusRepository
    autoresponders: AutoRespondersRepository
    stat_counters: StatCountersRepository
    votes: VotesRepository

    def __init__(self, bot: Bot, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(bot, loop=loop)
        self.guilds = GuildsRepository(self)
        self.users = UsersRepository(self)
        self.polls = PollsRepository(self)
        self.leveling = LevelingRepository(self)
        self.moderation = ModerationRepository(self)
        self.tags = TagsRepository(self)
        self.stats = StatsRepository(self)
        self.incidents = IncidentsRepository(self)
        self.giveaways = GiveawaysRepository(self)
        self.emoji_stats = EmojiStatsRepository(self)
        self.game_stats = GameStatsRepository(self)
        self.highlights = HighlightsRepository(self)
        self.temp_channels = TempChannelsRepository(self)
        self.playlists = PlaylistsRepository(self)
        self.music_sessions = MusicSessionsRepository(self)
        self.admin = AdminRepository(self)
        self.timers = TimersRepository(self)
        self.comics = ComicsRepository(self)
        self.starboard = StarboardRepository(self)
        self.cases = CasesRepository(self)
        self.economy = EconomyRepository(self)
        self.rolemenu = RoleMenusRepository(self)
        self.autoresponders = AutoRespondersRepository(self)
        self.stat_counters = StatCountersRepository(self)
        self.anilist = AniListRepository(self)
        self.votes = VotesRepository(self)

        self._register_cache_signals()

    def _register_cache_signals(self) -> None:
        """Wire up cache invalidation signals so repositories can fire them on mutation."""
        s = self.signals
        s.register("guild_config_changed").connect(self.get_guild_config, None)
        s.register("user_config_changed").connect(self.get_user_config, None)
        s.register("sentinel_changed").connect(self.get_guild_sentinel, None)
        s.register("ai_config_changed").connect(self.get_guild_ai_config, None)

    @cache.cache()
    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        """|coro| @cached

        Fetches the guild record from the database.

        Parameters
        ----------
        guild_id : :class:`int`
            The guild ID to fetch the record for.

        Returns
        -------
        :class:`GuildConfig`
            The guild record if it exists, else a new record.
        """
        record = await self.guilds.get_config_record(guild_id)
        return GuildConfig(bot=self.bot, record=record)

    @cache.cache()
    async def get_guild_ai_config(self, guild_id: int) -> GuildAIConfig:
        """|coro| @cached

        Resolve a guild's AI configuration: the server-wide ``ai_flags`` bitfield plus any
        per-channel overrides, as a :class:`GuildAIConfig`. Reuses the cached guild config
        for the flags, so this only adds the (small) override lookup.
        """
        config = await self.get_guild_config(guild_id)
        override_records = await self.guilds.get_ai_overrides(guild_id)
        overrides = {r["channel_id"]: (r["flags_mask"], r["enabled_mask"]) for r in override_records}
        return GuildAIConfig(guild_id=guild_id, flags=config.ai_flags, overrides=overrides)

    @cache.cache(action=lambda g: g.cancel_task())
    async def get_guild_sentinel(self, guild_id: int | None) -> Sentinel | None:
        """|coro|

        Get the sentinel for the guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the sentinel from.

        Returns
        -------
        :class:`Sentinel`
            The sentinel if it exists, else ``None``.
        """
        if guild_id is None:
            return None

        record = await self.guilds.get_sentinel_record(guild_id)
        if record is None:
            return None

        members = await self.guilds.get_sentinel_members(guild_id)
        return Sentinel(members, bot=self.bot, record=record)

    @cache.cache()
    async def get_user_config(self, user_id: int, /) -> UserConfig:
        """|coro| @cached

        Retrieves the user config for a user.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to retrieve the config for.

        Returns
        -------
        :class:`UserConfig`
            The user config for the user, if it exists.
        """
        record = await self.users.get_settings_record(user_id)
        return UserConfig(bot=self.bot, record=record)

    async def get_user_timezone(self, user_id: int, /) -> str:
        """|coro|

        Retrieves the user's timezone.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to retrieve the timezone for.

        Returns
        -------
        :class:`str`
            The user's timezone.
        """
        return await self.users.get_timezone(user_id)

    async def get_user_balance(self, user_id: int, guild_id: int) -> Balance:
        """|coro|

        A coroutine that gets the balance of a user in a guild.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to get the balance from.
        guild_id: :class:`int`
            The guild ID to get the balance from.

        Returns
        -------
        :class:`Balance`
            The balance of the user in the guild.
        """
        record = await self.users.get_balance_record(user_id, guild_id)
        return Balance(bot=self.bot, record=record)

    async def get_guild_balances(self, guild_id: int) -> list[Balance]:
        """|coro|

        A coroutine that gets the balances of all users in a guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the balances from.

        Returns
        -------
        list[:class:`Balance`]
            The balances of all users in the guild.
        """
        records = await self.users.get_guild_balance_records(guild_id)
        return [Balance(bot=self.bot, record=record) for record in records]


class BaseRecord(ABC):
    """A lightweight ORM mapping an ``asyncpg.Record`` onto a typed Python object.

    Subclasses declare ``__slots__`` matching database columns; the base class maps a
    fetched row onto attributes automatically. Declarative class kwargs control
    persistence behaviour:

    Parameters (passed as ``class Foo(BaseRecord, table=..., pk=..., ...)``)
    ----------
    table : str | None
        The PostgreSQL table this record maps to. Required for :meth:`update`,
        :meth:`delete`, and :meth:`refresh` to work without overrides.
    pk : str | tuple[str, ...]
        Primary-key column(s) for WHERE clauses. Defaults to ``"id"``.
        Composite keys are passed as a tuple, e.g. ``pk=("user_id", "guild_id")``.
    changed_signal : str | None
        Cache-invalidation signal fired (with pk values) after :meth:`update` or
        :meth:`delete`. ``None`` means no signal is fired.
    ignore_record : bool
        Allow construction without a backing ``asyncpg.Record`` (for temporary/
        projection instances created via :meth:`temporary`).

    Mutation helpers
    ----------------
    All mutation methods build parameterized SQL, re-hydrate the instance in place from
    the ``RETURNING *`` row (so the object never goes stale), and fire the configured
    cache signal. Column names are validated against the backing record before any SQL is
    built, closing the identifier-injection surface.

    - :meth:`update` — ``SET col = $n``
    - :meth:`add` / :meth:`remove` — ``SET col = col +/- $n``
    - :meth:`append` / :meth:`prune` — ``ARRAY_APPEND`` / ``ARRAY_REMOVE``
    - :meth:`merge` — ``ARRAY_CAT``
    - :meth:`delete` — ``DELETE FROM ... WHERE pk = ...``
    - :meth:`refresh` — ``SELECT * FROM ... WHERE pk = ...`` (re-hydrate without mutating)

    Lifecycle hooks
    ---------------
    - :meth:`_coerce` — post-load type conversions (runs after construction and after
      every hydration). Override to convert raw DB types (ints → flag objects, arrays →
      sets, JSON blobs → extracted fields).
    - :meth:`_update` — the SQL execution path. Override only for records with bespoke
      persistence semantics (e.g. Sentinel, which must skip hydration and signals).

    Examples
    --------
    .. code-block:: python3

        class User(BaseRecord, table="users", pk="id", changed_signal="user_changed"):
            __slots__ = ('id', 'name', 'age')

        class Balance(BaseRecord, table="economy", pk=("user_id", "guild_id")):
            __slots__ = ('user_id', 'guild_id', 'cash', 'bank')

        # Mutate and stay in sync:
        user = User(bot=bot, record=row)
        await user.update(name="Jane")   # UPDATE users SET name=$2 WHERE id=$1 RETURNING *
        await user.delete()              # DELETE FROM users WHERE id=$1
    """

    if TYPE_CHECKING:
        __record: asyncpg.Record
        __ignore_record__: bool
        __tablename__: str | None
        __pk__: tuple[str, ...]
        __changed_signal__: str | None

    __slots__ = ("__record",)

    def __init_subclass__(
        cls,
        *,
        ignore_record: bool = False,
        table: str | None = None,
        pk: str | tuple[str, ...] = "id",
        changed_signal: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Registers a record subclass and its update metadata.

        Parameters
        ----------
        ignore_record:
            Allow construction without a backing record (temporary/projection instances).
        table:
            The table this record maps to. When set, the inherited :meth:`_update` builds the
            ``UPDATE`` automatically; otherwise a subclass must override ``_update``.
        pk:
            Primary-key column(s) for the ``WHERE`` clause — a single name or a tuple.
        changed_signal:
            Cache-invalidation signal fired (with the pk values) after a successful update;
            ``None`` means the record is its own cache source and fires nothing.
        """
        if cls is BaseRecord:
            raise TypeError("Class `BaseRecord` must be initialized by subclassing it.")

        cls.__ignore_record__ = ignore_record
        cls.__tablename__ = table
        cls.__pk__ = (pk,) if isinstance(pk, str) else tuple(pk)
        cls.__changed_signal__ = changed_signal
        super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: dict[str, Any] | object | asyncpg.Record) -> None:
        """Initialize a new instance of the `PostgresItem` class.

        Parameters
        ----------
        record : asyncpg.Record
            The PostgreSQL record to be associated with the item.

        Raises
        ------
        TypeError
            If a subclass of `BaseRecord` is instantiated without providing a `record` keyword argument, and the
            class does not specify to ignore the record.
        """
        self.__record = record = kwargs.pop("record", None)  # type: ignore
        if record is None and not self.__class__.__ignore_record__:
            raise TypeError("Subclasses of `BaseRecord` must provide a `record` keyword-only argument.")

        if record:
            if not isinstance(record, (asyncpg.Record, dict)):  # dict-like is okay too
                raise TypeError(f"The record must be an instance of `asyncpg.Record`, not `{record.__class__.__name__}`.")

            for k, v in record.items():
                self._set_item_safe(k, v)
        else:
            self.__record = kwargs  # type: ignore

        if kwargs:
            for k, v in kwargs.items():
                self._set_item_safe(k, v)

        self._coerce()

    def _set_item_safe(self, key: str, value: Any) -> None:
        """Sets an item as a class attribute if its present in the __slots__ attribute."""
        if key in self.__slots__:
            setattr(self, key, value)

    def _coerce(self) -> None:
        """Hook for post-load type conversions (arrays → sets, flag ints → flag objects, …).

        Runs after the raw row is mapped onto the instance — both at construction and after an
        in-place update — so a subclass's converted attributes never regress to raw DB types.
        The default does nothing.
        """

    def _hydrate(self, record: asyncpg.Record) -> None:
        """Re-maps a freshly-returned row onto this instance, then re-applies :meth:`_coerce`."""
        self.__record = record
        for key, value in record.items():
            self._set_item_safe(key, value)
        self._coerce()

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Self:
        """|coro| Builds and executes the ``UPDATE`` for this record.

        The default implementation uses the ``table``/``pk``/``changed_signal`` declared
        via ``__init_subclass__``:

        1. Builds ``UPDATE "<table>" SET <key(col)> WHERE <pk> RETURNING *``
        2. Re-hydrates this instance in place from the returned row (via :meth:`_hydrate`)
        3. Fires the configured cache signal (if any)
        4. Returns ``self``

        The ``key`` callable receives ``(param_index, column_name)`` and returns the SET
        fragment — this is what allows :meth:`update`, :meth:`add`, :meth:`append`, etc.
        to share a single code path with different SQL expressions.

        Override this method only for records with bespoke persistence semantics (e.g.
        :class:`Sentinel`, which must skip hydration to preserve a singleton + background
        task reference).
        """
        table = type(self).__tablename__
        if table is None:
            raise NotImplementedError(
                f"{type(self).__name__} is not updatable: pass `table=...` when subclassing "
                "BaseRecord, or override `_update`."
            )

        pk = type(self).__pk__
        pk_values = [getattr(self, column) for column in pk]
        set_clause = ", ".join(map(key, enumerate(values.keys(), start=len(pk) + 1)))
        where_clause = " AND ".join(f'"{column}" = ${index}' for index, column in enumerate(pk, start=1))
        query = f'UPDATE "{table}" SET {set_clause} WHERE {where_clause} RETURNING *;'

        record = await (connection or self.bot.db).fetchrow(query, *pk_values, *values.values())
        if record is not None:
            self._hydrate(record)
        signal = type(self).__changed_signal__
        if signal is not None:
            self.bot.db.signals.fire(signal, *pk_values)
        return self

    async def _apply(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Self:
        """Guards a mutation, then delegates to :meth:`_update`.

        Validates that:
        1. At least one column is being set (rejects empty updates).
        2. Every column name exists in the backing record (via :meth:`_assert_known_columns`).

        This closes the identifier-injection surface: column names are interpolated into
        the SQL statement (identifiers cannot be parameterised), so they must be validated
        against the known schema before any query is built.
        """
        if not values:
            raise ValueError(f"{type(self).__name__}.update() requires at least one column to set.")
        self._assert_known_columns(values)
        return await self._update(key, values, connection=connection)

    def _assert_known_columns(self, values: dict[str, Any]) -> None:
        """Rejects any key that is not a real column of the backing row."""
        record = self.__record
        if not record:
            return  # no authoritative row to validate against (temporary/projection instance)
        try:
            known = set(record.keys())
        except AttributeError:
            return
        unknown = sorted(column for column in values if column not in known)
        if unknown:
            raise ValueError(
                f"{type(self).__name__}: refusing to update unknown column(s) {unknown}. "
                f"Known columns: {sorted(known)}."
            )

    async def update(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Sets each column to the given value (``col = $n``)."""
        return await self._apply(lambda o: f'"{o[1]}" = ${o[0]}', values, connection=connection)

    async def add(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Increments each column by the given value (``col = col + $n``)."""
        return await self._apply(lambda o: f'"{o[1]}" = "{o[1]}" + ${o[0]}', values, connection=connection)

    async def remove(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Decrements each column by the given value (``col = col - $n``)."""
        return await self._apply(lambda o: f'"{o[1]}" = "{o[1]}" - ${o[0]}', values, connection=connection)

    async def append(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Appends the value to each array column (``col = ARRAY_APPEND(col, $n)``)."""
        return await self._apply(lambda o: f'"{o[1]}" = ARRAY_APPEND("{o[1]}", ${o[0]})', values, connection=connection)

    async def prune(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Removes the value from each array column (``col = ARRAY_REMOVE(col, $n)``)."""
        return await self._apply(lambda o: f'"{o[1]}" = ARRAY_REMOVE("{o[1]}", ${o[0]})', values, connection=connection)

    async def merge(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Self:
        """|coro| Concatenates the value onto each array column (``col = ARRAY_CAT(col, $n)``)."""
        return await self._apply(lambda o: f'"{o[1]}" = ARRAY_CAT("{o[1]}", ${o[0]})', values, connection=connection)

    async def delete(self, *, connection: asyncpg.Connection | None = None) -> None:
        """|coro| Deletes this record's row from the database.

        Builds ``DELETE FROM "<table>" WHERE <pk> = ...`` using the class-level
        ``table`` and ``pk`` declarations. Fires the ``changed_signal`` (if configured)
        so cached config getters are invalidated.

        Raises :exc:`NotImplementedError` if no ``table`` was declared.
        Subclasses may override to add cascading deletes or Discord-side cleanup
        before/after calling ``super().delete()``.
        """
        table = type(self).__tablename__
        if table is None:
            raise NotImplementedError(
                f"{type(self).__name__} is not deletable: pass `table=...` when subclassing BaseRecord."
            )
        pk = type(self).__pk__
        pk_values = [getattr(self, column) for column in pk]
        where_clause = " AND ".join(f'"{column}" = ${index}' for index, column in enumerate(pk, start=1))
        query = f'DELETE FROM "{table}" WHERE {where_clause};'
        await (connection or self.bot.db).execute(query, *pk_values)
        signal = type(self).__changed_signal__
        if signal is not None:
            self.bot.db.signals.fire(signal, *pk_values)

    async def refresh(self, *, connection: asyncpg.Connection | None = None) -> Self:
        """|coro| Re-fetches this record from the database and hydrates in place.

        Issues ``SELECT * FROM "<table>" WHERE <pk> = ...`` and calls :meth:`_hydrate`
        to update every attribute (plus :meth:`_coerce`). Useful when the row may have
        been mutated externally (e.g. by a repository method or a concurrent process)
        and you need the instance to reflect the current DB state without constructing
        a new object.

        Returns ``self`` for chaining. If the row no longer exists the instance is
        left unchanged (no error raised).
        """
        table = type(self).__tablename__
        if table is None:
            raise NotImplementedError(
                f"{type(self).__name__} is not refreshable: pass `table=...` when subclassing BaseRecord."
            )
        pk = type(self).__pk__
        pk_values = [getattr(self, column) for column in pk]
        where_clause = " AND ".join(f'"{column}" = ${index}' for index, column in enumerate(pk, start=1))
        query = f'SELECT * FROM "{table}" WHERE {where_clause};'
        record = await (connection or self.bot.db).fetchrow(query, *pk_values)
        if record is not None:
            self._hydrate(record)
        return self

    @classmethod
    def __subclasshook__(cls, subclass: type[Any]) -> bool:
        """Returns whether the subclass has a record attribute."""
        return hasattr(subclass, "__record")

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        """An iterator over the record's keys (excluding private ones)."""
        return iter(k for k in self.__record if not k.startswith("_"))

    def __repr__(self) -> str:
        args = [f"{k}={v!r}" for k, v in (self.__record.items() if self.__record else self.__dict__.items())]
        return "<{}.{}({})>".format(self.__class__.__module__, self.__class__.__name__, ", ".join(args))

    def __eq__(self, other: object) -> bool:
        """Returns whether the items base record is equal to the other item's base record."""
        if isinstance(other, self.__class__):
            return getattr(self, "__record", None) == getattr(other, "__record", None)
        return False

    def __getitem__(self, item: str) -> Any:
        """Returns the value of the item's record."""
        if not self.__record:
            raise TypeError("Cannot get item from unresolved `BaseRecord` class without a record.")
        return self.__record[item]

    def __setitem__(self, item: str, value: Any) -> None:
        """Sets the value of the item's record and the internal attributes.

        The item is only set in the class attributes if it is declared in the __slots__ attribute.
        """
        if not self.__record:
            self.__record = {}  # type: ignore # setting a 'fake' record
        self.__record[item] = value

        if item in self.__slots__:
            setattr(self, item, value)

    def __delitem__(self, item: str) -> None:
        """Deletes an item from the internal attributes. (Still available in the origin record)

        The item is only deleted from the class attributes if it is declared in the __slots__ attribute.

        Raises
        ------
        KeyError
            If the item does not exist in the record.
        """
        if item in self.__slots__:
            delattr(self, item)

    def __contains__(self, item: str) -> bool:
        """Returns whether the item's record contains the given item.

        The item must be declared in the __slots__ attribute.
        """
        return item in self.__slots__

    def __len__(self) -> int:
        """Returns the length of the item's record."""
        return len(self.__record)

    def __bool__(self) -> bool:
        """Returns whether the item has a record."""
        if self.__class__.__ignore_record__:
            return True
        return bool(self.__record)

    def __hash__(self) -> int:  # type: ignore[override]
        """Hashes on the primary key (consistent with ``__eq__``; falls back to identity)."""
        try:
            return hash((type(self).__name__, *(getattr(self, column) for column in type(self).__pk__)))
        except (AttributeError, TypeError):
            return object.__hash__(self)

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the value of the item's record."""
        return self.__record.get(key, default)

    def to_record(self) -> asyncpg.Record:
        """Returns the record of the item."""
        return self.__record

    @classmethod
    def temporary(cls, *args: Any, **kwargs: Any) -> Self:
        """Creates a temporary instance of this class with __ignore_record__ set to True."""
        return ignore_record(cls)(*args, **kwargs)  # type: ignore[return-value]


def ignore_record(cls: type[BaseRecord]) -> type[BaseRecord]:
    """A decorator that sets the __ignore_record__ attribute to True."""
    cls.__ignore_record__ = True
    return cls


class GuildConfig(BaseRecord, table="guild_config", pk="id", changed_signal="guild_config_changed"):
    """The configuration for a guild."""

    class AutoModFlags(BaseFlags):
        """The flags for the guild config settings."""

        @flag_value
        def audit_log(self) -> int:
            """Whether the server is broadcasting audit logs."""
            return 1

        @flag_value
        def raid(self) -> int:
            """Whether the server is auto banning spammers."""
            return 2

        @flag_value
        def alerts(self) -> int:
            """Whether the server has alerts enabled."""
            return 4

        @flag_value
        def sentinel(self) -> int:
            """Whether the server has sentinel enabled."""
            return 8

        @flag_value
        def mentions(self) -> int:
            """Whether the server has mention spam protection enabled."""
            return 16

    class AIFlags(BaseFlags):
        """Per-guild AI feature toggles (the AI-native rewrite). All default off.

        Stored in ``guild_config.ai_flags`` and overridable per channel via
        :class:`GuildAIConfig`. Bit names mirror the AI feature domains in docs/ai/.
        """

        @flag_value
        def assistant(self) -> int:
            """The conversational ``?ask`` assistant."""
            return 1

        @flag_value
        def router(self) -> int:
            """Natural-language command routing for unmatched messages."""
            return 2

        @flag_value
        def moderation(self) -> int:
            """AI-assisted spam/abuse verdicts feeding the existing penalty flow."""
            return 4

        @flag_value
        def sentinel(self) -> int:
            """AI screening signal for the captcha/verify gatekeeper."""
            return 8

        @flag_value
        def music(self) -> int:
            """Natural-language music intent (query/filters/presets)."""
            return 16

        @flag_value
        def polls(self) -> int:
            """Structured poll extraction from a sentence."""
            return 32

        @flag_value
        def giveaways(self) -> int:
            """Structured giveaway argument extraction."""
            return 64

        @flag_value
        def tags(self) -> int:
            """Semantic tag retrieval and drafting."""
            return 128

        @flag_value
        def reminders(self) -> int:
            """Natural-language temporal extraction for reminders."""
            return 256

    bot: Bot
    id: int
    flags: AutoModFlags
    ai_flags: AIFlags

    audit_log_channel_id: int | None
    audit_log_flags: dict[str, bool]
    audit_log_webhook_url: str | None

    poll_channel_id: int | None
    poll_ping_role_id: int | None
    poll_reason_channel_id: int | None

    mention_count: int | None
    safe_automod_entity_ids: set[int]
    muted_members: set[int]
    mute_role_id: int | None

    alert_webhook_url: str | None
    alert_channel_id: int | None

    # Added by V21 migration — accessed via getattr() until migration is applied.
    mod_log_channel_id: int | None
    message_log_channel_id: int | None
    voice_log_channel_id: int | None

    music_panel_channel_id: int | None
    music_panel_message_id: int | None
    use_music_panel: bool
    music_dj_mode: int

    prefixes: set[str]

    linked_automod_rules: set[str]

    __slots__ = (
        "_cs_alert_webhook",
        "_cs_audit_log_webhook",
        "ai_flags",
        "alert_channel_id",
        "alert_webhook_url",
        "audit_log_channel_id",
        "audit_log_flags",
        "audit_log_webhook_url",
        "bot",
        "flags",
        "id",
        "linked_automod_rules",
        "mention_count",
        "message_log_channel_id",
        "mod_log_channel_id",
        "music_dj_mode",
        "music_panel_channel_id",
        "music_panel_message_id",
        "mute_role_id",
        "muted_members",
        "poll_channel_id",
        "poll_ping_role_id",
        "poll_reason_channel_id",
        "prefixes",
        "safe_automod_entity_ids",
        "use_music_panel",
        "voice_log_channel_id",
    )

    def _coerce(self) -> None:
        self.flags = self.AutoModFlags(self.flags or 0)  # type: ignore
        # ``ai_flags`` is added by the V32 migration; tolerate its absence (getattr) so a
        # record loaded before the migration applies still coerces cleanly to 0.
        self.ai_flags = self.AIFlags(getattr(self, "ai_flags", 0) or 0)  # type: ignore
        self.safe_automod_entity_ids = set(self.safe_automod_entity_ids or [])
        self.muted_members = set(self.muted_members or [])
        self.prefixes = set(self.prefixes or [])
        self.linked_automod_rules = set(self.linked_automod_rules or [])

    @property
    def guild(self) -> discord.Guild | None:
        """:class:`discord.Guild`: The guild."""
        return self.bot.get_guild(self.id)

    @property
    def poll_channel(self) -> discord.TextChannel | discord.ForumChannel | None:
        """:class:`discord.TextChannel`: The poll channel."""
        guild = self.bot.get_guild(self.id)
        if guild and self.poll_channel_id:
            return guild.get_channel(self.poll_channel_id)  # type: ignore[return-value]
        return None

    @property
    def poll_reason_channel(self) -> discord.TextChannel | None:
        """:class:`discord.TextChannel`: The poll reason channel."""
        guild = self.bot.get_guild(self.id)
        if guild and self.poll_reason_channel_id:
            return guild.get_channel(self.poll_reason_channel_id)  # type: ignore[return-value]
        return None

    @property
    def music_panel_channel(self) -> discord.TextChannel | None:
        """:class:`discord.TextChannel`: The music panel channel."""
        guild = self.bot.get_guild(self.id)
        if guild and self.music_panel_channel_id:
            return guild.get_channel(self.music_panel_channel_id)  # type: ignore[return-value]
        return None

    @discord.utils.cached_slot_property("_cs_audit_log_webhook")
    def audit_log_webhook(self) -> discord.Webhook | None:
        """:class:`discord.Webhook`: The audit log webhook."""
        if self.audit_log_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.audit_log_webhook_url, session=self.bot.session, client=self.bot)

    @discord.utils.cached_slot_property("_cs_alert_webhook")
    def alert_webhook(self) -> discord.Webhook | None:
        """:class:`discord.Webhook`: The alert webhook."""
        if self.alert_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.alert_webhook_url, session=self.bot.session, client=self.bot)

    @property
    def mute_role(self) -> discord.Role | None:
        """:class:`discord.Role`: The mute role."""
        guild = self.bot.get_guild(self.id)
        if guild and self.mute_role_id:
            return guild.get_role(self.mute_role_id)  # type: ignore[return-value]
        return None

    async def fetch_automod_rules(self) -> list[discord.AutoModRule]:
        """|coro|

        Fetches all linked automod rules from the guild that were created using the bot.

        Returns
        -------
        list[:class:`discord.AutoModRule`]
            The linked automod rules.
        """
        if not self.linked_automod_rules or self.guild is None:
            return []

        guild_rules = await self.guild.fetch_automod_rules()
        # ``linked_automod_rules`` stores the bot's preset *names* (see the AutoMod cog),
        # so match on name rather than id.
        return [rule for rule in guild_rules if rule.name in self.linked_automod_rules]

    def is_muted(self, member: discord.abc.Snowflake) -> bool:
        """Checks if a member is muted.

        Parameters
        ----------
        member : :class:`discord.abc.Snowflake`
            The member to check.

        Returns
        -------
        :class:`bool`
            Whether the member is muted.
        """
        return member.id in self.muted_members

    async def apply_mute(self, member: discord.Member, reason: str | None) -> None:
        """|coro|

        Applies the mute role to a member.

        Parameters
        ----------
        member : :class:`discord.Member`
            The member to mute.
        reason : :class:`str`
            The reason for the mute.
        """
        if self.mute_role_id:
            await member.add_roles(discord.Object(id=self.mute_role_id), reason=reason)

    async def send_alert(self, content: str = MISSING, *, force: bool = False, **kwargs: Any) -> discord.Message | None:
        """|coro|

        Sends an alert to the alert webhook if enabled or the system channel
        if not and the `force` parameter is set to `True`.

        Parameters
        ----------
        content : :class:`str`
            The content of the alert.
        force : :class:`bool`
            Whether to force the alert to be sent to the system channel
            if the alert webhook is disabled or not found. Defaults to `False`.
        kwargs : Any
            The keyword arguments to pass to the webhook send method.
        """
        alerts_available = self.flags.alerts and self.alert_webhook
        if not alerts_available and not force:
            return

        if content is not MISSING and not content.startswith("<:"):
            content = f"{Emojis.info} {content}"

        try:
            if not alerts_available and force:
                # Send to the system channel if alerts are disabled
                guild = self.bot.get_guild(self.id)
                if guild is None or guild.system_channel is None:
                    return None
                return await guild.system_channel.send(content, **kwargs)
            if self.alert_webhook is None:
                return None
            return await self.alert_webhook.send(content, **kwargs)
        except discord.HTTPException:
            return None


@dataclass(slots=True)
class GuildAIConfig:
    """Resolved AI configuration for a guild: server-wide flags + per-channel overrides.

    Built by the cached :meth:`Database.get_guild_ai_config`. Cogs ask it whether a given
    AI feature is enabled (optionally in a specific channel); a per-channel override wins
    over the server default, otherwise the channel inherits it.
    """

    guild_id: int
    flags: GuildConfig.AIFlags
    #: channel_id -> (flags_mask, enabled_mask). ``flags_mask`` marks which bits the
    #: channel overrides; ``enabled_mask`` holds their on/off value.
    overrides: dict[int, tuple[int, int]]

    def effective(self, channel_id: int | None = None) -> GuildConfig.AIFlags:
        """Return the effective flags, applying any override for ``channel_id``."""
        value = self.flags.value
        if channel_id is not None and channel_id in self.overrides:
            mask, enabled = self.overrides[channel_id]
            value = (value & ~mask) | (enabled & mask)
        return GuildConfig.AIFlags(value)

    def is_enabled(self, feature: str, channel_id: int | None = None) -> bool:
        """Whether an AI ``feature`` (an :class:`GuildConfig.AIFlags` name) is on here."""
        return bool(getattr(self.effective(channel_id), feature))


class UserConfig(BaseRecord, table="user_settings", pk="id", changed_signal="user_config_changed"):
    bot: Bot
    id: int
    timezone: str

    track_presence: bool
    track_history: bool

    __slots__ = ("bot", "id", "timezone", "track_history", "track_presence")

    @property
    def tzinfo(self) -> datetime.tzinfo:
        if self.timezone is None:
            return datetime.UTC
        return dateutil.tz.gettz(self.timezone) or datetime.UTC


class Sentinel(BaseRecord, table="guild_sentinel", pk="id"):
    """A sentinel (Captcha-Verify-System) that prevents users from participating
    in the server until certain conditions are met.

    This is currently implemented as the user must solve a generated captcha image of six random characters.

    Attributes
    ----------
    id : int
        The ID of the guild.
    started_at : datetime.datetime | None
        The time when the sentinel was started.
    role_id : int | None
        The role ID to add to members.
    starter_role_id : int | None
        The role ID to add to members that bypass the sentinel.
    channel_id : int | None
        The channel ID where the sentinel is active.
    message_id : int | None
        The message ID that the sentinel is using.
    bypass_action : Literal['ban', 'kick']
        The action to take when someone bypasses the sentinel.
    rate : tuple[int, int] | None
        The rate limit for joining the server.
    members : set[int]
        The members that have the role and are pending to be verified.
    task : asyncio.Task
        The task that adds and removes the role from members.
    queue : CancellableQueue[int, tuple[int, SentinelRoleState]]
        The queue that is being processed in the background.

    Behavior Overview
    ------------------
    - Sentinel.members
        This is a set of members that have the role and are pending to
        receive the role. Anyone in this set is technically being gatekept.
        If they talk in any channel while technically gatekept then they
        should get autobanned/autokicked.

        If the sentinel is disabled, then this list should be cleared,
        probably one by one during clean-up.
    - Sentinel.started_at is None
        This signals that the sentinel is fully disabled.
        If this is true, then all members should lose their role
        and the table **should not** be cleared.

        There is a special case where this is true, but there
        are still members. In this case, clean up should resume.
    - Sentinel.started_at is not None
        This one's simple, the sentinel is fully operational
        and serving captchas and adding roles.
    """

    class SentinelRoleState(enum.Enum):
        """The state of a member in the sentinel."""

        added = "added"
        pending_add = "pending_add"
        pending_remove = "pending_remove"

    log = logging.getLogger("mod")

    bot: Bot
    id: int
    started_at: datetime.datetime | None
    channel_id: int | None
    role_id: int | None
    starter_role_id: int | None
    message_id: int | None
    bypass_action: Literal["ban", "kick"]
    rate: tuple[int, int] | str | None

    __slots__ = (
        "__stop_event",
        "bot",
        "bypass_action",
        "channel_id",
        "id",
        "members",
        "message_id",
        "queue",
        "rate",
        "role_id",
        "started_at",
        "starter_role_id",
        "task",
    )

    def __init__(self, members: list[Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.members: set[int] = {r["user_id"] for r in members if r["state"] == "added"}

        # This event is used to stop the task because we can't
        # cancel the task gracefully without stopping the internal loop
        self.__stop_event: asyncio.Event = asyncio.Event()
        self.task: asyncio.Task = asyncio.create_task(self.role_loop())
        self.task._log_destroy_pending = False  # type: ignore[attr-defined]

        self.log.debug("Sentinel %r has started.", self.id)

        self.queue: CancellableQueue[int, tuple[int, Sentinel.SentinelRoleState]] = CancellableQueue(
            hook_check=self.__stop_event.is_set
        )

        for member in members:
            state = self.SentinelRoleState(member["state"])
            member_id = member["user_id"]
            if state is not self.SentinelRoleState.added:
                self.queue.put(member_id, (member_id, state))

    def _coerce(self) -> None:
        if isinstance(self.rate, str):
            rate, per = self.rate.split("/")
            self.rate = (int(rate), int(per))
        if self.started_at is not None and self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=datetime.UTC)

    def __repr__(self) -> str:
        attrs = [
            ("id", self.id),
            ("members", len(self.members)),
            ("started_at", self.started_at),
            ("role_id", self.role_id),
            ("starter_role_id", self.starter_role_id),
            ("channel_id", self.channel_id),
            ("message_id", self.message_id),
            ("bypass_action", self.bypass_action),
            ("rate", self.rate),
        ]
        joined = " ".join("{}={!r}".format(*t) for t in attrs)
        return f"<{self.__class__.__name__} {joined}>"

    @property
    def status(self) -> str:
        """The status of the sentinel."""
        headers = [
            ("Blocked Members", f"**{len(self.members)}**"),
            ("Enabled", discord.utils.format_dt(self.started_at) if self.started_at is not None else "False"),
            ("Role", self.role.mention if self.role is not None else "N/A"),
            ("Starter Role", self.starter_role.mention if self.starter_role is not None else "N/A"),
            ("Channel", self.channel.mention if self.channel is not None else "N/A"),
            ("Message", self.message.jump_url if self.message is not None else "N/A"),
            ("Bypass Action", self.bypass_action.title()),
            ("Auto Trigger", f"{self.rate[0]}/{self.rate[1]}s" if self.rate is not None else "N/A"),
        ]
        return "\n".join(f"{header}: {value}" for header, value in headers)

    def cancel_task(self) -> None:
        """Cancels the task."""
        self.__stop_event.set()
        try:
            self.task.cancel()
        except (TimeoutError, asyncio.CancelledError):
            pass
        finally:
            self.__stop_event.clear()

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Self:
        """Persists the mutation but deliberately does NOT hydrate or fire a signal.

        ``self`` is the cached sentinel returned by ``get_guild_sentinel`` — editing in
        place keeps the cache, the open setup menu, and the background role loop coherent.
        Constructing a new instance would spawn a second ``role_loop`` task on every edit,
        and firing ``sentinel_changed`` would cancel the task ``disable()`` relies on.
        Raw-SQL mutations (the dashboard) still fire the signal to force a rebuild.
        """
        table = type(self).__tablename__
        pk = type(self).__pk__
        pk_values = [getattr(self, column) for column in pk]
        set_clause = ", ".join(map(key, enumerate(values.keys(), start=len(pk) + 1)))
        where_clause = " AND ".join(f'"{column}" = ${index}' for index, column in enumerate(pk, start=1))
        query = f'UPDATE "{table}" SET {set_clause} WHERE {where_clause};'
        await (connection or self.bot.db).execute(query, *pk_values, *values.values())
        return self

    async def edit(
        self,
        *,
        started_at: datetime.datetime | None = MISSING,
        role_id: int | None = MISSING,
        starter_role_id: int | None = MISSING,
        channel_id: int | None = MISSING,
        message_id: int | None = MISSING,
        bypass_action: Literal["ban", "kick"] = MISSING,
        rate: tuple[int, int] | None = MISSING,
    ) -> None:
        """|coro|

        Edits the sentinel.

        Parameters
        ----------
        started_at : datetime.datetime | None
            The time when the sentinel was started.
        role_id : int | None
            The role ID to add to members.
        starter_role_id : int | None
            The role ID to add to members that bypass the sentinel.
        channel_id : int | None
            The channel ID where the sentinel is active.
        message_id : int | None
            The message ID that the sentinel is using.
        bypass_action : Literal['ban', 'kick']
            The action to take when someone bypasses the sentinel.
        rate : tuple[int, int] | None
            The rate limit for joining the server.
        """
        form: dict[str, Any] = {}

        if role_id is None or channel_id is None or message_id is None:
            started_at = None

        if started_at is not MISSING:
            form["started_at"] = started_at
        if role_id is not MISSING:
            form["role_id"] = role_id
        if starter_role_id is not MISSING:
            form["starter_role_id"] = starter_role_id
        if channel_id is not MISSING:
            form["channel_id"] = channel_id
        if message_id is not MISSING:
            form["message_id"] = message_id
        if bypass_action is not MISSING:
            form["bypass_action"] = bypass_action
        if rate is not MISSING:
            form["rate"] = "/".join(map(str, rate)) if rate is not None else None

        await self.update(**form)

        if role_id is not MISSING:
            await self.bot.db.guilds.delete_sentinel_members(self.id)

            self.members.clear()
            self.queue.cancel_all()
            self.cancel_task()
            self.role_id = role_id
            self.task = asyncio.create_task(self.role_loop())

        if started_at is not MISSING:
            self.started_at = started_at
        if starter_role_id is not MISSING:
            self.starter_role_id = starter_role_id
        if channel_id is not MISSING:
            self.channel_id = channel_id
        if message_id is not MISSING:
            self.message_id = message_id
        if bypass_action is not MISSING:
            self.bypass_action = bypass_action
        if rate is not MISSING:
            self.rate = rate

    async def role_loop(self) -> None:
        """|coro|

        The main loop that adds and removes the role from members.

        This is a bit of a weird loop because it's not really a loop.
        It's more of a queue that's being processed in the background.
        """
        while self.role_id is not None and not self.__stop_event.is_set():
            role_id = self.role_id  # capture for narrowing inside the loop
            member_id, action = await self.queue.get()

            try:
                if action is self.SentinelRoleState.pending_remove:
                    await self.bot.http.remove_role(self.id, member_id, role_id, reason="Completed Sentinel verification")
                    await self.bot.db.guilds.delete_sentinel_member(self.id, member_id)

                    if self.starter_role:
                        await self.bot.http.add_role(
                            self.id, member_id, self.starter_role_id, reason="Completed Sentinel verification"
                        )  # type: ignore[arg-type]
                elif action is self.SentinelRoleState.pending_add:
                    await self.bot.http.add_role(self.id, member_id, role_id, reason="Started Sentinel verification")
                    await self.bot.db.guilds.update_sentinel_member_state(self.id, member_id, "added")
            except discord.DiscordServerError:
                self.queue.put(member_id, (member_id, action))
            except discord.NotFound as e:
                if e.code not in (10011, 10013):
                    break
                if e.code == 10011:
                    # Unknown role, disable the sentinel.
                    config = await self.bot.db.get_guild_config(self.id)  # type: ignore
                    await config.send_alert(
                        "A Role you've set up for the sentinel was not found, please review! Disabling the sentinel."
                    )
                    needs_migration = {}
                    if self.role is None:
                        needs_migration["role_id"] = None
                    if self.starter_role is None:
                        needs_migration["starter_role_id"] = None
                    await self.edit(started_at=None, **needs_migration)
                    break
                continue
            except Exception:
                self.log.exception("[Sentinel] An exception happened in the role loop of guild ID %d", self.id)
                continue

    async def cleanup_loop(self, members: set[int]) -> None:
        """|coro|

        A loop that cleans up the members that are no longer in the guild.
        Potentially this could be a bit of a performance hog, but it is what it is.

        Parameters
        ----------
        members : set[int]
            The members that are currently in the guild.
        """
        if self.role_id is None:
            return

        for member_id in members:
            try:
                await self.bot.http.remove_role(self.id, member_id, self.role_id)
            except discord.HTTPException as e:
                if e.code not in (10011, 10013):
                    break
                if e.code == 10011:
                    await self.edit(role_id=None)
                    break
                continue
            except Exception as exc:
                self.log.exception(
                    "[Sentinel] An exception happened in the role cleanup loop of guild ID %d: %r", self.id, exc
                )

    @property
    def pending_members(self) -> int:
        """The number of members that are pending to receive the role."""
        return len(self.members)

    async def enable(self) -> None:
        """|coro|

        Enables the sentinel.
        This will set the started_at field to the current time.
        """
        await self.edit(started_at=discord.utils.utcnow())

    async def disable(self) -> None:
        """|coro|

        Disables the sentinel.
        This will remove the role from all members and clear the queue.
        """
        await self.edit(started_at=None)

        async with self.bot.db.acquire(timeout=300.0) as conn, conn.transaction():
            await self.bot.db.guilds.bulk_update_sentinel_member_state(self.id, "added", "pending_remove", connection=conn)
            for member_id in self.members:
                self.queue.put(member_id, (member_id, self.SentinelRoleState.pending_remove))
            self.members.clear()

    @property
    def role(self) -> discord.Role | None:
        """The role that is being added to members."""
        guild = self.bot.get_guild(self.id)
        if guild and self.role_id:
            return guild.get_role(self.role_id)  # type: ignore[return-value]
        return None

    @property
    def starter_role(self) -> discord.Role | None:
        """The role that is being added to members that bypass the sentinel."""
        guild = self.bot.get_guild(self.id)
        if guild and self.starter_role_id:
            return guild.get_role(self.starter_role_id)  # type: ignore[return-value]
        return None

    @property
    def channel(self) -> discord.TextChannel | None:
        """The channel where the sentinel is active."""
        guild = self.bot.get_guild(self.id)
        if guild and self.channel_id:
            return guild.get_channel(self.channel_id)  # type: ignore[return-value]
        return None

    @property
    def message(self) -> discord.PartialMessage | None:
        """The message that the sentinel is using."""
        if self.channel_id is None or self.message_id is None:
            return None

        channel = self.bot.get_partial_messageable(self.channel_id)
        return channel.get_partial_message(self.message_id)

    @property
    def requires_setup(self) -> bool:
        """Whether the sentinel requires setup."""
        return self.role_id is None or self.channel_id is None or self.message_id is None

    def is_blocked(self, user_id: int, /) -> bool:
        """Whether the user is blocked from participating in the server."""
        return user_id in self.members

    def has_role(self, member: discord.Member, /) -> bool:
        """Checks if a user has the sentinel role."""
        return self.role_id is not None and member._roles.has(self.role_id)

    def is_bypassing(self, member: discord.Member) -> bool:
        """Whether the member is bypassing the sentinel."""
        if self.started_at is None:
            return False
        if member.joined_at is None:
            return False

        return member.joined_at >= self.started_at and self.is_blocked(member.id)

    async def block(self, member: discord.Member) -> None:
        """|coro|

        Blocks the member from participating in the server.
        This will add the member to the queue and the members set.

        Parameters
        ----------
        member : discord.Member
            The member to block.
        """
        self.members.add(member.id)
        await self.bot.db.guilds.insert_sentinel_member(self.id, member.id)
        self.queue.put(member.id, (member.id, self.SentinelRoleState.pending_add))

    async def force_enable_with(self, members: Sequence[discord.Member]) -> None:
        """|coro|

        Forces the sentinel to enable with the given members.
        This will add the members to the queue and the members set.

        Parameters
        ----------
        members : Sequence[discord.Member]
            The members to block.
        """
        self.members.update(m.id for m in members)
        await self.edit(started_at=discord.utils.utcnow())
        await self.bot.db.guilds.insert_sentinel_members_bulk(self.id, [m.id for m in members])

        for member in members:
            self.queue.put(member.id, (member.id, self.SentinelRoleState.pending_add))

    async def unblock(self, member: discord.Member) -> None:
        """|coro|

        Unblocks the member from participating in the server.
        This will remove the member from the queue and the members set.

        Parameters
        ----------
        member : discord.Member
            The member to unblock.
        """
        self.members.discard(member.id)
        if self.queue.is_pending(member.id):
            await self.bot.db.guilds.delete_sentinel_member(self.id, member.id)
            self.queue.cancel(member.id)
        else:
            await self.bot.db.guilds.update_sentinel_member_state(self.id, member.id, "pending_remove")
            self.queue.put(member.id, (member.id, self.SentinelRoleState.pending_remove))


class Balance(BaseRecord, table="economy", pk=("user_id", "guild_id")):
    """Represents a user's balance"""

    bot: Bot
    user_id: int
    guild_id: int
    cash: int
    bank: int

    __slots__ = ('bank', 'bot', 'cash', 'guild_id', 'user_id')

    @property
    def total(self) -> int:
        """Gets the total amount of money a user has"""
        return self.cash + self.bank
