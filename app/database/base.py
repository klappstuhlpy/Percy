from __future__ import annotations

import asyncio
import datetime
import enum
import json
import logging
import random
from abc import ABC
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, NamedTuple, ParamSpec, TypeVar, overload, Type

import asyncpg
import dateutil.tz
import discord
from captcha.image import ImageCaptcha
from discord.utils import MISSING
from app.utils import BaseFlags, CancellableQueue, cache, flag_value
from config import DatabaseConfig, Emojis

from ..rendering import ASSETS
from .migrations import Migrations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from PIL import Image

    from app.core import Bot

    DatabaseT = TypeVar('DatabaseT', bound='_Database')
    RecordT = TypeVar('RecordT', bound='BaseRecord')

P = ParamSpec('P')

__all__ = (
    'Database',
    'BaseRecord',
    'GuildConfig',
    'UserConfig',
    'Gatekeeper',
    'Balance',
)


class _Database:
    """The base class for the database.

    This class provides the basic functionality to interact with the PostgreSQL database using the asyncpg library.

    Attributes
    ----------
    bot : Bot
        The bot instance.
    loop : asyncio.AbstractEventLoop
        The event loop to use for the database operations.
    """

    __slots__ = ('bot', '_internal_pool', '_connect_task', 'loop')

    if TYPE_CHECKING:
        loop: asyncio.AbstractEventLoop
        _internal_pool: asyncpg.Pool
        _connect_task: asyncio.Task

    def __init__(self, bot: Bot, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self.bot = bot
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self._connect_task: asyncio.Task = self.loop.create_task(self._connect())

    async def _connect(self) -> None:
        try:
            self._internal_pool = await self.create_pool()

            async with self.acquire() as conn:
                migrator = Migrations()
                await migrator.upgrade(conn)
        except (asyncpg.PostgresError, OSError, TimeoutError) as e:
            logging.error(f'Failed to connect to the PostgreSQL database: {e}')
            logging.critical('Shutting down the bot due to database connection failure.')
            await self.bot.close()

    @classmethod
    async def create_pool(cls) -> asyncpg.Pool:
        """Creates a connection pool to the PostgreSQL database.

        This creates a connection pool to the PostgreSQL database using the asyncpg library.
        This also ensures the support for JSONB data type by encoding and decoding it.
        """
        def _encode_jsonb(value: Any) -> str:
            return json.dumps(value)

        def _decode_jsonb(value: str) -> Any:
            return json.loads(value)

        async def init(con: asyncpg.Connection) -> None:
            await con.set_type_codec(
                'jsonb',
                schema='pg_catalog',
                encoder=_encode_jsonb,
                decoder=_decode_jsonb,
                format='text',
            )

        return await asyncpg.create_pool(
            **DatabaseConfig.to_kwargs(),
            init=init,
            command_timeout=300,
            max_size=20,
            min_size=20,
        )

    async def wait(self: DatabaseT) -> DatabaseT:
        """|coro|

        Waits for the database to connect and returns the database instance.

        Returns
        -------
        DatabaseT
            The database instance.
        """
        await self._connect_task
        return self

    @overload
    def acquire(self, *, timeout: float | None = None) -> Awaitable[asyncpg.Connection]:
        ...

    def acquire(self, *, timeout: float | None = None) -> asyncpg.pool.PoolAcquireContext | Awaitable[None]:
        return self._internal_pool.acquire(timeout=timeout)

    def release(self, conn: asyncpg.Connection, *, timeout: float | None = None) -> Awaitable[None]:
        return self._internal_pool.release(conn, timeout=timeout)

    def execute(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[str]:
        return self._internal_pool.execute(query, *args, timeout=timeout)

    def fetch(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[list[Any]]:
        return self._internal_pool.fetch(query, *args, timeout=timeout)

    def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> Awaitable[asyncpg.Record]:
        return self._internal_pool.fetchrow(query, *args, timeout=timeout)

    def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float | None = None) -> Awaitable[Any]:
        return self._internal_pool.fetchval(query, *args, column=column, timeout=timeout)


class Database(_Database):

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
        query = "SELECT * FROM guild_config WHERE id=$1;"
        async with self.acquire(timeout=300.0) as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                return GuildConfig(bot=self.bot, record=record)

            query = "INSERT INTO guild_config (id) VALUES ($1) RETURNING *;"
            record = await con.fetchrow(query, guild_id)
            return GuildConfig(bot=self.bot, record=record)

    @cache.cache(action=lambda g: g.cancel_task())
    async def get_guild_gatekeeper(self, guild_id: int | None) -> Gatekeeper | None:
        """|coro|

        Get the gatekeeper for the guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the gatekeeper from.

        Returns
        -------
        :class:`Gatekeeper`
            The gatekeeper if it exists, else ``None``.
        """
        if guild_id is None:
            return None

        query = "SELECT * FROM guild_gatekeeper WHERE id=$1;"
        async with self.acquire(timeout=300.0) as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                query = "SELECT * FROM guild_gatekeeper_members WHERE guild_id=$1;"
                members = await con.fetch(query, guild_id)
                return Gatekeeper(members, bot=self.bot, record=record)
            return None

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
        query = "SELECT * from user_settings WHERE id = $1;"
        record = await self.fetchrow(query, user_id)
        if record is None:
            query = "INSERT INTO user_settings (id) VALUES ($1) RETURNING *;"
            record = await self.fetchrow(query, user_id)
        return UserConfig(bot=self.bot, record=record)

    async def get_user_timezone(self, user_id: int, /) -> str | None:
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
        query = "SELECT timezone FROM user_settings WHERE id = $1;"
        return await self.fetchval(query, user_id, column='timezone')

    async def get_user_balance(self, user_id: int, guild_id: int) -> Balance | None:
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
        query = "SELECT * FROM economy WHERE user_id = $1 AND guild_id = $2;"
        record = await self.fetchrow(query, user_id, guild_id)
        if not record:
            query = "INSERT INTO economy (user_id, guild_id, cash, bank) VALUES ($1, $2, 0, 0) RETURNING *;"
            record = await self.fetchrow(query, user_id, guild_id)
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
        query = "SELECT * FROM economy WHERE guild_id = $1;"
        records = await self.fetch(query, guild_id)
        return [Balance(bot=self.bot, record=record) for record in records]


class BaseRecord(ABC):
    """The base class for representing a PostgreSQL fetched record.

    This class facilitates the creation of a class that maps to a PostgreSQL row. It automatically maps the record's
    values to the class attributes, providing convenient access to the data.

    By overriding the `__slots__` attribute, you can specify which attributes should be mapped to the record.

    Additional attributes, which are not mapped to the record, can be added by specifying
    them in the `__init__` method and calling `super().__init__()`.

    Examples
    --------
    .. code-block:: python3

            class User(BaseRecord):
                __slots__ = ('id', 'name', 'age')

                def __init__(year: int, **kwargs):
                    self.year = year
                    super().__init__(**kwargs)

                @property
                def display_text() -> str:
                    return f'{self.name} ({self.age} Years old) - {self.year}'

            >> user = User(record=asyncpg.Record)
            >> print(user.display_text)
            John Doe (20 Years old) - 2021
    """

    if TYPE_CHECKING:
        __record: asyncpg.Record
        __ignore_record__: bool

    __slots__ = ('__record',)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Initializes the subclass."""
        if cls is BaseRecord:
            raise TypeError('Class `BaseRecord` must be initialized by subclassing it.')

        cls.__ignore_record__ = kwargs.pop('ignore_record', False)
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
        self.__record = record = kwargs.pop('record', None)
        if record is None and not self.__class__.__ignore_record__:
            raise TypeError('Subclasses of `BaseRecord` must provide a `record` keyword-only argument.')

        if record:
            if not isinstance(record, (asyncpg.Record, dict)):  # dict-like is okay too
                raise TypeError(
                    f'The record must be an instance of `asyncpg.Record`, not `{record.__class__.__name__}`.')

            for k, v in record.items():
                self._set_item_safe(k, v)
        else:
            self.__record = kwargs  # type: ignore

        if kwargs:
            for k, v in kwargs.items():
                self._set_item_safe(k, v)

    def _set_item_safe(self, key: str, value: Any) -> None:
        """Sets an item as a class attribute if its present in the __slots__ attribute."""
        if key in self.__slots__:
            setattr(self, key, value)

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> RecordT:
        """|coro|

        Updates the record with the given values.

        Notes
        -----
        This method `must` be overridden by subclasses to provide the functionality to
        update the record with the given values.

        Parameters
        ----------
        key : Callable[[tuple[int, str]], str]
            A callable that takes a tuple of an index and a key, and returns a string.
        values : dict[str, Any]
            The values to update the record with.
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        """
        raise NotImplementedError

    async def update(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Updates the record with the given values by setting the values to the record.

        -> X = Y

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = ${o[0]}', values, connection=connection)

    async def add(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Adds the given values to the record.

        -> X = X + Y

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = "{o[1]}" + ${o[0]}', values, connection=connection)

    async def remove(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Removes the given values from the record.

        -> X = X - Y

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = "{o[1]}" - ${o[0]}', values, connection=connection)

    async def append(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Appends the given values to the array.

        -> X = ARRAY_APPEND(X, Y)

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = ARRAY_APPEND("{o[1]}", ${o[0]})', values, connection=connection)

    async def prune(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Removes the given values to the array.

        -> X = ARRAY_REMOVE(X, Y)

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = ARRAY_REMOVE("{o[1]}", ${o[0]})', values, connection=connection)

    async def merge(
            self, *, connection: asyncpg.Connection | None = None, **values: Any
    ) -> Awaitable[RecordT] | RecordT:
        """|coro|

        Merges two lists in the array with the given values.

        -> X = ARRAY_CAT(X, Y)

        Parameters
        ----------
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        values : dict[str, Any]
            The values to update the record with.
        """
        return await self._update(lambda o: f'"{o[1]}" = ARRAY_CAT("{o[1]}", ${o[0]})', values, connection=connection)

    @classmethod
    def __subclasshook__(cls, subclass: type[Any]) -> bool:
        """Returns whether the subclass has a record attribute."""
        return hasattr(subclass, '__record')

    def __iter__(self) -> dict[str, Any]:
        """An iterator over the record's values."""
        return {k: v for k, v in self.__record.items() if not k.startswith('_')}

    def __repr__(self) -> str:
        args = [f'{k}={v!r}' for k, v in (self.__record.items() if self.__record else self.__dict__.items())]
        return '<{}.{}({})>'.format(self.__class__.__module__, self.__class__.__name__, ', '.join(args))

    def __eq__(self, other: object) -> bool:
        """Returns whether the items base record is equal to the other item's base record."""
        if isinstance(other, self.__class__):
            return getattr(self, '__record', None) == getattr(other, '__record', None)
        return False

    def __getitem__(self, item: str) -> Any:
        """Returns the value of the item's record."""
        if not self.__record:
            raise TypeError('Cannot get item from unresolved `BaseRecord` class without a record.')
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

    def __hash__(self) -> int:
        """Returns the hash of the item's record."""
        return hash(self.__record)

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the value of the item's record."""
        return self.__record.get(key, default)

    def to_record(self) -> asyncpg.Record:
        """Returns the record of the item."""
        return self.__record

    @classmethod
    def temporary(cls, *args: P.args, **kwargs: P.kwargs) -> BaseRecord:
        """Creates a temporary instance of this class with __ignore_record__ set to True."""
        return ignore_record(cls)(*args, **kwargs)  # type: ignore


def ignore_record(cls: type[BaseRecord]) -> Type[BaseRecord]:
    """A decorator that sets the __ignore_record__ attribute to True."""
    cls.__ignore_record__ = True
    return cls


class GuildConfig(BaseRecord):
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
        def gatekeeper(self) -> int:
            """Whether the server has gatekeeper enabled."""
            return 8

    bot: Bot
    id: int
    flags: AutoModFlags

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

    music_panel_channel_id: int | None
    music_panel_message_id: int | None
    use_music_panel: bool

    prefixes: set[str]

    linked_automod_rules: set[str]

    __slots__ = (
        'flags',
        'id',
        'bot',
        'audit_log_channel_id',
        'audit_log_flags',
        'audit_log_webhook_url',
        'poll_channel_id',
        'poll_ping_role_id',
        'poll_reason_channel_id',
        'mention_count',
        'safe_automod_entity_ids',
        'mute_role_id',
        'muted_members',
        'alert_webhook_url',
        'alert_channel_id',
        'music_panel_channel_id',
        'music_panel_message_id',
        'use_music_panel',
        'prefixes',
        'linked_automod_rules',
        '_cs_audit_log_webhook',
        '_cs_alert_webhook',
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.flags = self.AutoModFlags(self.flags or 0)
        self.safe_automod_entity_ids = set(self.safe_automod_entity_ids or [])
        self.muted_members = set(self.muted_members or [])
        self.prefixes = set(self.prefixes or [])
        self.linked_automod_rules = set(self.linked_automod_rules or [])

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> GuildConfig:
        """|coro|

        Updates the record with the given values.

        Parameters
        ----------
        key : Callable[[tuple[int, str]], str]
            A callable that takes a tuple of an index and a key, and returns a string.
        values : dict[str, Any]
            The values to update the record with.
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        """
        query = f"""
            UPDATE guild_config
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())
        self.bot.db.get_guild_config.invalidate(self.id)
        return self.__class__(bot=self.bot, record=record)

    @property
    def guild(self) -> discord.Guild | None:
        """:class:`discord.Guild`: The guild."""
        return self.bot.get_guild(self.id)

    @property
    def poll_channel(self) -> discord.TextChannel | None:
        """:class:`discord.TextChannel`: The poll channel."""
        guild = self.bot.get_guild(self.id)
        if guild:
            return guild.get_channel(self.poll_channel_id)

    @property
    def poll_reason_channel(self) -> discord.TextChannel | None:
        """:class:`discord.TextChannel`: The poll reason channel."""
        guild = self.bot.get_guild(self.id)
        if guild:
            return guild.get_channel(self.poll_reason_channel_id)

    @property
    def music_panel_channel(self) -> discord.TextChannel | None:
        """:class:`discord.TextChannel`: The music panel channel."""
        guild = self.bot.get_guild(self.id)
        if guild:
            return guild.get_channel(self.music_panel_channel_id)

    @discord.utils.cached_slot_property('_cs_audit_log_webhook')
    def audit_log_webhook(self) -> discord.Webhook | None:
        """:class:`discord.Webhook`: The audit log webhook."""
        if self.audit_log_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.audit_log_webhook_url, session=self.bot.session, client=self.bot)

    @discord.utils.cached_slot_property('_cs_alert_webhook')
    def alert_webhook(self) -> discord.Webhook | None:
        """:class:`discord.Webhook`: The alert webhook."""
        if self.alert_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.alert_webhook_url, session=self.bot.session, client=self.bot)

    @property
    def mute_role(self) -> discord.Role | None:
        """:class:`discord.Role`: The mute role."""
        guild = self.bot.get_guild(self.id)
        return guild and self.mute_role_id and guild.get_role(self.mute_role_id)

    async def fetch_automod_rules(self) -> list[discord.AutoModRule]:
        """|coro|

        Fetches all linked automod rules from the guild that were created using the bot.

        Returns
        -------
        list[:class:`discord.AutoModRule`]
            The linked automod rules.
        """
        if not self.linked_automod_rules:
            return []

        guild_rules = await self.guild.fetch_automod_rules()
        return list(filter(lambda rule: rule.id in self.linked_automod_rules, guild_rules))

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

    async def send_alert(
            self, content: str = MISSING, *, force: bool = False, **kwargs: Any
    ) -> discord.Message | None:
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

        if content is not MISSING and not content.startswith('<:'):
            content = f'{Emojis.info} {content}'

        try:
            if not alerts_available and force:
                # Send to the system channel if alerts are disabled
                return await self.bot.get_guild(self.id).system_channel.send(content, **kwargs)
            return await self.alert_webhook.send(content, **kwargs)
        except discord.HTTPException:
            return None


class UserConfig(BaseRecord):

    bot: Bot
    id: int
    timezone: str

    track_presence: bool

    __slots__ = ('bot', 'id', 'timezone', 'track_presence')

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> RecordT:
        """|coro|

        Updates the record with the given values.

        Parameters
        ----------
        key : Callable[[tuple[int, str]], str]
            A callable that takes a tuple of an index and a key, and returns a string.
        values : dict[str, Any]
            The values to update the record with.
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        """
        query = f"""
            UPDATE user_settings
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())
        self.bot.db.get_user_config.invalidate(self.id)
        return self.__class__(bot=self.bot, record=record)

    @property
    def tzinfo(self) -> datetime.tzinfo:
        if self.timezone is None:
            return datetime.UTC
        return dateutil.tz.gettz(self.timezone) or datetime.UTC


class Gatekeeper(BaseRecord):
    """A gatekeeper (Captcha-Verify-System) that prevents users from participating
    in the server until certain conditions are met.

    This is currently implemented as the user must solve a generated captcha image of six random characters.

    Attributes
    ----------
    id : int
        The ID of the guild.
    started_at : datetime.datetime | None
        The time when the gatekeeper was started.
    role_id : int | None
        The role ID to add to members.
    starter_role_id : int | None
        The role ID to add to members that bypass the gatekeeper.
    channel_id : int | None
        The channel ID where the gatekeeper is active.
    message_id : int | None
        The message ID that the gatekeeper is using.
    bypass_action : Literal['ban', 'kick']
        The action to take when someone bypasses the gatekeeper.
    rate : tuple[int, int] | None
        The rate limit for joining the server.
    members : set[int]
        The members that have the role and are pending to be verified.
    task : asyncio.Task
        The task that adds and removes the role from members.
    queue : CancellableQueue[int, tuple[int, GatekeeperRoleState]]
        The queue that is being processed in the background.

    Behavior Overview
    ------------------
    - Gatekeeper.members
        This is a set of members that have the role and are pending to
        receive the role. Anyone in this set is technically being gatekept.
        If they talk in any channel while technically gatekept then they
        should get autobanned/autokicked.

        If the gatekeeper is disabled, then this list should be cleared,
        probably one by one during clean-up.
    - Gatekeeper.started_at is None
        This signals that the gatekeeper is fully disabled.
        If this is true, then all members should lose their role
        and the table **should not** be cleared.

        There is a special case where this is true, but there
        are still members. In this case, clean up should resume.
    - Gatekeeper.started_at is not None
        This one's simple, the gatekeeper is fully operational
        and serving captchas and adding roles.
    """

    class GatekeeperRoleState(enum.Enum):
        """The state of a member in the gatekeeper."""
        added = 'added'
        pending_add = 'pending_add'
        pending_remove = 'pending_remove'

    class Captcha(NamedTuple):
        """A captcha image with the letters and the image."""
        text: str
        image: Image.Image

    __CAPTCHA_CHARS: Final[ClassVar[str]] = 'abcdefghijklmnopqrstuvwxyz1234567890'  # ABCDEFGHIJKLMNOPQRSTUVWXYZ
    __image_captcha: Final[ClassVar[ImageCaptcha]] = ImageCaptcha(
        width=300, height=100, fonts=[str(ASSETS / 'fonts/helvetica.ttf')])

    log = logging.getLogger('mod')

    bot: Bot
    id: int
    started_at: datetime.datetime | None
    channel_id: int | None
    role_id: int | None
    starter_role_id: int | None
    message_id: int | None
    bypass_action: Literal['ban', 'kick']
    rate: tuple[int, int] | str | None

    __slots__ = (
        'bot', '__members', '__stop_event', 'id', 'members', 'queue', 'task',
        'started_at', 'role_id', 'starter_role_id', 'channel_id', 'message_id', 'bypass_action', 'rate',
    )

    def __init__(self, members: list[Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.__members = members
        self.members: set[int] = {r['user_id'] for r in members if r['state'] == 'added'}

        if self.rate is not None:
            rate, per = self.rate.split('/')
            self.rate = (int(rate), int(per))

        # This event is used to stop the task because we can't
        # cancel the task gracefully without stopping the internal loop
        self.__stop_event: asyncio.Event = asyncio.Event()
        self.task: asyncio.Task = asyncio.create_task(self.role_loop())
        self.task._log_destroy_pending = False

        self.log.debug('Gatekeeper %r has started.', self.id)
        if self.started_at is not None:
            self.started_at = self.started_at.replace(tzinfo=datetime.UTC)

        # Alias for a type hint because we can't use self.GatekeeperRoleState
        _GatekeeperRoleState = self.GatekeeperRoleState
        self.queue: CancellableQueue[int, tuple[int, _GatekeeperRoleState]] = CancellableQueue(hook_check=self.__stop_event.is_set)

        for member in members:
            state = self.GatekeeperRoleState(member['state'])
            member_id = member['user_id']
            if state is not self.GatekeeperRoleState.added:
                self.queue.put(member_id, (member_id, state))

    def __repr__(self) -> str:
        attrs = [
            ('id', self.id),
            ('members', len(self.members)),
            ('started_at', self.started_at),
            ('role_id', self.role_id),
            ('starter_role_id', self.starter_role_id),
            ('channel_id', self.channel_id),
            ('message_id', self.message_id),
            ('bypass_action', self.bypass_action),
            ('rate', self.rate),
        ]
        joined = ' '.join('{}={!r}'.format(*t) for t in attrs)
        return f'<{self.__class__.__name__} {joined}>'

    @property
    def status(self) -> str:
        """The status of the gatekeeper."""
        headers = [
            ('Blocked Members', f'**{len(self.members)}**'),
            ('Enabled', discord.utils.format_dt(self.started_at) if self.started_at is not None else 'False'),
            ('Role', self.role.mention if self.role is not None else 'N/A'),
            ('Starter Role', self.starter_role.mention if self.starter_role is not None else 'N/A'),
            ('Channel', self.channel.mention if self.channel is not None else 'N/A'),
            ('Message', self.message.jump_url if self.message is not None else 'N/A'),
            ('Bypass Action', self.bypass_action.title()),
            ('Auto Trigger', f'{self.rate[0]}/{self.rate[1]}s' if self.rate is not None else 'N/A'),
        ]
        return '\n'.join(f'{header}: {value}' for header, value in headers)

    def generate_captcha(self) -> Captcha:
        """Creates a new random captacha image."""
        chars: str = ''.join(random.choices(self.__CAPTCHA_CHARS, k=6))
        return self.Captcha(text=chars, image=self.__image_captcha.generate_image(chars))

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
    ) -> Gatekeeper:
        """|coro|

        Updates the record with the given values.

        Parameters
        ----------
        key : Callable[[tuple[int, str]], str]
            A callable that takes a tuple of an index and a key, and returns a string.
        values : dict[str, Any]
            The values to update the record with.
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        """
        query = f"""
            UPDATE guild_gatekeeper
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())
        self.bot.db.get_guild_gatekeeper.invalidate(self.id)
        return self.__class__(self.__members, bot=self.bot, record=record)

    async def edit(
            self,
            *,
            started_at: datetime.datetime | None = MISSING,
            role_id: int | None = MISSING,
            starter_role_id: int | None = MISSING,
            channel_id: int | None = MISSING,
            message_id: int | None = MISSING,
            bypass_action: Literal['ban', 'kick'] = MISSING,
            rate: tuple[int, int] | None = MISSING,
    ) -> None:
        """|coro|

        Edits the gatekeeper.

        Parameters
        ----------
        started_at : datetime.datetime | None
            The time when the gatekeeper was started.
        role_id : int | None
            The role ID to add to members.
        starter_role_id : int | None
            The role ID to add to members that bypass the gatekeeper.
        channel_id : int | None
            The channel ID where the gatekeeper is active.
        message_id : int | None
            The message ID that the gatekeeper is using.
        bypass_action : Literal['ban', 'kick']
            The action to take when someone bypasses the gatekeeper.
        rate : tuple[int, int] | None
            The rate limit for joining the server.
        """
        form: dict[str, Any] = {}

        if role_id is None or channel_id is None or message_id is None:
            started_at = None

        if started_at is not MISSING:
            form['started_at'] = started_at
        if role_id is not MISSING:
            form['role_id'] = role_id
        if starter_role_id is not MISSING:
            form['starter_role_id'] = starter_role_id
        if channel_id is not MISSING:
            form['channel_id'] = channel_id
        if message_id is not MISSING:
            form['message_id'] = message_id
        if bypass_action is not MISSING:
            form['bypass_action'] = bypass_action
        if rate is not MISSING:
            form['rate'] = '/'.join(map(str, rate)) if rate is not None else None

        await self.update(**form)

        if role_id is not MISSING:
            await self.bot.db.execute("DELETE FROM guild_gatekeeper_members WHERE guild_id = $1;", self.id)

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
            member_id, action = await self.queue.get()

            try:
                if action is self.GatekeeperRoleState.pending_remove:
                    await self.bot.http.remove_role(
                        self.id, member_id, self.role_id, reason='Completed Gatekeeper verification')
                    query = "DELETE FROM guild_gatekeeper_members WHERE guild_id = $1 AND user_id = $2;"
                    await self.bot.db.execute(query, self.id, member_id)

                    if self.starter_role:
                        await self.bot.http.add_role(
                            self.id, member_id, self.starter_role_id, reason='Completed Gatekeeper verification')
                elif action is self.GatekeeperRoleState.pending_add:
                    await self.bot.http.add_role(
                        self.id, member_id, self.role_id, reason='Started Gatekeeper verification')
                    query = "UPDATE guild_gatekeeper_members SET state = 'added' WHERE guild_id = $1 AND user_id = $2;"
                    await self.bot.db.execute(query, self.id, member_id)
            except discord.DiscordServerError:
                self.queue.put(member_id, (member_id, action))
            except discord.NotFound as e:
                if e.code not in (10011, 10013):
                    break
                if e.code == 10011:
                    # Unknown role, disable the gatekeeper.
                    config = await self.bot.db.get_guild_config(self.id)
                    await config.send_alert(
                        'A Role you\'ve set up for the gatekeeper was not found, please review! Disabling the gatekeeper.'
                    )
                    needs_migration = {}
                    if self.role is None:
                        needs_migration['role_id'] = None
                    if self.starter_role is None:
                        needs_migration['starter_role_id'] = None
                    await self.edit(started_at=None, **needs_migration)
                    break
                continue
            except Exception:
                self.log.exception('[Gatekeeper] An exception happened in the role loop of guild ID %d', self.id)
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
                    '[Gatekeeper] An exception happened in the role cleanup loop of guild ID %d: %r', self.id, exc)

    @property
    def pending_members(self) -> int:
        """The number of members that are pending to receive the role."""
        return len(self.members)

    async def enable(self) -> None:
        """|coro|

        Enables the gatekeeper.
        This will set the started_at field to the current time.
        """
        await self.edit(started_at=discord.utils.utcnow())

    async def disable(self) -> None:
        """|coro|

        Disables the gatekeeper.
        This will remove the role from all members and clear the queue.
        """
        await self.edit(started_at=None)

        async with self.bot.db.acquire(timeout=300.0) as conn, conn.transaction():
            query = "UPDATE guild_gatekeeper_members SET state = 'pending_remove' WHERE guild_id = $1 AND state = 'added';"
            await conn.execute(query, self.id)
            for member_id in self.members:
                self.queue.put(member_id, (member_id, self.GatekeeperRoleState.pending_remove))
            self.members.clear()

    @property
    def role(self) -> discord.Role | None:
        """The role that is being added to members."""
        guild = self.bot.get_guild(self.id)
        return guild and self.role_id and guild.get_role(self.role_id)

    @property
    def starter_role(self) -> discord.Role | None:
        """The role that is being added to members that bypass the gatekeeper."""
        guild = self.bot.get_guild(self.id)
        return guild and self.starter_role_id and guild.get_role(self.starter_role_id)

    @property
    def channel(self) -> discord.TextChannel | None:
        """The channel where the gatekeeper is active."""
        guild = self.bot.get_guild(self.id)
        return guild and guild.get_channel(self.channel_id)

    @property
    def message(self) -> discord.PartialMessage | None:
        """The message that the gatekeeper is using."""
        if self.channel_id is None or self.message_id is None:
            return None

        channel = self.bot.get_partial_messageable(self.channel_id)
        return channel.get_partial_message(self.message_id)

    @property
    def requires_setup(self) -> bool:
        """Whether the gatekeeper requires setup."""
        return self.role_id is None or self.channel_id is None or self.message_id is None

    def is_blocked(self, user_id: int, /) -> bool:
        """Whether the user is blocked from participating in the server."""
        return user_id in self.members

    def has_role(self, member: discord.Member, /) -> bool:
        """Checks if a user has the gatekeeper role."""
        return self.role_id is not None and member._roles.has(self.role_id)

    def is_bypassing(self, member: discord.Member) -> bool:
        """Whether the member is bypassing the gatekeeper."""
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
        query = "INSERT INTO guild_gatekeeper_members(guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
        await self.bot.db.execute(query, self.id, member.id)
        self.queue.put(member.id, (member.id, self.GatekeeperRoleState.pending_add))

    async def force_enable_with(self, members: Sequence[discord.Member]) -> None:
        """|coro|

        Forces the gatekeeper to enable with the given members.
        This will add the members to the queue and the members set.

        Parameters
        ----------
        members : Sequence[discord.Member]
            The members to block.
        """
        self.members.update(m.id for m in members)
        await self.edit(started_at=discord.utils.utcnow())

        async with self.bot.db.acquire(timeout=300.0) as conn, conn.transaction():
            query = "INSERT INTO guild_gatekeeper_members(guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
            await conn.executemany(query, [(self.id, m.id) for m in members])

        for member in members:
            self.queue.put(member.id, (member.id, self.GatekeeperRoleState.pending_add))

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
            query = "DELETE FROM guild_gatekeeper_members WHERE guild_id = $1 AND user_id = $2;"
            await self.bot.db.execute(query, self.id, member.id)
            self.queue.cancel(member.id)
        else:
            query = "UPDATE guild_gatekeeper_members SET state = 'pending_remove' WHERE guild_id = $1 AND user_id = $2;"
            await self.bot.db.execute(query, self.id, member.id)
            self.queue.put(member.id, (member.id, self.GatekeeperRoleState.pending_remove))


class Balance(BaseRecord):
    """Represents a user's balance"""

    bot: Bot
    user_id: int
    guild_id: int
    cash: int
    bank: int

    __slots__ = ('bot', 'user_id', 'guild_id', 'cash', 'bank')

    @property
    def total(self) -> int:
        """Gets the total amount of money a user has"""
        return self.cash + self.bank

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> Balance:
        """|coro|

        Updates the record with the given values.

        Parameters
        ----------
        key : Callable[[tuple[int, str]], str]
            A callable that takes a tuple of an index and a key, and returns a string.
        values : dict[str, Any]
            The values to update the record with.
        connection : asyncpg.Connection | None
            The connection to use for the update operation.
        """
        query = f"""
            UPDATE economy
            SET {', '.join(map(key, enumerate(values.keys(), start=3)))}
            WHERE user_id = $1 AND guild_id = $2
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.user_id, self.guild_id, *values.values())
        return self.__class__(bot=self.bot, record=record)
