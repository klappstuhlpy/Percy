from __future__ import annotations

import asyncio
import datetime
import random
from typing import Optional, TypedDict

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import Percy
from .utils.commands_ext import PermissionTemplate
from .mod import AutoModFlags
from .utils import cache, commands_ext
from .utils.context import Context, GuildContext
from .utils.formats import medal_emojize
from .utils.helpers import PostgresItem
from .utils.render import Render
from launcher import get_logger


class LevelConfig(PostgresItem):
    """Represents a level configuration for a guild."""

    user_id: int
    guild_id: int
    messages: int
    experience: int
    voice_minutes: int

    __slots__ = ('cog', 'bot', 'user_id', 'guild_id', 'messages', 'experience', 'voice_minutes')

    def __init__(self, cog: Leveling, **kwargs) -> None:
        self.cog: Leveling = cog
        self.bot: Percy = cog.bot

        super().__init__(**kwargs)

    def __len__(self):
        return self.messages

    def __int__(self):
        return self.experience

    def __str__(self):
        return f"{self.experience:,}"

    @property
    def level(self) -> int:
        """Returns the current level of a user from their total experience."""
        level = 0

        while (self.experience - self.get_experience(level)) >= self.get_required(level):
            level += 1

        return level

    @staticmethod
    def get_experience(level: int) -> int:
        """Returns the total experience required for reaching a certain level."""
        return (level ** 3) + (104 * level)

    @staticmethod
    def get_required(level: int) -> int:
        """Returns the experience required for reaching the next level."""
        return (3 * level ** 2) + (3 * level) + 105

    async def get_rank(self, *, connection: Optional[asyncpg.Connection] = None) -> int:
        con = connection or self.bot.pool

        query = """
            SELECT rank FROM ( 
                SELECT user_id, guild_id, row_number() OVER (ORDER BY experience DESC) AS rank 
                FROM levels
                WHERE guild_id = $2
            ) AS rank
            WHERE user_id = $1 AND guild_id = $2
            LIMIT 1;
            """
        record = await con.fetchval(query, self.user_id, self.guild_id)
        return record

    async def add_experience(self, experience: int) -> None:
        self.experience += experience
        await self.send_patch()

    async def add_messages(self, messages: int) -> None:
        self.messages += messages
        await self.send_patch()

    async def set_level(self, level: int) -> None:
        self.experience = self.get_experience(level)
        await self.send_patch()

    async def set_experience(self, experience: int) -> None:
        self.experience = experience
        await self.send_patch()

    async def add_voice_minutes(self, voice_minutes: int, multiplier: int) -> None:
        if voice_minutes <= 0:
            return
        self.voice_minutes = self.voice_minutes or 0
        self.voice_minutes += voice_minutes
        self.experience += round(voice_minutes * multiplier)
        await self.send_patch()

    async def send_patch(self):
        async with self.cog.batch_lock:
            self.cog.batch_data.append(
                {
                    'guild_id': self.guild_id,
                    'user_id': self.user_id,
                    'messages': self.messages,
                    'experience': self.experience,
                    'voice_minutes': self.voice_minutes
                }
            )
            self.cog.get_level_config.refactor(self.user_id, self.guild_id, replace=self)


log = get_logger(__name__)


class DataBatchEntry(TypedDict):
    user_id: int
    guild_id: int
    messages: int
    experience: int
    voice_minutes: int


class PointsWatch(TypedDict):
    started: datetime.datetime
    muted: bool


class OverwriteList(list):

    def append(self, other: DataBatchEntry):
        if existing := discord.utils.find(
                lambda x: x['user_id'] == other['user_id'] and x['guild_id'] == other['guild_id'], self):
            self[self.index(existing)] = other
        else:
            super().append(other)


class Leveling(commands.Cog):
    """Leveling system, commands and utilities."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.render: Render = Render()

        # Better for those two variables to be global, not private to
        # access them from the :class:`LevelConfig` class.
        self.batch_lock = asyncio.Lock()
        self.batch_data: OverwriteList[DataBatchEntry] = OverwriteList()

        self._voicebatch_data: dict[int, PointsWatch] = {}

        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

        self.message_cooldown = commands.CooldownMapping.from_cooldown(1, 60, commands.BucketType.member)

    async def bulk_insert(self) -> None:
        query = """
                INSERT INTO levels (guild_id, user_id, messages, experience, voice_minutes)
                SELECT x.guild_id, x.user_id, x.messages, x.experience, x.voice_minutes
                FROM jsonb_to_recordset($1::jsonb) AS
                x(
                    guild_id BIGINT,
                    user_id BIGINT,
                    messages INTEGER,
                    experience INTEGER,
                    voice_minutes INTEGER
                )
                ON CONFLICT (guild_id, user_id) DO UPDATE
                SET messages = excluded.messages,
                    experience = excluded.experience,
                    voice_minutes = excluded.voice_minutes
            """

        if self.batch_data:
            await self.bot.pool.execute(query, self.batch_data)
            total = len(self.batch_data)
            if total > 1:
                log.debug('Registered %s leveling batches to the database.', total)
            self.batch_data.clear()

    def cog_unload(self):
        self.bulk_insert_loop.stop()

    @tasks.loop(seconds=15.0)
    async def bulk_insert_loop(self):
        async with self.batch_lock:
            await self.bulk_insert()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="oneup", id=1113286994378899516)

    @cache.cache(strategy=cache.Strategy.ADDITIVE)
    async def get_level_config(self, user_id: int, guild_id: int) -> Optional[LevelConfig]:
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
        Optional[:class:`LevelConfig`]
            The level config for the given user and guild.
        """
        query = "SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;"
        record: asyncpg.Record = await self.bot.pool.fetchrow(query, user_id, guild_id)

        if not record:
            query = "INSERT INTO levels (user_id, guild_id) VALUES ($1, $2) RETURNING *;"
            record: asyncpg.Record = await self.bot.pool.fetchrow(query, user_id, guild_id)

        return LevelConfig(self, record=record)

    @commands.Cog.listener()
    async def on_voice_state_update(
            self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return

        mod_config = await self.bot.moderation.get_guild_config(member.guild.id)

        if not mod_config:
            return

        if not mod_config.flags.leveling:
            return

        config = await self.get_level_config(member.id, member.guild.id)

        if before.mute != after.mute:
            if member.id in self._voicebatch_data:
                total_time = discord.utils.utcnow() - self._voicebatch_data[member.id]['started']
                total_minutes = total_time.total_seconds() // 60
                await config.add_voice_minutes(
                    total_minutes, 2 if self._voicebatch_data[member.id]['muted'] else 7)

                self._voicebatch_data[member.id]['started'] = discord.utils.utcnow()
                self._voicebatch_data[member.id]['muted'] = after.self_mute

            return

        if before.channel:
            if member.id in self._voicebatch_data:
                total_time = discord.utils.utcnow() - self._voicebatch_data[member.id]['started']
                total_minutes = total_time.total_seconds() // 60
                await config.add_voice_minutes(
                    total_minutes, 2 if self._voicebatch_data[member.id]['muted'] else 7)

        if after.channel:
            self._voicebatch_data[member.id] = {
                'started': discord.utils.utcnow(),
                'muted': after.self_mute
            }
        else:
            try:
                del self._voicebatch_data[member.id]
            except KeyError:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return

        if message.author.bot:
            return

        mod_config = await self.bot.moderation.get_guild_config(message.guild.id)

        if not mod_config:
            return

        if not mod_config.flags.leveling:
            return

        if any(user.bot for user in message.mentions):
            return

        if len(message.content) <= 3:
            return

        bucket = self.message_cooldown.get_bucket(message)
        retry_after = bucket.update_rate_limit()

        if retry_after:
            return

        config = await self.get_level_config(message.author.id, message.guild.id)
        await config.add_messages(1)

        experience = random.randint(7, 13)
        leveled_up = config.experience + experience - config.get_experience(config.level) >= config.get_required(
            config.level)
        await config.add_experience(experience)

        if leveled_up:
            await message.reply(
                f"*Congratulations {message.author.mention}!* You leveled up to level **{config.level}**! <:oneup:1113286994378899516>")

    @commands_ext.command(commands.hybrid_group, name="level", description="Leveling purpose Commands.")
    @commands.guild_only()
    async def level(self, ctx: Context) -> None:
        """Leveling purpose Commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands_ext.command(level.command, description="Toggle leveling on or off.")
    @app_commands.describe(enabled="Boolean to enable or disable leveling. If not provided, it will toggle.")
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.manager)
    async def toggle(self, ctx: GuildContext, enabled: Optional[bool] = None) -> None:
        """Toggle leveling on or off."""
        query = """
            INSERT INTO guild_config (id, flags)
            VALUES ($1, $2) ON CONFLICT (id)
            DO UPDATE SET
                -- If we're toggling then we need to negate the previous result
                flags = CASE COALESCE($3, NOT (guild_config.flags & $2 = $2))
                                WHEN TRUE THEN guild_config.flags | $2
                                WHEN FALSE THEN guild_config.flags & ~$2
                        END
            RETURNING COALESCE($3, (flags & $2 = $2));
        """
        row: Optional[tuple[bool]] = await ctx.db.fetchrow(query, ctx.guild.id, AutoModFlags.leveling.flag, enabled)
        enabled = row and row[0]
        self.bot.moderation.get_guild_config.invalidate(self, ctx.guild.id)
        fmt = '*enabled*' if enabled else '*disabled*'
        await ctx.send_tick(True, f'Leveling {fmt}.')

    @commands_ext.command(level.command, aliases=['top'], description="View the server leaderboard.")
    @commands.guild_only()
    async def leaderboard(self, ctx: GuildContext):
        """View the Top 3 active users of the server."""
        query = "SELECT * FROM levels WHERE guild_id = $1 AND messages > 0 ORDER BY messages DESC LIMIT 3;"
        records = [LevelConfig(self, record=record) for record in await self.bot.pool.fetch(query, ctx.guild.id)]

        e = discord.Embed(colour=self.bot.colour.darker_red(), title=f'Level Statistics for {ctx.guild.name}')
        e.set_thumbnail(url=ctx.guild.icon.url)
        e.set_footer(text='Level Statistics for this Server.')

        if not records:
            value = '*There are no statistics for this category available.*'
        else:
            value = '\n'.join(
                [f'{emoji}: <@{record.user_id}> • LV **{record.level}** • (**{record.messages}** messages)'
                 for emoji, record in medal_emojize(records)])

        e.add_field(name=f'**TOP 3 TEXT 💬**', value=value, inline=False)

        query = "SELECT * FROM levels WHERE guild_id = $1 AND voice_minutes > 0 ORDER BY voice_minutes DESC LIMIT 3;"
        records = [LevelConfig(self, record=record) for record in await self.bot.pool.fetch(query, ctx.guild.id)]

        if not records:
            value = '*There are no statistics for this category available.*'
        else:
            value = '\n'.join(
                [f'{emoji}: <@{record.user_id}> • LV **{record.level}** • (**{record.voice_minutes}** minutes)'
                 for emoji, record in medal_emojize(records)])

        e.add_field(name=f'**TOP 3 VOICE 🎙️**', value=value, inline=False)

        await ctx.send(embed=e)

    @commands_ext.command(level.command, description="View your level card.")
    @commands.guild_only()
    @app_commands.describe(member="The member you want to see the rank card for.")
    async def rank(self, ctx: GuildContext, member: Optional[discord.Member] = None):
        """View yours or someone else's rank card."""
        member = member or ctx.author

        if member.bot:
            return await ctx.send(f"{ctx.tick(False)} You can't view the rank card of a bot.")

        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        config = await self.get_level_config(member.id, member.guild.id)

        card = await self.render.generate_rank_card(
            avatar=await member.display_avatar.read(),
            user=member,
            level=config.level,
            current=config.experience - config.get_experience(config.level),
            required=config.get_required(config.level),
            rank=await config.get_rank(),
            members=sum(not member.bot for member in ctx.guild.members),
            messages=config.messages,
        )
        await ctx.send(file=discord.File(fp=card, filename="rank.png"))

    @commands_ext.command(
        level.command,
        description="Set a members experience or level."
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
    @app_commands.describe(target="The target member to modify.")
    @app_commands.describe(level="The level you want to set.")
    @app_commands.describe(xp="The amount of XP you want to set.")
    async def set(
            self,
            ctx: GuildContext,
            target: discord.Member,
            xp: app_commands.Range[int, 0, 125052000] | None = None,
            level: app_commands.Range[int, 0, 500] | None = None
    ):
        """Set a users experience/level."""
        if target.bot:
            return await ctx.send(f"{ctx.tick(False)} You can't manage Bot's Level/Experience.")

        if (xp is None and level is None) or (xp and level):
            return await ctx.send(f"{ctx.tick(False)} You need to provide either a level or xp to set.")

        config = await self.get_level_config(target.id, target.guild.id)

        if level:
            if level > 500:
                return await ctx.send(f"{ctx.tick(False)} You can't set more than **Level 500**.")

            await config.set_level(level)
        elif xp:
            if xp > config.get_experience(500):
                return await ctx.send(
                    f"{ctx.tick(False)} Sorry. You can't set more than **125,052,000 XP**. (Level 500)")

            await config.set_experience(xp)

        await ctx.send(
            f"{target.mention} is now level **{config.level}** with **{str(config)}** total XP. <:oneup:1113286994378899516>"
        )


async def setup(bot: Percy) -> None:
    await bot.add_cog(Leveling(bot))
