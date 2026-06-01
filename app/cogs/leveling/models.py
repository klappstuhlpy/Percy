from __future__ import annotations

import asyncio
import math
import random
from contextlib import suppress
from typing import TYPE_CHECKING, Any, NamedTuple

import discord
from discord.ext import commands

from app.database import BaseRecord
from app.utils import fnumb, sanitize_snowflakes

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    import asyncpg

    from app.cogs.leveling.cog import Leveling
    from app.core import Bot

__all__ = (
    'CooldownManager',
    'GainConfig',
    'GuildLevelConfig',
    'LevelConfig',
    'LevelingSpec',
)

_MAX_LEVEL = 500
_MAX_XP = 125_052_000


class CooldownManager:
    """A class to manage cooldowns for the leveling system."""

    def __init__(self, guild_id: int, *, rate: int, per: float) -> None:
        self.guild_id: int = guild_id

        self.rate: int = rate
        self.per: float = per

        self._cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(rate, per, commands.BucketType.user)

    def _is_ratelimited(self, message: discord.Message) -> float | None:
        assert message.guild is not None
        current = message.created_at.timestamp()
        bucket = self._cooldown.get_bucket(message, current=current)
        if bucket is None:
            return None
        return bucket.update_rate_limit(current)

    def can_gain(self, message: discord.Message) -> bool:
        return not self._is_ratelimited(message)


class GainConfig(NamedTuple):
    minimum: int
    maximum: int


class LevelingSpec(NamedTuple):
    """Represents the leveling configuration for a guild."""
    guild_id: int
    base: int
    factor: float
    gain: GainConfig

    def level_requirement_for(self, level: int, /) -> int:
        """Returns the level requirement for the given level."""
        return math.ceil(self.base * (level ** self.factor) / 10) * 10

    def xp_requirement_for(self, xp: int, /) -> int:
        return math.floor((xp / self.base) ** (1 / self.factor))

    def get_total_xp(self, level: int, xp: int, /) -> int:
        return xp + self.level_requirement_for(level)

    def get_xp_gain(self, multiplier: float = 1.0) -> int:
        return round(random.randint(*self.gain) * multiplier)


class GuildLevelConfig(BaseRecord):
    """Represents a leveling configuration for a guild."""

    cog: Leveling
    id: int
    enabled: bool
    role_stack: bool
    base: int
    factor: float
    min_gain: int
    max_gain: int
    cooldown_rate: int
    cooldown_per: float
    level_up_message: str
    level_up_channel: int
    special_level_up_messages: dict[int, str]
    blacklisted_roles: set[int]
    blacklisted_channels: set[int]
    blacklisted_users: set[int]
    level_roles: dict[int, int]
    multiplier_roles: dict[int, int]
    multiplier_channels: dict[int, int]
    delete_after_leave: bool

    __slots__ = (
        'base',
        'blacklisted_channels',
        'blacklisted_roles',
        'blacklisted_users',
        'bot',
        'cog',
        'cooldown_manager',
        'cooldown_per',
        'cooldown_rate',
        'delete_after_leave',
        'enabled',
        'factor',
        'id',
        'level_roles',
        'level_up_channel',
        'level_up_message',
        'max_gain',
        'min_gain',
        'multiplier_channels',
        'multiplier_roles',
        'role_stack',
        'spec',
        'special_level_up_messages'
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bot: Bot = self.cog.bot

        gain = GainConfig(self.min_gain, self.max_gain)
        self.spec: LevelingSpec = LevelingSpec(
            guild_id=self.id,
            base=self.base,
            factor=self.factor,
            gain=gain,
        )

        self.cooldown_manager: CooldownManager = CooldownManager(
            self.id,
            rate=self.cooldown_rate,
            per=self.cooldown_per
        )

        self.level_roles = sanitize_snowflakes(self.level_roles)
        self.multiplier_roles = sanitize_snowflakes(self.multiplier_roles)
        self.multiplier_channels = sanitize_snowflakes(self.multiplier_channels)

    def __bool__(self) -> bool:
        return self.enabled

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> GuildLevelConfig:
        """|coro|

        Update the guild level configuration with the given values.

        Parameters
        ----------
        key: Callable[[tuple[int, str]], str]
            A callable that returns the key for the update query.
        values: dict[str, Any]
            The values to update the guild level configuration with.
        connection: :class:`asyncpg.Connection
            The connection to use for the query.

        Returns
        -------
        :class:`GuildLevelConfig`
            The updated guild level configuration.
        """
        record = await self.bot.db.leveling.update_guild_config(self.id, key, values, connection=connection)
        self.cog.get_guild_level_config.invalidate(self.id)
        return self.__class__(cog=self.cog, record=record)

    async def walk_users(self) -> AsyncGenerator[LevelConfig, None]:
        records = await self.bot.db.leveling.get_user_levels(self.id)
        for record in records:
            yield LevelConfig(cog=self.cog, config=self, record=record)

    async def update_all_roles(self) -> None:
        async for record in self.walk_users():
            await record.update_roles(record.level)
        self.cog.get_guild_level_config.invalidate(self.id)

    async def delete_member(self, member: discord.Member) -> None:
        await self.bot.db.leveling.delete_member(member.id, self.id)


class LevelConfig(BaseRecord):
    """Represents a level configuration for a guild."""

    cog: Leveling
    config: GuildLevelConfig
    guild_id: int
    user_id: int
    level: int
    xp: int
    messages: int

    __slots__ = ('cog', 'config', 'guild_id', 'level', 'messages', 'user_id', 'xp')

    def __len__(self) -> int:
        return self.messages

    def __int__(self) -> int:
        return self.xp

    def __str__(self) -> str:
        return fnumb(self.xp)

    @property
    def user(self) -> discord.Member | None:
        """Returns the member associated with this level config."""
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild:
            return guild.get_member(self.user_id)
        return None

    @property
    def max_xp(self) -> int:
        """:class:`int`: The maximum XP for the current level."""
        return self.config.spec.level_requirement_for(self.level + 1)

    def is_ratelimited(self, message: discord.Message) -> bool:
        return not self.config.cooldown_manager.can_gain(message)

    def can_gain(self, message: discord.Message) -> bool:
        user = self.user
        if user is None:
            return False
        return (
                user.id not in self.config.blacklisted_users
                and message.channel.id not in self.config.blacklisted_channels
                and not any(user._roles.has(role) for role in self.config.blacklisted_roles)
                and not self.is_ratelimited(message)
        )

    def get_multiplier(self, message: discord.Message) -> float:
        multiplier = 1.0
        user = self.user

        for role_id, multi in self.config.multiplier_roles.items():
            if user is not None and user._roles.has(role_id):
                multiplier += multi

        if message.channel.id in self.config.multiplier_channels:
            multiplier += self.config.multiplier_channels[message.channel.id]

        return multiplier

    async def send_level_up_message(self, level: int, message: discord.Message) -> None:
        if not self.config.level_up_channel or not self.config.level_up_message:
            return

        func: Callable[..., Awaitable[Any]]
        match self.config.level_up_channel:
            case 0:
                return
            case 1:
                func = message.reply
            case 2:
                user = self.user
                if user is None:
                    return
                dm_channel = await user.create_dm()
                func = dm_channel.send
            case custom:
                channel = self.cog.bot.get_channel(custom)
                if not isinstance(channel, discord.abc.Messageable):
                    return
                func = channel.send

        content = self.config.special_level_up_messages.get(
            level,
            self.config.level_up_message,
        ).format(user=self.user, level=level)
        with suppress(discord.HTTPException):
            await func(content)

    async def process_invoke(self, message: discord.Message) -> None:
        if not self.can_gain(message):
            return

        multiplier = self.get_multiplier(message)
        gain = self.config.spec.get_xp_gain(multiplier)
        await self.add_xp(gain, message=message)

    async def update_roles(self, level: int) -> None:
        user = self.user
        if user is None:
            return

        roles = self.config.level_roles
        if not roles:
            return

        _new = [(k, v) for k, v in roles.items() if level >= v]
        if _new:
            _new = max(_new, key=lambda r: r[1]),

        _new_ids: set[int] = {int(k) for k, v in _new}
        _old: set[int] = set(user._roles)

        new: set[int] = _old | _new_ids
        new.difference_update(int(role) for role in roles if role not in _new_ids)

        if new == {r.id for r in user.roles}:
            return

        reason = f'Overwrting roles for level {level}.'
        with suppress(discord.HTTPException):
            await user.edit(roles=list(map(discord.Object, new)), reason=reason)

    async def add_xp(self, xp: int, *, message: discord.Message) -> tuple[int, int]:
        self.xp += xp
        self.messages += 1
        __tasks = []

        if xp > 0 and self.xp > self.max_xp:
            while self.xp > self.max_xp:
                self.xp -= self.max_xp
                self.level += 1

            __tasks.extend((
                self.send_level_up_message(self.level, message),
                self.update_roles(self.level),
            ))

        elif xp < 0:
            while self.xp < 0 <= self.level:
                self.xp += self.max_xp
                self.level -= 1

        await self.update(xp=self.xp, level=self.level, messages=self.messages)
        await asyncio.gather(*__tasks)

        return self.level, self.xp

    async def get_rank(self, *, connection: asyncpg.Connection | None = None) -> int:
        """|coro|

        Returns the rank of the user in the guild.

        Parameters
        ----------
        connection: :class:`asyncpg.Connection
            The connection to use for the query.

        Returns
        -------
        :class:`int`
            The rank of the user in the guild.
        """
        return await self.cog.bot.db.leveling.get_rank(self.user_id, self.guild_id, connection=connection)

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> LevelConfig:
        """|coro|

        Update the level configuration with the given values.

        Parameters
        ----------
        key: Callable[[tuple[int, str]], str]
            A callable that returns the key for the update query.
        values: dict[str, Any]
            The values to update the level configuration with.
        connection: :class:`asyncpg.Connection
            The connection to use for the query.

        Returns
        -------
        :class:`LevelConfig`
            The updated level configuration.
        """
        record = await self.cog.bot.db.leveling.update_user_level(
            self.user_id, self.guild_id, key, values, connection=connection)
        return self.__class__(cog=self.cog, config=self.config, record=record)
