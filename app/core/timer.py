from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, Literal, ParamSpec, TypeVar, override

import asyncpg
import discord
from discord.utils import format_dt, utcnow

from app.database import BaseRecord, Database

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from . import Bot

    P = ParamSpec("P")
    TimerT = TypeVar("TimerT", bound="Timer")

log = logging.getLogger(__name__)


class Timer(BaseRecord, table="timers", pk="id"):
    """A timer that will fire at a given time and send a message to a given channel."""

    bot: Bot
    id: int
    event: str
    created: datetime.datetime
    expires: datetime.datetime
    timezone: str
    metadata: dict[str, Any]

    __slots__ = ("args", "bot", "created", "event", "expires", "id", "kwargs", "metadata", "timezone")

    def _coerce(self) -> None:
        self.args: Sequence[Any] = self.metadata.get("args", [])
        self.kwargs: dict[str, Any] = self.metadata.get("kwargs", {})

    async def rerun(  # noqa: ANN201
        self, when: datetime.datetime | datetime.timedelta, /, *args: Any, **kwargs: Any
    ):
        r"""|coro|

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
        new_kwargs = self.metadata["kwargs"].copy()
        new_kwargs.update(kwargs)

        new_args = self.metadata["args"] + args

        return await self.bot.timers.create(when, self.event, *new_args, **new_kwargs)

    @property
    def is_short_dispatch(self) -> bool:
        """Returns whether this timer should be dispatched as a short timer."""
        return self.id < 0

    def human_delta(self, spec: Literal["f", "F", "d", "D", "t", "T", "R"] = "R") -> str:
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
        return f"<Timer id={self.id} event={self.event!r} expires={self.expires!r}>"

    def __eq__(self: TimerT, other: TimerT) -> bool:
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


class TimerManager:
    """Manages all timers and dispatches them when they expire."""

    SHORT_TIMER_THRESHOLD: ClassVar[int] = 60
    MAX_DAYS: ClassVar[int] = 40

    def __init__(self, bot: Bot) -> None:
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
            raise ValueError("You must provide at least one keyword argument.")

        record = await self.bot.db.timers.fetch_by_kwargs(event, kwargs)
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
            raise ValueError("You must provide at least one keyword argument.")

        timer_id = await self.bot.db.timers.delete_by_kwargs(event, kwargs)

        if timer_id is not None and self._loaded_timer and self._loaded_timer.id == timer_id:
            self.reset_task()

    async def fetch_member_timer(self, event: str, guild_id: int, member_id: int) -> Timer | None:
        """|coro|

        Fetches an active member-scoped moderation timer (e.g. ``tempmute``/``tempban``).

        These timers store ``[guild_id, mod_id, member_id, ...]`` positionally in ``args``,
        so they cannot be located through :meth:`fetch` (which matches ``kwargs``).
        """
        record = await self.bot.db.timers.fetch_member_timer(event, guild_id, member_id)
        return Timer(bot=self.bot, record=record) if record else None

    async def delete_member_timer(self, event: str, guild_id: int, member_id: int) -> bool:
        """|coro|

        Deletes every member-scoped moderation timer of ``event`` for ``member_id`` (persisted
        and sub-minute in-memory ones), resetting the dispatch loop if a cancelled timer was the
        next one due. Returns whether a timer was removed.

        This is the one place a ban, unban or mute cancels the expiry of an older one, so a
        stale ``tempban``/``tempmute`` can never fire later and undo the newer action.
        """
        timer_ids = await self.bot.db.timers.delete_member_timer(event, guild_id, member_id)
        if self._loaded_timer and self._loaded_timer.id in timer_ids:
            self.reset_task()

        short = [
            key for key, timer in self._short_timers.items()
            if timer.event == event and timer[0] == guild_id and timer[2] == member_id
        ]
        for key in short:
            del self._short_timers[key]
        return bool(timer_ids or short)

    async def drop_lockdown_channels(self, guild_id: int, channel_ids: list[int]) -> None:
        """|coro|

        Takes channels whose lockdown has ended out of every pending ``lockdown`` timer of the
        guild (persisted and sub-minute in-memory ones), deleting a timer left with none. A
        timer must only ever end the lockdown it started, never a later re-lock of a channel.
        """
        if not channel_ids:
            return
        timer_ids = await self.bot.db.timers.drop_lockdown_channels(guild_id, channel_ids)
        # The loaded timer holds its args in memory, so a narrowed one must be reloaded too.
        if self._loaded_timer and self._loaded_timer.id in timer_ids:
            self.reset_task()

        ended = set(channel_ids)
        for key, timer in list(self._short_timers.items()):
            if timer.event != "lockdown" or timer[0] != guild_id:
                continue
            rest = [c for c in timer[3] if c not in ended]
            if rest:
                timer.args = [*timer.args[:3], rest]
            else:
                del self._short_timers[key]

    async def call(self, timer: Timer) -> None:
        """|coro|

        Dispatches the specified event of the given :class:`Timer`.

        Parameters
        -----------
        timer: :class:`Timer`
            The timer to dispatch.
        """
        if timer.is_short_dispatch:
            # A cancelled short timer is already gone from the map; its sleeping task must not fire it.
            if self._short_timers.pop(timer.id, None) is None:
                return
        else:
            await self.db.timers.delete_timer(timer.id)

        event_name = f"{timer.event}_timer_complete"
        self._dispatch(event_name, timer)

    async def create(self, when: datetime.datetime | datetime.timedelta, event: str, /, *args: Any, **kwargs: Any) -> Timer:
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
        now = kwargs.pop("created", discord.utils.utcnow())
        tz = kwargs.pop("timezone", "UTC")
        if tz.__class__.__name__ == "TimeZone":
            tz = getattr(tz, "key", "UTC")

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
            metadata={"args": args, "kwargs": kwargs},
        )

        seconds = (when - now).total_seconds()
        if seconds <= self.SHORT_TIMER_THRESHOLD:  # dont want delta to be negative
            timer.id = key = await self.decrement_atomic_key()
            self._short_timers[key] = timer

            self._loop.create_task(self.start_short_timer(seconds, timer))
            log.debug(f"Short timer {timer.id} will fire in {seconds} seconds.")  # noqa: G004
            return timer

        timer.id = await self.bot.db.timers.create_timer(event, {"args": args, "kwargs": kwargs}, when, now, tz)

        if seconds <= self.MAX_DAYS * 86400:
            self.__event.set()

        if self._loaded_timer and when < self._loaded_timer.expires:
            self.reset_task()

        log.debug(f"Timer {timer.id} will fire at {when}.")  # noqa: G004
        return timer

    async def decrement_atomic_key(self) -> int:
        """Decrements the atomic key buffer used to store short timers atomically."""
        async with self.__short_timer_key_mutex:
            self.__short_timer_key_buffer -= 1
            return self.__short_timer_key_buffer

    # DISPATCHING

    async def load_next_timer(self, *, connection: asyncpg.Connection | None = None, days: int = 7) -> Timer | None:
        """|coro|

        Loads the next timer to be dispatched.

        Parameters
        -----------
        connection: :class:`asyncpg.Connection`
            The connection to use for the query.
        days: int
            The amount of days to look for the next timer.
        """
        record = await self.bot.db.timers.get_next_due(days, connection=connection)
        return Timer(bot=self.bot, record=record) if record else None

    async def wait(self, *, connection: asyncpg.Connection | None = None, days: int = 7) -> Timer:
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
        # No pooled connection is held across the idle wait: one parked there for days kept a pool
        # slot busy and made pool.close() at shutdown wait for it.
        self._loaded_timer = None  # the previous one has fired; nothing is loaded while we query
        while True:
            # Cleared before the query, so a timer created while it runs (possibly sooner than
            # the one it returns) sets the event and is picked up by querying again.
            self.__event.clear()
            timer = await self.load_next_timer(connection=connection, days=days)
            if self.__event.is_set():
                continue
            if timer is not None:
                log.debug("Loaded timer %r to fire at %s.", timer.id, timer.expires)
                return timer

            # Also reached when the timer that woke us was deleted again before the reload.
            log.debug("No timers to load, waiting for new timers to be created.")
            await self.__event.wait()

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

                # ``expires`` comes back from asyncpg as a naive datetime (the column is
                # ``TIMESTAMP`` without a zone), so it must be made UTC-aware before any
                # arithmetic against the aware ``now`` -- otherwise subtracting the two
                # raises ``TypeError`` and kills the whole dispatch loop, leaving every
                # future timer (polls, reminders, tempbans) stuck forever.
                expires = timer.expires.replace(tzinfo=datetime.UTC)
                if expires >= now:
                    to_sleep = (expires - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                log.debug("Dispatching timer %r for event %s now.", timer.id, timer.event)
                await self.call(timer)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self.reset_task()
        except Exception:
            # A dead loop stops every timer (tempbans, mutes, lockdowns, reminders) until a restart.
            log.exception("Timer dispatch loop failed; restarting it in 5 seconds")
            await asyncio.sleep(5)
            self.reset_task()

    def reset_task(self) -> None:
        self.__task.cancel()
        self.__task = self.bot.loop.create_task(self.dispatch_timers())
