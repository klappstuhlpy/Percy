from __future__ import annotations

import asyncio
import asyncpg
import logging
import math
import random
from contextlib import suppress
from typing import Annotated, Any, NamedTuple, TypeVar
from collections.abc import AsyncGenerator, Awaitable, Callable

import discord
from discord import AppCommandOptionType, app_commands
from discord.ext import commands
from discord.ext.commands import Range

from app.core import Bot, Cog, Flags, flag, View, converter
from app.core.converter import IgnoreableEntity, IgnoreEntity
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.rendering import LevelCard
from app.utils import cache, get_asset_url, helpers, humanize_duration, medal_emoji, sanitize_snowflakes, truncate, \
    fnumb
from config import Emojis

log = logging.getLogger(__name__)


_MAX_LEVEL = 500
_MAX_XP = 125_052_000


class LevelSetFlags(Flags):
    xp: Range[int, 1, _MAX_XP] = flag(description='The amount of XP you want to set.', alias='experience')
    level: Range[int, 1, _MAX_LEVEL] = flag(description='The level you want to set.')


class AnyLevelChannel(commands.Converter, app_commands.Transformer):
    async def convert(self, ctx: Context, argument: str) -> discord.TextChannel | str:
        if argument.lower() in ('dm', 'channel'):
            return argument.lower()
        return await commands.TextChannelConverter().convert(ctx, argument)

    @property
    def type(self) -> AppCommandOptionType:
        return AppCommandOptionType.channel


class AddLevelRoleModal(discord.ui.Modal):
    level = discord.ui.TextInput(
        label='Level',
        placeholder='Enter a level, e.g. 10',
        required=True,
        min_length=1,
        max_length=3,
    )

    def __init__(self, view: InteractiveLevelRolesView, *, role: discord.Role) -> None:
        self.view = view
        self.role = role
        self.level.label = 'At what level will this role be assigned at?'
        super().__init__(title='Configure Level Role', timeout=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        level = int(self.level.value)
        if not 1 <= level <= _MAX_LEVEL:
            return await interaction.response.send_message(f'Level must be between 1 and {_MAX_LEVEL}.', ephemeral=True)

        self.view._roles[self.role.id] = level
        self.view.remove_select.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RemoveLevelRolesSelect(discord.ui.RoleSelect['InteractiveLevelRolesView']):
    def __init__(self, roles_ref: dict[SnowflakeT, int], ctx: Context) -> None:
        self._roles_ref = roles_ref
        self._ctx = ctx
        super().__init__(placeholder='Remove level roles...', row=1, max_values=25)
        self.update()

    def update(self) -> None:
        self.options = [
            discord.SelectOption(
                label=f'Level {level}',
                description=f'@{self._ctx.guild.get_role(role_id)}',
                value=str(role_id),
                emoji=Emojis.trash,
            )
            for role_id, level in sorted(self._roles_ref.items(), key=lambda pair: pair[1])
        ]
        self.disabled = not self.options
        if self.disabled:
            self.options = [discord.SelectOption(label='.')]
        self.max_values = len(self.options)

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            for value in self.values:
                self._roles_ref.pop(int(value))  # type: ignore
        except KeyError:
            pass

        self.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RoleStackToggle(discord.ui.Button['InteractiveLevelRolesView']):
    def __init__(self, current: bool) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=f'{'Disable' if current else 'Enable'} Role Stack',
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view._role_stack = new = not self.view._role_stack
        self.label = f'{'Disable' if new else 'Enable'} Role Stack'
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class InteractiveLevelRolesView(View):
    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()
        self._role_stack = config.role_stack

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)
        self.add_item(self.remove_select)
        self.add_item(RoleStackToggle(self._role_stack))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(color=helpers.Colour.white(), timestamp=self.ctx.now)
        embed.set_author(name=f'{self.ctx.guild} Level Role Rewards', icon_url=self.ctx.guild.icon.url)
        embed.set_footer(text='Make sure to save your changes by pressing the Save button!')

        indicator = 'Users can accumulate multiple level roles.' if self._role_stack else 'Users can only have the highest level role.'
        embed.add_field(name='Role Stack', value=f'{Emojis.success if self._role_stack else Emojis.error} {indicator}')

        if not self._roles:
            embed.description = 'You have not configured any level role rewards yet.'
            return embed

        embed.insert_field_at(
            index=0,
            name=f'Level Roles ({len(self._roles)}/25 slots)',
            value='\n'.join(
                f'- Level {level}: <@&{role_id}>'
                for role_id, level in sorted(self._roles.items(), key=lambda pair: pair[1])
            ),
            inline=False
        )
        return embed

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder='Add a new level role reward...',
        min_values=1,
        max_values=1,
        row=0,
    )
    async def add_level_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        if role.is_default() or role.managed:
            return await interaction.response.send_message(
                'That role is a default role or managed role, which means I am unable to assign it.\n'
                'Try using a different role or creating a new one.',
                ephemeral=True,
            )

        if not role.is_assignable():
            return await interaction.response.send_message(
                f'That role is lower than or equal to my top role ({self.ctx.me.top_role.mention}) in the role hierarchy, '
                f'which means I am unable to assign it.\nTry moving the role to be lower than {self.ctx.me.top_role.mention}, '
                'and then try again.',
                ephemeral=True,
            )
        await interaction.response.send_modal(AddLevelRoleModal(self, role=role))

    @discord.ui.button(label='Save', style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, _) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        for child in self.children:
            child.disabled = True

        embed = self.make_embed()
        embed.colour = helpers.Colour.yellow()
        await interaction.response.edit_message(content='Updating roles...', embed=embed, view=self)

        await self.config.update_all_roles()

        embed.colour = helpers.Colour.lime_green()
        await interaction.edit_original_response(content='Saved and updated level roles.', embed=embed, view=self)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, _) -> None:
        for child in self.children:
            child.disabled = True

        embed = self.make_embed()
        embed.colour = helpers.Colour.light_red()
        await interaction.response.edit_message(content='Cancelled. Changes were discarded.', embed=embed, view=self)

    async def on_timeout(self) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        await self.config.update_all_roles()


class InteractiveMultiplierView(View):
    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()
        self._role_stack = config.role_stack

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)
        self.add_item(self.remove_select)
        self.add_item(RoleStackToggle(self._role_stack))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(color=helpers.Colour.white(), timestamp=self.ctx.now)
        embed.set_author(name=f'{self.ctx.guild} Level Role Rewards', icon_url=self.ctx.guild.icon.url)
        embed.set_footer(text='Make sure to save your changes by pressing the Save button!')

        indicator = 'Users can accumulate multiple level roles.' if self._role_stack else 'Users can only have the highest level role.'
        embed.add_field(name='Role Stack', value=f'{Emojis.success if self._role_stack else Emojis.error} {indicator}')

        if not self._roles:
            embed.description = 'You have not configured any level role rewards yet.'
            return embed

        embed.insert_field_at(
            index=0,
            name=f'Level Roles ({len(self._roles)}/25 slots)',
            value='\n'.join(
                f'- Level {level}: <@&{role_id}>'
                for role_id, level in sorted(self._roles.items(), key=lambda pair: pair[1])
            ),
            inline=False
        )
        return embed

    async def on_timeout(self) -> None:
        await self.config.update()


class CooldownManager:
    """A class to manage cooldowns for the leveling system."""

    def __init__(self, guild_id: SnowflakeT, *, rate: int, per: float) -> None:
        self.guild_id: SnowflakeT = guild_id

        self.rate: int = rate
        self.per: float = per

        self._cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(rate, per, commands.BucketType.user)

    def _is_ratelimited(self, message: discord.Message) -> float | None:
        assert message.guild is not None
        current = message.created_at.timestamp()
        bucket = self._cooldown.get_bucket(message, current=current)
        return bucket.update_rate_limit(current)

    def can_gain(self, message: discord.Message) -> bool:
        return not self._is_ratelimited(message)


class GainConfig(NamedTuple):
    minimum: int
    maximum: int


class LevelingSpec(NamedTuple):
    """Represents the leveling configuration for a guild."""
    guild_id: SnowflakeT
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


T = TypeVar('T')
SnowflakeT = TypeVar('SnowflakeT', bound=discord.abc.Snowflake | int)


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
        'id', 'enabled', 'role_stack', 'base', 'factor', 'min_gain', 'max_gain', 'cooldown_rate',
        'cooldown_per', 'level_up_message', 'level_up_channel', 'special_level_up_messages', 'blacklisted_roles', 'blacklisted_channels',
        'blacklisted_users', 'level_roles', 'multiplier_roles', 'multiplier_channels', 'delete_after_leave',
        'bot', 'cog', 'spec', 'cooldown_manager'
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

        self.level_roles: dict[SnowflakeT, int] = sanitize_snowflakes(self.level_roles)
        self.multiplier_roles: dict[SnowflakeT, int] = sanitize_snowflakes(self.multiplier_roles)
        self.multiplier_channels: dict[SnowflakeT, int] = sanitize_snowflakes(self.multiplier_channels)

    def __bool__(self):
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
        query = f"""
            UPDATE level_config
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())
        self.cog.get_guild_level_config.invalidate(self.id)
        return self.__class__(cog=self.cog, record=record)

    async def walk_users(self) -> AsyncGenerator[LevelConfig, None]:
        query = "SELECT * FROM levels WHERE guild_id = $1;"
        records = await self.bot.db.fetch(query, self.id)
        for record in records:
            yield LevelConfig(cog=self.cog, config=self, record=record)

    async def update_all_roles(self) -> None:
        async for record in self.walk_users():
            await record.update_roles(record.level)
        self.cog.get_guild_level_config.invalidate(self.id)

    async def delete_member(self, member: discord.Member) -> None:
        query = "DELETE FROM levels WHERE user_id = $1 AND guild_id = $2;"
        await self.bot.db.execute(query, member.id, self.id)


class LevelConfig(BaseRecord):
    """Represents a level configuration for a guild."""

    cog: Leveling
    config: GuildLevelConfig
    guild_id: int
    user_id: int
    level: int
    xp: int
    messages: int

    __slots__ = ('cog', 'config', 'user_id', 'guild_id', 'messages', 'level', 'xp')

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
        return (
                self.user.id not in self.config.blacklisted_users
                and message.channel.id not in self.config.blacklisted_channels
                and not any(self.user._roles.has(role) for role in self.config.blacklisted_roles)
                and not self.is_ratelimited(message)
        )

    def get_multiplier(self, message: discord.Message) -> float:
        multiplier = 1.0

        for role_id, multi in self.config.multiplier_roles.items():
            if self.user._roles.has(role_id):
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
                dm_channel = await self.user.create_dm()
                func = dm_channel.send
            case custom:
                func = self.cog.bot.get_channel(custom).send

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
        roles = self.config.level_roles
        if not roles:
            return

        _new = [(k, v) for k, v in roles.items() if level >= v]
        if _new:
            _new = max(_new, key=lambda r: r[1]),

        _new = {k for k, v in _new}
        _old = set(self.user._roles)

        new = _old | _new
        new.difference_update(role for role in roles if role not in _new)

        if new == self.user.roles:
            return

        reason = f'Overwrting roles for level {level}.'
        with suppress(discord.HTTPException):
            await self.user.edit(roles=list(map(discord.Object, new)), reason=reason)

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
        query = """
            SELECT rank
            FROM (SELECT user_id, guild_id, row_number() OVER (ORDER BY xp DESC) AS rank
                  FROM levels
                  WHERE guild_id = $2) AS rank
            WHERE user_id = $1
              AND guild_id = $2
            LIMIT 1;
        """
        record = await (connection or self.cog.bot.db).fetchval(query, self.user_id, self.guild_id)
        return record

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
        query = f"""
            UPDATE levels
            SET {', '.join(map(key, enumerate(values.keys(), start=3)))}
            WHERE user_id = $1 AND guild_id = $2
            RETURNING *;
        """
        record = await (connection or self.cog.bot.db).fetchrow(query, self.user_id, self.guild_id, *values.values())
        return self.__class__(cog=self.cog, config=self.config, record=record)


class Leveling(Cog):
    """Leveling system, commands and utilities."""

    emoji = '<:oneup:1322338839909634118>'

    @cache.cache()
    async def get_guild_level_config(self, guild_id: int, /) -> GuildLevelConfig | None:
        """|coro| @cached

        Returns the :class:`GuildLevelConfig` for the given guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the level config for.

        Returns
        -------
        :class:`GuildLevelConfig`
            The level config for the given guild.
        """
        query = "SELECT * FROM level_config WHERE id = $1 LIMIT 1;"
        record: asyncpg.Record = await self.bot.db.fetchrow(query, guild_id)
        if not record:
            return None
        return GuildLevelConfig(cog=self, record=record)

    async def get_level_config(self, user_id: int, guild_id: int) -> LevelConfig | None:
        """|coro| @cached

        Returns the :class:`LevelConfig` for the given user and guild.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to get the level config for.
        guild_id: :class:`int`
            The guild ID to get the level config for.

        Returns
        -------
        :class:`LevelConfig`
            The level config for the given user and guild.
        """
        query = "SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;"
        record: asyncpg.Record = await self.bot.db.fetchrow(query, user_id, guild_id)
        if not record:
            query = "INSERT INTO levels (user_id, guild_id) VALUES ($1, $2) RETURNING *;"
            record: asyncpg.Record = await self.bot.db.fetchrow(query, user_id, guild_id)
        return LevelConfig(cog=self, config=await self.get_guild_level_config(guild_id), record=record)

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return

        if message.author.bot:
            return

        guild_config: GuildLevelConfig = await self.get_guild_level_config(message.guild.id)

        if guild_config is None:
            return

        if not guild_config.enabled:
            return

        if any(user.bot for user in message.mentions):
            return

        if len(message.content) <= 2:
            return

        config = await self.get_level_config(message.author.id, message.guild.id)
        assert config is not None
        await config.process_invoke(message)

    @Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild_config = await self.get_guild_level_config(member.guild.id)
        if guild_config is None:
            return

        if not guild_config.delete_after_leave:
            return

        await guild_config.delete_member(member)

    @group(
        'level',
        fallback='rank',
        description='Leveling purpose Commands.',
        guild_only=True,
        hybrid=True
    )
    @describe(member='The member to view the rank card of.')
    async def level(
            self, ctx: Context, *, member: Annotated[discord.Member, converter.MemberConverter]
    ) -> None:
        """View yours or someone else's rank card."""
        user: discord.Member = member or ctx.author

        if user.bot:
            await ctx.send_error('You can\'t view the rank card of a bot.')
            return

        await ctx.defer(typing=True)

        config: LevelConfig = await self.get_level_config(user.id, user.guild.id)

        if config.xp == 0:
            await ctx.send_error(f'**{user}** has not gained any XP yet.')
            return

        level_card = LevelCard(
            await user.display_avatar.read(),
            user,
            config
        )
        image = await level_card.create()
        await ctx.send(file=image)

    @level.command(
        aliases=['top'],
        description='View the server leaderboard.',
        guild_only=True
    )
    async def leaderboard(self, ctx: Context) -> None:
        """View the Top 10 users of the server."""
        query = "SELECT user_id, level, xp, messages FROM levels WHERE guild_id = $1 AND messages > 0 ORDER BY messages DESC LIMIT 10;"
        records = await ctx.db.fetch(query, ctx.guild.id)

        embed = discord.Embed(colour=helpers.Colour.white(), title=f'Level Statistics for {ctx.guild.name}')
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text='Level Statistics for this Server.')

        if not records:
            value = '*There are no statistics for this category available.*'
        else:
            value = '\n'.join(
                [f'{medal_emoji(index, numerate=True)}: <@{record['user_id']}> • Level **{record['level']}** • **{fnumb(record['xp'])}** XP'
                 for index, record in enumerate(records, 1)]
            )

        embed.description = value
        await ctx.send(embed=embed)

    @level.command(
        'set',
        description='Set a members experience or level.',
        guild_only=True,
        user_permissions=PermissionTemplate.admin
    )
    @describe(target='The target member to modify.')
    async def level_set(
            self,
            ctx: Context,
            target: Annotated[discord.Member, converter.MemberConverter],
            *,
            flags: LevelSetFlags
    ) -> None:
        """Set a users experience/level."""
        guild_config: GuildLevelConfig = await self.get_guild_level_config(ctx.guild.id)
        if guild_config is None or (guild_config and not guild_config.enabled):
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        if target.bot:
            await ctx.send_error('You can\'t manage Bot\'s Level/Experience.')
            return

        if (flags.xp is None and flags.level is None) or (flags.xp and flags.level):
            await ctx.send_error('You need to provide either a level or xp to set.')
            return

        config: LevelConfig = await self.get_level_config(target.id, target.guild.id)

        if flags.level:
            if flags.level > _MAX_LEVEL:
                await ctx.send_error(f'You can\'t set more than **Level {_MAX_LEVEL}**.')
                return

            level = flags.level
            xp = guild_config.spec.level_requirement_for(flags.level)
        else:
            if flags.xp > _MAX_XP:
                await ctx.send_error(f'Sorry. You can\'t set more than **{fnumb(_MAX_XP)} XP**. (Level **{_MAX_LEVEL}**)')
                return

            level = guild_config.spec.xp_requirement_for(flags.xp)
            xp = flags.xp

        await config.update(xp=xp, level=level)

        await ctx.send(f'**{target}** is now level **{level}** with **{fnumb(xp)}** total XP. {self.emoji}')

    @level.group(
        'config',
        fallback='view',
        description='Leveling Configuration Commands.',
        guild_only=True,
        hybrid=True
    )
    async def level_config(self, ctx: Context) -> None:
        """Leveling Configuration Commands."""
        config: GuildLevelConfig = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        match config.level_up_channel:
            case 0:
                channel = 'Don\'t send'
            case 1:
                channel = 'Source Channel'
            case 2:
                channel = 'DMs'
            case custom:
                channel = f'<#{custom}>'

        default = '*N/A*'

        def to_emoji(val: bool) -> str:
            return Emojis.success if val else Emojis.error

        level_roles = '\n'.join(
            f'- Level **{level}**: <@&{role}>' for role, level in config.level_roles.items()) or default
        embed = discord.Embed(
            title='Leveling Configuration',
            colour=helpers.Colour.white(),
            description=f'**Enabled:** {to_emoji(config.enabled)} `{config.enabled}`\n'
                        f'**Delete User Data After Leave:** {to_emoji(config.delete_after_leave)} `{config.delete_after_leave}`\n'
                        f'**Level Up Message:** ```\n{config.level_up_message}```\n'
                        f'**Level Up Channel:** {channel}\n\n'
                        f'**Level Roles:**\n'
                        f'{level_roles}')

        cooldown = config.cooldown_manager
        embed.add_field(
            name='Cooldown',
            value=f'{cooldown.rate} time(s) per {humanize_duration(cooldown.per)}',
            inline=False)

        embed.add_field(
            name='Blacklisted Roles',
            value=truncate(', '.join(f'<@&{role}>' for role in config.blacklisted_roles) or default, 1024),
            inline=False)
        embed.add_field(
            name='Blacklisted Channels',
            value=truncate(', '.join(f'<#{channel}>' for channel in config.blacklisted_channels) or default, 1024),
            inline=False)
        embed.add_field(
            name='Blacklisted Users',
            value=truncate(', '.join(f'<@{user}>' for user in config.blacklisted_users) or default, 1024),
            inline=False)

        embed.add_field(
            name='Multiplier Roles',
            value=truncate(', '.join(
                f'<@&{role}>: **{multiplier}**' for role, multiplier in config.multiplier_roles.items()) or default,
                           1024),
            inline=False)

        embed.add_field(
            name='Multiplier Channels',
            value=truncate(', '.join(f'<#{channel}>: **{multiplier}**' for channel, multiplier in
                                     config.multiplier_channels.items()) or default, 1024),
            inline=False)

        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text='Leveling Configuration for this Server.')
        await ctx.send(embed=embed)

    @level_config.command(
        'toggle',
        description='Toggle leveling on or off.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(enabled='Boolean to enable or disable leveling. If not provided, it will toggle.')
    async def level_config_toggle(self, ctx: Context, enabled: bool) -> None:
        """Toggle leveling on or off."""
        config = await self.get_guild_level_config(ctx.guild.id)
        if enabled:
            if config is not None:
                await config.update(enabled=True)
            else:
                query = "INSERT INTO level_config (id, enabled) VALUES ($1, $2) RETURNING *;"
                await ctx.db.fetchrow(query, ctx.guild.id, enabled)
                self.get_guild_level_config.invalidate(ctx.guild.id)
        else:
            if not config:
                await ctx.send_error('Leveling is already disabled.')
                return

            await config.update(enabled=False)

        fmt = '*enabled*' if enabled else '*disabled*'
        await ctx.send_success(f'Leveling {fmt}.')

    @level_config.command(
        'delete-after-leave',
        description='Toggle deleting user data after leave.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(delete='Boolean to enable or disable deleting user data after leave.')
    async def level_config_delete_after_leave(self, ctx: Context, delete: bool) -> None:
        """Toggle deleting user data after leave."""
        config = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        await config.update(delete_after_leave=delete)
        fmt = '*enabled*' if delete else '*disabled*'
        await ctx.send_success(f'Deleting user data after leave {fmt}.')

    @level_config.command(
        'roles',
        description='Set the level roles for the server.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    async def level_config_roles(self, ctx: Context) -> None:
        """Set the level up message for the server."""
        config = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        view = InteractiveLevelRolesView(ctx, config=config)
        await ctx.send(embed=view.make_embed(), view=view)

    @level_config.command(
        'message',
        description='Set the level up message for the server (Use {level} for the level and {user} for the user).',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(message='The message to set the level up message to.')
    async def level_config_message(self, ctx: Context, *, message: str) -> None:
        """Set the level up message for the server.

        Use {level} for the level and {user} for the user.
        """
        config = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        await config.update(level_up_message=message)
        await ctx.send_success('Level up message has been updated.')

    @level_config.command(
        'channel',
        description='Set the level up channel for the server.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(channel='The channel to set the level up channel to.')
    async def level_config_channel(self, ctx: Context, channel: AnyLevelChannel = None) -> None:
        """Set the level up channel for the server.

        Leave `channel` empty to don't send level up messages, use `dm` for DMs
        and `channel` for the current channel or provide a channel.

        Note: To set the channel to dm or current channel, please use the text command version of this command.
        """
        config = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        if channel is None:
            channel_id = 0
        else:
            assert isinstance(channel, (discord.TextChannel, str))
            match channel:
                case 'dm':
                    channel_id = 2
                case 'channel':
                    channel_id = 1
                case _:
                    channel_id = channel.id

        await config.update(level_up_channel=channel_id)
        await ctx.send_success('Level up channel has been updated.')

    @level_config.command(
        'ignore',
        description='Set ignorable entities for the leveling system.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(entities='The entities to ignore.')
    async def level_config_ignore(
            self, ctx: Context, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]) -> None:
        """Set ignorable entities for the leveling system.

        You can ignore roles, channels and users from the leveling system.
        """
        config: GuildLevelConfig = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        roles = set()
        channels = set()
        users = set()

        for entity in entities:
            if isinstance(entity, discord.Role):
                roles.add(entity.id)
            elif isinstance(entity, discord.TextChannel):
                channels.add(entity.id)
            elif isinstance(entity, discord.Member):
                users.add(entity.id)

        await config.merge(
            blacklisted_roles=roles,
            blacklisted_channels=channels,
            blacklisted_users=users
        )
        await ctx.send_success('Blacklisted entities have been updated.')

    @level_config.command(
        'unignore',
        description='Unset ignored entities for the leveling system.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    @describe(entities='The entities to unignore.')
    async def level_config_unignore(
            self, ctx: Context, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]) -> None:
        """Unset ignored entities for the leveling system."""
        config: GuildLevelConfig = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        roles = set()
        channels = set()
        users = set()

        for entity in entities:
            if isinstance(entity, discord.Role):
                roles.add(entity.id)
            elif isinstance(entity, discord.TextChannel):
                channels.add(entity.id)
            elif isinstance(entity, discord.Member):
                users.add(entity.id)

        await config.update(
            blacklisted_roles=config.blacklisted_roles - roles,
            blacklisted_channels=config.blacklisted_channels - channels,
            blacklisted_users=config.blacklisted_users - users
        )
        await ctx.send_success('Blacklisted entities have been updated.')

    @level_config.command(
        'multiplier',
        description='Set the multiplier roles for the server.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager
    )
    async def level_config_multipliers(self, ctx: Context) -> None:
        """Set the multiplier roles for the server."""
        config = await self.get_guild_level_config(ctx.guild.id)
        if not config:
            await ctx.send_error('Leveling is not enabled in this server.')
            return

        view = InteractiveMultiplierView(ctx, config=config)
        await ctx.send(embed=view.make_embed(), view=view)


async def setup(bot) -> None:
    await bot.add_cog(Leveling(bot))
