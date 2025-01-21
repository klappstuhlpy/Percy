from __future__ import annotations

import asyncio
import datetime
import logging

import asyncpg
from typing import (
    Any,
    Callable,
    ClassVar,
    Concatenate,
    Literal,
    ParamSpec,
    TYPE_CHECKING,
    TypeVar,
    Sequence, override
)

import discord
from discord.utils import format_dt, utcnow

from app.database import Database, BaseRecord

if TYPE_CHECKING:
    from . import Bot

    P = ParamSpec('P')
    TimerT = TypeVar('TimerT', bound='Timer')

log = logging.getLogger(__name__)


class Timer(BaseRecord):
    """A timer that will fire at a given time and send a message to a given channel."""

    bot: Bot
    id: int
    event: str
    created: datetime.datetime
    expires: datetime.datetime
    timezone: str
    metadata: dict[str, Any]

    __slots__ = ('bot', 'id', 'args', 'kwargs', 'metadata', 'event', 'created', 'expires', 'timezone')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.args: Sequence[Any] = self.metadata.get('args', [])
        self.kwargs: dict[str, Any] = self.metadata.get('kwargs', {})

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ):
        """|coro|

        Updates the timer in the database.

        Parameters
        -----------
        key: Callable[[tuple[int, str]], str]
            The key to use for the update query.
        values: dict[str, Any]
            The values to update.
        connection: :class:`asyncpg.Connection` | None
            The connection to use for the query.
        """
        query = f"""
            UPDATE timers
            SET {', '.join(f"{key((i, k))} = ${i + 1}" for i, k in enumerate(values))}
            WHERE id = $1;
        """
        record = await (connection or self.bot.db).execute(query, self.id, *values.values())
        return self.__class__(bot=self.bot, record=record)

    async def rerun(
            self,
            when: datetime.datetime | datetime.timedelta,
            /,
            *args: Any,
            **kwargs: Any
    ):
        """|coro|

        Reruns a timer with a new expiry time.
        You can override the `timer`s kwargs and args with the new ones provided.

        Parameters
        -----------
        when: datetime.datetime | datetime.timedelta
            When the timer should fire.
        \*args
            Arguments to pass to the event
        \*\*kwargs
            Keyword arguments to pass to the event

            .. special keyword-only arguments::

                created: datetime.datetime
                    Special keyword-only argument to use as the creation time.
                timezone: str
                    Special keyword-only argument to use as the timezone for the
                    expiry time. This automatically adjusts the expiry time to be
                    in the future, should it be in the past.

        Note
        ----
        Arguments and keyword arguments must be JSON serializable.

        Returns
        --------
        :class:`Timer`
            The timer that was rerun.
        """
        new_kwargs = self.metadata['kwargs'].copy()
        new_kwargs.update(kwargs)

        new_args = self.metadata['args'] + args

        return await self.bot.timers.create(
            when,
            self.event,
            *new_args,
            **new_kwargs
        )

    @property
    def is_short_dispatch(self) -> bool:
        """Returns whether this timer should be dispatched as a short timer."""
        return self.id < 0

    def human_delta(self, spec: Literal['f', 'F', 'd', 'D', 't', 'T', 'R'] = 'R') -> str:
        """Return this timer formatted as <t:expires:spec>."""
        return format_dt(self.expires, spec)

    @override
    def get(self, key: str, default: Any = None) -> Any:
        """Gets a value from the metadata."""
        return self.kwargs.get(key, default)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            try:
                return self.args[key]
            except IndexError:
                return None
        return self.get(key)

    @classmethod
    def from_timer(cls, timer: Timer) -> TimerT:
        """Creates a new :class:`Timer` from the original base class :class:`Timer`."""
        if cls is Timer:
            raise TypeError("cannot create 'Timer' instances")

        return cls(bot=timer.bot, record=timer.to_record())

    def __repr__(self) -> str:
        return f'<Timer id={self.id} event={self.event!r} expires={self.expires!r}>'

    def __eq__(self: TimerT, other: TimerT) -> bool:
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


class TimerManager:
    """Manages all timers and dispatches them when they expire."""

    SHORT_TIMER_THRESHOLD: ClassVar[int] = 60
    MAX_DAYS: ClassVar[int] = 40

    def __init__(self, bot: Bot):
        self.bot = bot
        self.db: Database = bot.db
        self._dispatch: Callable[Concatenate[str, P], None] = bot.dispatch
        self._loop = bot.loop

        self._loaded_timer: Timer | None = None
        self._short_timers: dict[int, Timer] = {}

        self.__event = asyncio.Event()
        self.__task = bot.loop.create_task(self.dispatch_timers())

        self.__short_timer_key_buffer: int = 0
        self.__short_timer_key_mutex: asyncio.Lock = asyncio.Lock()

    async def fetch(self, event: str, /, **kwargs: Any) -> Timer | None:
        r"""|coro|

        Gets a timer from the database.
        Note: you cannot find a timer by its expiry or creation time.

        This should rarely be used due to being the base class of the a timer instance.

        Parameters
        -----------
        event: str
            The name of the event to search for.
        \*\*kwargs
            Keyword arguments to search for in the database.

        Returns
        --------
        :class:`Timer`
            The timer if found, otherwise None.
        """
        if not kwargs:
            raise ValueError('You must provide at least one keyword argument.')

        filter_clause = [f"metadata #>> ARRAY['kwargs', '{key}'] = ${i}" for i, key in enumerate(kwargs.keys(), start=2)]
        query = f"SELECT * FROM timers WHERE event = $1 AND {' AND '.join(filter_clause)} LIMIT 1;"
        record = await self.bot.db.fetchrow(query, event, *map(str, kwargs.values()))
        return Timer(bot=self.bot, record=record) if record else None

    async def delete(self, event: str, /, **kwargs: Any) -> None:
        r"""|coro|

        Deletes a timer from the database.
        Note you cannot find a database by its expiry or creation timetools.

        Parameters
        -----------
        event: str
            The name of the event to search for.
        \*\*kwargs
            Keyword arguments to search for in the database.
        """
        if not kwargs:
            raise ValueError('You must provide at least one keyword argument.')

        filter_clause = [f"metadata #>> ARRAY['kwargs', '{key}'] = ${i}" for i, key in enumerate(kwargs.keys(), start=2)]
        query = f"DELETE FROM timers WHERE event = $1 AND {' AND '.join(filter_clause)} RETURNING id;"
        timer_id = await self.bot.db.fetchval(query, event, *map(str, kwargs.values()))

        if timer_id is not None and self._loaded_timer and self._loaded_timer.id == timer_id:
            self.reset_task()

    async def call(self, timer: Timer) -> None:
        """|coro|

        Dispatches the specified event of the given :class:`Timer`.

        Parameters
        -----------
        timer: :class:`Timer`
            The timer to dispatch.
        """
        if timer.is_short_dispatch:
            del self._short_timers[timer.id]
        else:
            await self.db.execute("DELETE FROM timers WHERE id = $1;", timer.id)

        event_name = f'{timer.event}_timer_complete'
        self._dispatch(event_name, timer)

    async def create(
            self,
            when: datetime.datetime | datetime.timedelta,
            event: str,
            /,
            *args: Any,
            **kwargs: Any
    ) -> Timer:
        r"""|coro|

        Creates a timer to be dispatched at a given time.

        Parameters
        -----------
        when: datetime.datetime | datetime.timedelta
            When the timer should fire.
        event: str
            The name of the event to trigger.
            Will transform to 'on_{event}_timer_complete'.
        \*args
            Arguments to pass to the event
        \*\*kwargs
            Keyword arguments to pass to the event

            .. special keyword-only arguments::

                created: datetime.datetime
                    Special keyword-only argument to use as the creation time.
                timezone: str
                    Special keyword-only argument to use as the timezone for the
                    expiry time. This automatically adjusts the expiry time to be
                    in the future, should it be in the past.

        Note
        ----
        Arguments and keyword arguments must be JSON serializable.

        Returns
        --------
        :class:`Timer`
            The timer that was created.
        """
        now = kwargs.pop('created', discord.utils.utcnow())
        tz = kwargs.pop('timezone', 'UTC')
        if tz.__class__.__name__ == 'TimeZone':
            tz = getattr(tz, 'key', 'UTC')

        if isinstance(when, datetime.timedelta):
            when = now + when

        when = when.astimezone(datetime.UTC).replace(tzinfo=None)
        now = now.astimezone(datetime.UTC).replace(tzinfo=None)

        timer = Timer.temporary(
            bot=self.bot,
            id=0,  # temporary id
            event=event,
            expires=when,
            created=now,
            timezone=tz,
            metadata={'args': args, 'kwargs': kwargs}
        )

        seconds = (when - now).total_seconds()
        if seconds <= self.SHORT_TIMER_THRESHOLD:  # dont want delta to be negative
            timer.id = key = await self.decrement_atomic_key()
            self._short_timers[key] = timer

            self._loop.create_task(self.start_short_timer(seconds, timer))  # noqa
            log.debug(f'Short timer {timer.id} will fire in {seconds} seconds.')
            return timer

        query = """
            INSERT INTO timers (event, metadata, expires, created, timezone)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            RETURNING id;
        """
        timer.id = await self.bot.db.fetchval(query, event, {'args': args, 'kwargs': kwargs}, when, now, tz)

        if seconds <= self.MAX_DAYS * 86400:
            self.__event.set()

        if self._loaded_timer and when < self._loaded_timer.expires:
            self.reset_task()

        log.debug(f'Timer {timer.id} will fire at {when}.')
        return timer

    async def decrement_atomic_key(self) -> int:
        """Decrements the atomic key buffer used to store short timers atomically."""
        async with self.__short_timer_key_mutex:
            self.__short_timer_key_buffer -= 1
            return self.__short_timer_key_buffer

    # DISPATCHING

    async def load_next_timer(
            self, *, connection: asyncpg.Connection | None = None, days: int = 7
    ) -> Timer | None:
        """|coro|

        Loads the next timer to be dispatched.

        Parameters
        -----------
        connection: :class:`asyncpg.Connection`
            The connection to use for the query.
        days: int
            The amount of days to look for the next timer.
        """
        query = """
            SELECT *
            FROM timers
            WHERE (expires AT TIME ZONE timezone) < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY expires
            LIMIT 1;
        """
        record = await (connection or self.db).fetchrow(query, datetime.timedelta(days=days))
        return Timer(bot=self.bot, record=record) if record else None

    async def wait(
            self, *, connection: asyncpg.Connection | None = None, days: int = 7
    ) -> Timer:
        """|coro|

        Waits for the next timer to be dispatched.

        Parameters
        -----------
        connection: :class:`asyncpg.Connection`
            The connection to use for the query.
        days: int
            The amount of days to look for the next timer.

        Returns
        --------
        :class:`Timer`
            The timer that was loaded.
        """
        async with (connection or self.db).acquire(timeout=500.0) as con:
            timer = await self.load_next_timer(connection=con, days=days)
            if timer is not None:
                log.debug(f'Loaded timer %r to fire at %s.', timer.id, timer.expires)
                self.__event.set()
                return timer

            self.__event.clear()
            self._loaded_timer = None
            log.debug('No timers to load, waiting for new timers to be created.')
            await self.__event.wait()

            return await self.load_next_timer(connection=con, days=days)  # type: ignore

    async def start_short_timer(self, seconds: float, timer: Timer) -> None:
        """Simply sleeps until the timer expires."""
        await asyncio.sleep(seconds)
        await self.call(timer)

    async def dispatch_timers(self) -> None:
        """|coro|

        Dispatches the timers when they are ready.

        Raises
        -------
        asyncio.CancelledError
            The task was cancelled.
        """
        await self.db.wait()

        try:
            while not self.bot.is_closed():
                timer = self._loaded_timer = await self.wait(days=self.MAX_DAYS)
                now = utcnow()

                if timer.expires.replace(tzinfo=datetime.UTC) >= now:
                    to_sleep = (timer.expires - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                log.debug(f'Dispatching timer %r for event %s now.', timer.id, timer.event)
                await self.call(timer)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self.reset_task()

    def reset_task(self) -> None:
        self.__task.cancel()
        self.__task = self.bot.loop.create_task(self.dispatch_timers())
