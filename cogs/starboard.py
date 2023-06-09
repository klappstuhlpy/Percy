from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time
import weakref
from typing import TYPE_CHECKING, Callable, Literal, Optional, Any, Union, List, NamedTuple

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing_extensions import Annotated

from cogs.utils.paginator import BasePaginator
from . import command, command_permissions
from .utils import checks, cache
from .utils.formats import plural
from .utils.helpers import PostgresItem
from .utils.scope import StarableChannel

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import GuildContext

    class StarboardContext(GuildContext):
        starboard: CompleteStarboardConfig


log = logging.getLogger(__name__)


class StarError(commands.CheckFailure):
    pass


def requires_starboard():
    async def predicate(ctx: StarboardContext) -> bool:
        if ctx.guild is None:
            return False

        cog: Starboard = ctx.bot.get_cog('Starboard')  # type: ignore

        ctx.starboard = await cog.get_starboard(ctx.guild.id)  # type: ignore
        if ctx.starboard.channel is None:
            raise StarError('<:redTick:1079249771975413910> Starboard channel not found.')

        return True

    return commands.check(predicate)


def MessageID(argument: str) -> int:
    try:
        return int(argument, base=10)
    except ValueError:
        raise StarError(f'"{argument}" is not a valid message ID. Use Developer Mode to get the Copy ID option.')


class StarMessage(NamedTuple):
    channel_id: int
    message_id: int
    bot_message_id: int
    stars: int


class StarboardConfig(PostgresItem, ignore_record=True):
    """Represents a starboard configuration for a guild."""

    id: int
    bot: Percy
    channel_id: Optional[int]
    threshold: int
    locked: bool
    needs_migration: bool
    max_age: datetime.timedelta
    created_at: datetime.datetime

    __slots__ = ('bot', 'id', 'channel_id', 'threshold', 'locked', 'needs_migration', 'max_age', 'created_at')

    def __init__(self, guild_id: int, bot: Percy, **kwargs):
        self.id: int = guild_id
        self.bot: Percy = bot

        super().__init__(**kwargs)

        if not self.record:
            self.channel_id: Optional[int] = None

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(self.id)
        return guild and guild.get_channel(self.channel_id)  # type: ignore

    def verify_message(self, message: discord.Message) -> bool:
        if message.author == self.bot.user:
            if message.embeds:
                valid_msg = re.compile(r'^#(\w{1,100})$')
                if valid_msg.match(message.embeds[0].footer.text):
                    return True
        return False


if TYPE_CHECKING:
    class CompleteStarboardConfig(StarboardConfig):
        channel: discord.TextChannel


class Starboard(commands.Cog):
    """A starboard to upvote posts by other users.

    React to a message with \N{WHITE MEDIUM STAR} and
    the bot will automatically add (or remove) it to the starboard.
    You can also Mark them via their message ID with the `star post` command.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._message_cache: dict[int, discord.Message] = {}
        self.clean_message_cache.start()
        self._about_to_be_deleted: set[int] = set()

        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self.spoilers = re.compile(r'\|\|(.+?)\|\|')

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="sparkle", id=1103419648831135934, animated=True)

    def cog_unload(self):
        self.clean_message_cache.cancel()

    async def cog_command_error(self, ctx: StarboardContext, error: commands.CommandError):
        if isinstance(error, StarError):
            await ctx.send(str(error), ephemeral=True)

    @tasks.loop(hours=1.0)
    async def clean_message_cache(self):
        self._message_cache.clear()

    @cache.cache()
    async def get_starboard(
            self, guild_id: int, *, connection: Optional[asyncpg.Pool | asyncpg.Connection] = None
    ) -> StarboardConfig:
        connection = connection or self.bot.pool
        query = "SELECT * FROM starboard WHERE id=$1;"
        record = await connection.fetchrow(query, guild_id)
        return StarboardConfig(guild_id=guild_id, bot=self.bot, record=record)

    @staticmethod
    def star_emoji(stars: int) -> str:
        if 5 > stars >= 0:
            return '\N{WHITE MEDIUM STAR}'
        elif 10 > stars >= 5:
            return '\N{GLOWING STAR}'
        elif 25 > stars >= 10:
            return '\N{DIZZY SYMBOL}'
        else:
            return '\N{SPARKLES}'

    @staticmethod
    def star_gradient_colour(stars: int) -> int:
        # We define as 13 stars to be 100% of the star gradient (half of the 26 emoji threshold)
        # So X / 13 will clamp to our percentage,
        # We start out with 0xfffdf7 for the beginning colour
        # Gradually evolving into 0xffc20c
        # rgb values are (255, 253, 247) -> (255, 194, 12)
        # To create the gradient, we use a linear interpolation formula
        # Which for reference is X = X_1 * p + X_2 * (1 - p)
        p = stars / 13
        if p > 1.0:
            p = 1.0

        red = 255
        green = int((194 * p) + (253 * (1 - p)))
        blue = int((12 * p) + (247 * (1 - p)))
        return (red << 16) + (green << 8) + blue

    def is_url_spoiler(self, text: str, url: str) -> bool:
        spoilers = self.spoilers.findall(text)
        for spoiler in spoilers:
            if url in spoiler:
                return True
        return False

    def get_emoji_message(self, message: discord.Message, stars: int) -> (str, discord.Embed):
        assert isinstance(message.channel, (discord.abc.GuildChannel, discord.Thread))
        emoji = self.star_emoji(stars)

        embed = discord.Embed(description=message.content)
        if message.embeds:
            data = message.embeds[0]
            if data.type == 'image' and data.url and not self.is_url_spoiler(message.content, data.url):
                embed.set_image(url=data.url)

        if message.attachments:
            file = message.attachments[0]
            spoiler = file.is_spoiler()
            if not spoiler and file.url.lower().endswith(('png', 'jpeg', 'jpg', 'gif', 'webp')):
                embed.set_image(url=file.url)
            elif spoiler:
                embed.add_field(name='Attachment', value=f'||[{file.filename}]({file.url})||', inline=False)
            else:
                embed.add_field(name='Attachment', value=f'[{file.filename}]({file.url})', inline=False)

        ref = message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            embed.add_field(name='Replied to', value=f'{ref.resolved.author}: {ref.resolved.jump_url}', inline=False)

        if stars > 1:
            content = f'> {emoji} • **{stars}** • {message.channel.mention}'
        else:
            content = f'> {emoji} • {message.channel.mention}'

        embed.add_field(name='*Jump to*', value=message.jump_url, inline=False)
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.set_footer(text=f'[{message.id}]')
        embed.timestamp = message.created_at
        embed.colour = self.star_gradient_colour(stars)
        return content, embed

    async def get_stars(self, message_id: int) -> Optional[StarMessage]:
        query = """
            SELECT entry.channel_id,
                  entry.message_id,
                  entry.bot_message_id,
                  COUNT(*) OVER(PARTITION BY entry_id) AS "Stars"
            FROM starrers
            INNER JOIN starboard_entries entry
            ON entry.id = starrers.entry_id
            WHERE entry.guild_id=$1
            AND (entry.message_id=$2 OR entry.bot_message_id=$2)
            LIMIT 1
        """
        record = await self.bot.pool.fetchrow(query, message_id)
        if record:
            return StarMessage(*record)
        return None

    async def get_message(self, channel: discord.abc.Messageable, message_id: int) -> Optional[discord.Message]:
        try:
            return self._message_cache[message_id]
        except KeyError:
            try:
                msg = await channel.fetch_message(message_id)
            except discord.HTTPException:
                return None
            else:
                self._message_cache[message_id] = msg
                return msg

    async def reaction_action(self, fmt: str, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != '\N{WHITE MEDIUM STAR}':
            return

        guild = self.bot.get_guild(payload.guild_id)  # type: ignore
        if guild is None:
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            return

        method = getattr(self, f'{fmt}_message')

        user = payload.member or (await self.bot.get_or_fetch_member(guild, payload.user_id))
        if user is None or user.bot:
            return

        try:
            await method(channel, payload.message_id, payload.user_id, verify=True)
        except StarError:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel):
            return

        starboard = await self.get_starboard(channel.guild.id)
        if starboard.channel is None or starboard.channel.id != channel.id:
            return

        async with self.bot.pool.acquire(timeout=300.0) as con:
            query = "DELETE FROM starboard WHERE id=$1;"
            await con.execute(query, channel.guild.id)
        self.get_starboard.invalidate(channel.guild.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction_action('star', payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reaction_action('unstar', payload)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.message_id in self._about_to_be_deleted:
            self._about_to_be_deleted.discard(payload.message_id)
            return

        starboard = await self.get_starboard(payload.guild_id)
        if starboard.channel is None or starboard.channel.id != payload.channel_id:
            return

        async with self.bot.pool.acquire(timeout=300.0) as con:
            query = "DELETE FROM starboard_entries WHERE bot_message_id=$1;"
            await con.execute(query, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.message_ids <= self._about_to_be_deleted:
            self._about_to_be_deleted.difference_update(payload.message_ids)
            return

        starboard = await self.get_starboard(payload.guild_id)
        if starboard.channel is None or starboard.channel.id != payload.channel_id:
            return

        async with self.bot.pool.acquire(timeout=300.0) as con:
            query = "DELETE FROM starboard_entries WHERE bot_message_id=ANY($1::bigint[]);"
            await con.execute(query, list(payload.message_ids))

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id)  # type: ignore
        if guild is None:
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None or not isinstance(channel, (discord.Thread, discord.TextChannel)):
            return

        async with self.bot.pool.acquire(timeout=300.0) as con:
            starboard = await self.get_starboard(channel.guild.id, connection=con)
            if starboard.channel is None:
                return

            query = "DELETE FROM starboard_entries WHERE message_id=$1 RETURNING bot_message_id;"
            bot_message_id = await con.fetchrow(query, payload.message_id)

            if bot_message_id is None:
                return

            bot_message_id = bot_message_id[0]
            msg = await self.get_message(starboard.channel, bot_message_id)
            if msg is not None:
                await msg.delete()

    async def star_message(
            self,
            channel: StarableChannel,
            message_id: int,
            starrer_id: int,
            *,
            verify: bool = False,
    ) -> None:
        guild_id = channel.guild.id
        lock = self._locks.get(guild_id)
        if lock is None:
            self._locks[guild_id] = lock = asyncio.Lock()

        async with lock:
            async with self.bot.pool.acquire(timeout=300.0) as con:
                if verify:
                    config = self.bot.config_cog
                    if config:
                        plonked = await config.is_plonked(guild_id, starrer_id, channel=channel, connection=con)
                        if plonked:
                            return
                        perms = await config.get_command_permissions(guild_id, connection=con)
                        if perms.is_command_blocked('star', channel.id):
                            return

                await self._star_message(channel, message_id, starrer_id, connection=con)

    async def _star_message(
            self,
            channel: StarableChannel,
            message_id: int,
            starrer_id: int,
            *,
            connection: asyncpg.Connection,
    ) -> None:
        """Stars a message.

        Parameters
        ------------
        channel: Union[:class:`TextChannel`, :class:`VoiceChannel`, :class:`Thread`]
            The channel that the starred message belongs to.
        message_id: int
            The message ID of the message being starred.
        starrer_id: int
            The ID of the person who starred this message.
        connection: asyncpg.Connection
            The connection to use.
        """
        record: Any
        guild_id = channel.guild.id
        starboard = await self.get_starboard(guild_id)
        starboard_channel = starboard.channel
        if starboard_channel is None:
            raise StarError('<:redTick:1079249771975413910> Starboard channel not found.')

        if starboard.locked:
            raise StarError('<:redTick:1079249771975413910> Starboard is locked.')

        if channel.is_nsfw() and not starboard_channel.is_nsfw():
            raise StarError('<:redTick:1079249771975413910> Cannot star NSFW in non-NSFW starboard channel.')

        if channel.id == starboard_channel.id:
            query = "SELECT channel_id, message_id FROM starboard_entries WHERE bot_message_id=$1;"
            record = await connection.fetchrow(query, message_id)
            if record is None:
                raise StarError('<:redTick:1079249771975413910> Could not find message in the starboard.')

            ch = channel.guild.get_channel_or_thread(record['channel_id'])
            if ch is None:
                raise StarError('<:redTick:1079249771975413910> Could not find original channel.')

            return await self._star_message(ch, record['message_id'], starrer_id, connection=connection)  # type: ignore

        if not starboard_channel.permissions_for(starboard_channel.guild.me).send_messages:
            raise StarError('<:redTick:1079249771975413910> Cannot post messages in starboard channel.')

        msg = await self.get_message(channel, message_id)

        if msg is None:
            raise StarError('<:redTick:1079249771975413910> This message could not be found.')

        if msg.author.id == starrer_id:
            raise StarError('<:redTick:1079249771975413910> You cannot star your own message.')

        empty_message = len(msg.content) == 0 and len(msg.attachments) == 0
        if empty_message or msg.type not in (discord.MessageType.default, discord.MessageType.reply):
            raise StarError('<:redTick:1079249771975413910> This message cannot be starred.')

        oldest_allowed = discord.utils.utcnow() - starboard.max_age
        if msg.created_at < oldest_allowed:
            raise StarError('<:redTick:1079249771975413910> This message is too old.')

        query = """
            WITH to_insert AS (
               INSERT INTO starboard_entries AS entries (message_id, channel_id, guild_id, author_id)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (message_id) DO NOTHING
               RETURNING entries.id
            )
            INSERT INTO starrers (author_id, entry_id)
            SELECT $5, entry.id
            FROM (
               SELECT id FROM to_insert
               UNION ALL
               SELECT id FROM starboard_entries WHERE message_id=$1
               LIMIT 1
            ) AS entry
            RETURNING entry_id;
        """

        try:
            record = await connection.fetchrow(
                query,
                message_id,
                channel.id,
                guild_id,
                msg.author.id,
                starrer_id,
            )
        except asyncpg.UniqueViolationError:
            raise StarError('<:redTick:1079249771975413910> You already starred this message.')

        entry_id = record[0]

        query = "SELECT COUNT(*) FROM starrers WHERE entry_id=$1;"
        record = await connection.fetchrow(query, entry_id)

        count = record[0]
        if count < starboard.threshold:
            return

        content, embed = self.get_emoji_message(msg, count)

        query = "SELECT bot_message_id FROM starboard_entries WHERE message_id=$1;"
        record = await connection.fetchrow(query, message_id)
        bot_message_id = record[0]

        if bot_message_id is None:
            new_msg = await starboard_channel.send(content, embed=embed)
            query = "UPDATE starboard_entries SET bot_message_id=$1 WHERE message_id=$2;"
            await connection.execute(query, new_msg.id, message_id)
        else:
            new_msg = await self.get_message(starboard_channel, bot_message_id)
            if new_msg is None:
                query = "DELETE FROM starboard_entries WHERE message_id=$1;"
                await connection.execute(query, message_id)
            else:
                await new_msg.edit(content=content, embed=embed)

    async def unstar_message(
            self,
            channel: StarableChannel,
            message_id: int,
            starrer_id: int,
            *,
            verify: bool = False,
    ) -> None:
        guild_id = channel.guild.id
        lock = self._locks.get(guild_id)
        if lock is None:
            self._locks[guild_id] = lock = asyncio.Lock()

        async with lock:
            async with self.bot.pool.acquire(timeout=300.0) as con:
                if verify:
                    config = self.bot.config_cog
                    if config:
                        plonked = await config.is_plonked(guild_id, starrer_id, channel=channel, connection=con)
                        if plonked:
                            return
                        perms = await config.get_command_permissions(guild_id, connection=con)
                        if perms.is_command_blocked('star', channel.id):
                            return

                await self._unstar_message(channel, message_id, starrer_id, connection=con)

    async def _unstar_message(
            self,
            channel: StarableChannel,
            message_id: int,
            starrer_id: int,
            *,
            connection: asyncpg.Connection,
    ) -> None:
        """Unstars a message.

        Parameters
        ------------
        channel: Union[:class:`TextChannel`, :class:`VoiceChannel`, :class:`Thread`]
            The channel that the starred message belongs to.
        message_id: int
            The message ID of the message being unstarred.
        starrer_id: int
            The ID of the person who unstarred this message.
        connection: asyncpg.Connection
            The connection to use.
        """
        record: Any
        guild_id = channel.guild.id
        starboard = await self.get_starboard(guild_id)
        starboard_channel = starboard.channel
        if starboard_channel is None:
            raise StarError('<:redTick:1079249771975413910> Starboard channel not found.')

        if starboard.locked:
            raise StarError('<:redTick:1079249771975413910> Starboard is locked.')

        if channel.id == starboard_channel.id:
            query = "SELECT channel_id, message_id FROM starboard_entries WHERE bot_message_id=$1;"
            record = await connection.fetchrow(query, message_id)
            if record is None:
                raise StarError('<:redTick:1079249771975413910> Could not find message in the starboard.')

            ch = channel.guild.get_channel_or_thread(record['channel_id'])
            if ch is None:
                raise StarError('<:redTick:1079249771975413910> Could not find original channel.')

            return await self._unstar_message(ch, record['message_id'], starrer_id,
                                              connection=connection)  # type: ignore

        if not starboard_channel.permissions_for(starboard_channel.guild.me).send_messages:
            raise StarError('<:redTick:1079249771975413910> Cannot edit messages in starboard channel.')

        query = """
            DELETE FROM starrers USING starboard_entries entry
            WHERE entry.message_id=$1
            AND   entry.id=starrers.entry_id
            AND   starrers.author_id=$2
            RETURNING starrers.entry_id, entry.bot_message_id
        """

        record = await connection.fetchrow(query, message_id, starrer_id)
        if record is None:
            raise StarError('<:redTick:1079249771975413910> You have not starred this message.')

        entry_id = record[0]
        bot_message_id = record[1]

        query = "SELECT COUNT(*) FROM starrers WHERE entry_id=$1;"
        record = await connection.fetchrow(query, entry_id)
        count = record[0]

        if count == 0:
            query = "DELETE FROM starboard_entries WHERE id=$1;"
            await connection.execute(query, entry_id)

        if bot_message_id is None:
            return

        bot_message = await self.get_message(starboard_channel, bot_message_id)
        if bot_message is None:
            return

        if count < starboard.threshold:
            self._about_to_be_deleted.add(bot_message_id)
            if count:
                query = "UPDATE starboard_entries SET bot_message_id=NULL WHERE id=$1;"
                await connection.execute(query, entry_id)

            await bot_message.delete()
        else:
            msg = await self.get_message(channel, message_id)
            if msg is None:
                raise StarError('<:redTick:1079249771975413910> This message could not be found.')

            content, embed = self.get_emoji_message(msg, count)
            await bot_message.edit(content=content, embed=embed)

    @command(
        commands.hybrid_group,
        name='starboard',
        fallback='create',
        description='Sets up the starboard for this server.',
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @app_commands.describe(name='The starboard channel name')
    async def starboard(self, ctx: GuildContext, *, name: str = 'starboard'):
        """Sets up the starboard for this server.

        This creates a new channel with the specified name
        and makes it into the server's "starboard". If no
        name is passed in then it defaults to "starboard".

        You must have Manage Server permission to use this.
        """

        await ctx.defer()

        self.get_starboard.invalidate(self, ctx.guild.id)

        starboard = await self.get_starboard(ctx.guild.id)
        if starboard.channel is not None:
            return await ctx.send(
                f'<:discord_info:1113421814132117545> This server already has a starboard ({starboard.channel.mention}).')

        if hasattr(starboard, 'locked'):
            try:
                confirm = await ctx.prompt(
                    '<:redTick:1079249771975413910> Apparently, a previously configured starboard channel was deleted. Is this true?'
                )
            except RuntimeError as e:
                await ctx.send(str(e))
            else:
                if confirm:
                    await ctx.db.execute('DELETE FROM starboard WHERE id=$1;', ctx.guild.id)
                else:
                    return await ctx.send(
                        '<:redTick:1079249771975413910> Aborting starboard creation. Join the bot support server for more questions.')

        perms = ctx.channel.permissions_for(ctx.me)

        if not perms.manage_roles or not perms.manage_channels:
            return await ctx.send(
                '<:redTick:1079249771975413910> I do not have proper permissions (Manage Roles and Manage Channel)')

        overwrites = {
            ctx.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_messages=True, embed_links=True,
                read_message_history=True
            ),
            ctx.guild.default_role: discord.PermissionOverwrite(
                read_messages=True, send_messages=False, read_message_history=True
            ),
        }

        reason = f'{ctx.author} (ID: {ctx.author.id}) has created the starboard channel.'

        try:
            channel = await ctx.guild.create_text_channel(name=name, overwrites=overwrites, reason=reason)
        except discord.Forbidden:
            return await ctx.send('<:redTick:1079249771975413910> I do not have permissions to create a channel.')
        except discord.HTTPException:
            return await ctx.send(
                '<:redTick:1079249771975413910> This channel name is bad or an unknown error happened.')

        query = "INSERT INTO starboard (id, channel_id, created_at) VALUES ($1, $2, $3);"
        try:
            await ctx.db.execute(query, ctx.guild.id, channel.id, discord.utils.utcnow())
        except:  # noqa
            await channel.delete(reason='Failure to commit to create the ')
            await ctx.send(
                '<:redTick:1079249771975413910> Could not create the channel due to an internal error. Join the bot support server for help.'
            )
        else:
            self.get_starboard.invalidate(self, ctx.guild.id)
            await ctx.send(f'<:greenTick:1079249732364406854> Starboard created at {channel.mention}. \N{GLOWING STAR}')

    @command(
        starboard.command,
        name='info',
        description='Shows meta information about the starboard.'
    )
    @requires_starboard()
    async def starboard_info(self, ctx: StarboardContext):
        """Shows meta information about the starboard."""
        starboard = ctx.starboard
        channel = starboard.channel

        e = discord.Embed(title=f"{ctx.guild.name}'s Starboard",
                          timestamp=ctx.starboard.created_at,
                          color=discord.Color.gold())
        e.set_thumbnail(url=ctx.guild.icon.url)
        e.set_footer(text=f'[{ctx.guild.id}]')

        if channel is None:
            e.add_field(name='Channel', value='#deleted-channel')
        else:
            e.add_field(name='Channel', value=channel.mention)
            e.add_field(name=f'Is NSFW:', value=channel.is_nsfw())

        e.add_field(name=f'Is Locked', value=starboard.locked)
        e.add_field(name=f'Limit', value=f'{plural(starboard.threshold):star}')
        e.add_field(name=f'Max Age', value=f'{plural(starboard.max_age.days):day}')

        await ctx.send(embed=e)

    @command(
        starboard.command,
        name='delete',
        description='Deletes the starboard for this server.'
    )
    @requires_starboard()
    @command_permissions(user=["ban_members", "manage_messages"])
    async def starboard_delete(self, ctx: StarboardContext):
        """Deletes the starboard for this server."""
        try:
            confirm = await ctx.prompt(
                '<:redTick:1079249771975413910> Are you sure you want to delete the starboard? This action is irreversible.',
                timeout=20
            )
        except RuntimeError as e:
            await ctx.send(str(e))
        else:
            if confirm:
                async with self.bot.pool.acquire(timeout=300.0) as con:
                    query = "DELETE FROM starboard WHERE id=$1;"
                    await con.execute(query, ctx.guild.id)
                await ctx.send('<:greenTick:1079249732364406854> Starboard deleted.')
                self.get_starboard.invalidate(ctx.guild.id)
            else:
                await ctx.send('<:redTick:1079249771975413910> Aborting starboard deletion.')

    @command(
        commands.hybrid_group,
        name='star',
        ignore_extra=False,
        description='Stars a message via message ID.',
        fallback='post'
    )
    @commands.guild_only()
    @app_commands.describe(message_id='The message ID to star')
    async def star(self, ctx: GuildContext, message_id: Annotated[int, MessageID]):
        """Stars a message via message ID.

        To star a message you should right click on the on a message and then
        click "Copy ID". You must have Developer Mode enabled to get that
        functionality.

        It is recommended that you react to a message with \N{WHITE MEDIUM STAR} instead.

        You can only star a message once.
        """
        await ctx.defer(ephemeral=True)
        try:
            await self.star_message(ctx.channel, message_id, ctx.author.id)
        except StarError as e:
            await ctx.send(str(e), ephemeral=True)
        else:
            if ctx.interaction is None:
                await ctx.message.delete()
            else:
                await ctx.send('<:greenTick:1079249732364406854> Successfully starred message', ephemeral=True)

    @command(
        commands.command,
        name='unstar',
        description='Unstars a message via message ID.'
    )
    @commands.guild_only()
    @app_commands.describe(message_id='The message ID to remove a star from')
    async def unstar(self, ctx: GuildContext, message_id: Annotated[int, MessageID]):
        """Unstars a message via message ID.

        To unstar a message you should right click on the on a message and then
        click "Copy ID". You must have Developer Mode enabled to get that
        functionality.
        """
        await ctx.defer(ephemeral=True)
        try:
            await self.unstar_message(ctx.channel, message_id, ctx.author.id, verify=True)
        except StarError as e:
            return await ctx.send(str(e), ephemeral=True)
        else:
            if ctx.interaction is None:
                await ctx.message.delete()
            else:
                await ctx.send('<:greenTick:1079249732364406854> Successfully unstarred message', ephemeral=True)

    @command(
        star.command,
        name='clean',
        description='Cleans the starboard',
        aliases=['prune']
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @requires_starboard()
    @app_commands.describe(stars='Remove messages that have less than or equal to this number')
    async def star_clean(self, ctx: StarboardContext, stars: commands.Range[int, 1, None] = 1):
        """Cleans the starboard

        This removes messages in the starboard that only have less
        than or equal to the number of specified stars. This defaults to 1.

        Note that this only checks the last 100 messages in the starboard.

        This command requires the Manage Server permission.
        """

        await ctx.defer()
        stars = max(stars, 1)
        channel = ctx.starboard.channel

        last_messages = [m.id async for m in channel.history(limit=100)]

        query = """
            WITH bad_entries AS (
               SELECT entry_id
               FROM starrers
               INNER JOIN starboard_entries
               ON starboard_entries.id = starrers.entry_id
               WHERE starboard_entries.guild_id=$1
               AND   starboard_entries.bot_message_id = ANY($2::bigint[])
               GROUP BY entry_id
               HAVING COUNT(*) <= $3
            )
            DELETE FROM starboard_entries USING bad_entries
            WHERE starboard_entries.id = bad_entries.entry_id
            RETURNING starboard_entries.bot_message_id
        """

        to_delete = await ctx.db.fetch(query, ctx.guild.id, last_messages, stars)

        min_snowflake = int((time.time() - 14 * 24 * 60 * 60) * 1000.0 - 1420070400000) << 22
        to_delete = [discord.Object(id=r[0]) for r in to_delete if r[0] > min_snowflake]

        try:
            self._about_to_be_deleted.update(o.id for o in to_delete)
            await channel.delete_messages(to_delete)
        except discord.HTTPException:
            await ctx.send('<:redTick:1079249771975413910> Could not delete messages.')
        else:
            await ctx.send(f'\N{PUT LITTER IN ITS PLACE SYMBOL} Deleted {plural(len(to_delete)):message}.')

    @command(
        star.command,
        name='show',
        description='Shows a starred message via its ID.'
    )
    @requires_starboard()
    @app_commands.describe(message_id='The message ID to show star information of')
    async def star_show(self, ctx: StarboardContext, message_id: Annotated[int, MessageID]):
        """Shows a starred message via its ID.

        To get the ID of a message you should right click on the
        message and then click "Copy ID". You must have
        Developer Mode enabled to get that functionality.

        You can only use this command once per 10 seconds.
        """

        await ctx.defer()

        record = await self.get_stars(message_id)
        if record is None:
            return await ctx.send('<:redTick:1079249771975413910> This message has not been starred.')

        bot_message_id = record.bot_message_id
        if bot_message_id is not None:
            msg = await self.get_message(ctx.starboard.channel, bot_message_id)
            if msg is not None:
                embed = msg.embeds[0] if msg.embeds else None
                return await ctx.send(msg.content, embed=embed)
            else:
                query = "DELETE FROM starboard_entries WHERE message_id=$1;"
                await ctx.db.execute(query, record.message_id)
                return

        channel: Optional[discord.abc.Messageable] = ctx.guild.get_channel_or_thread(
            record['channel_id'])  # type: ignore
        if channel is None:
            return await ctx.send("<:greenTick:1079249732364406854> The message's channel has been deleted.")

        msg = await self.get_message(channel, record.message_id)
        if msg is None:
            return await ctx.send('<:greenTick:1079249732364406854> The message has been deleted.')

        content, embed = self.get_emoji_message(msg, record.stars)
        await ctx.send(content, embed=embed)

    @command(
        star.command,
        name='who',
        description='Shows who starred a message via its ID.'
    )
    @requires_starboard()
    @app_commands.describe(message_id='The message ID to show starrer information of')
    async def star_who(self, ctx: StarboardContext, message_id: Annotated[int, MessageID]):
        """Show who starred a message.

        The ID can either be the starred message ID
        or the message ID in the starboard channel.
        """
        await ctx.defer()
        query = """
            SELECT starrers.author_id
            FROM starrers
            INNER JOIN starboard_entries entry
            ON entry.id = starrers.entry_id
            WHERE entry.message_id = $1 OR entry.bot_message_id = $1
        """

        records = await ctx.db.fetch(query, message_id)
        if records is None or len(records) == 0:
            return await ctx.send(
                '<:redTick:1079249771975413910> No one starred this message or this is an invalid message ID.')

        records = [r[0] for r in records]
        members = [str(member) async for member in self.bot.resolve_member_ids(ctx.guild, records)]
        items = [f'{index}. {entry}' for index, entry in enumerate(members, 1)]

        class TextPaginator(BasePaginator[str]):
            colour = self.bot.colour.darker_red()

            async def format_page(self, entries: List[str], /) -> discord.Embed:
                embed = discord.Embed(timestamp=datetime.datetime.utcnow(), color=self.colour)
                embed.set_author(name=f'Ignored Entities', icon_url=ctx.guild.icon.url)
                embed.set_footer(text=f"{plural(len(items)):entry|entries}")

                base = format(plural(len(records)), 'star')
                if len(records) > len(members):
                    embed.title = f'{base} (`{len(records) - len(members)}` left server)'
                else:
                    embed.title = base

                embed.description = '\n'.join(entries)

                return embed

        await TextPaginator.start(ctx, entries=items, per_page=15)

    @staticmethod
    def records_to_value(
            ctx: StarboardContext, records: list[Any], fmt: Optional[Callable[[str], str]] = None,
            _format: str = "member", default: str = 'None!'
    ) -> str:
        if not records:
            return default

        def jump_to_url(o: str) -> str:
            return f"https://discord.com/channels/{ctx.guild.id}/{ctx.starboard.channel.id}/{o}"

        emoji = 0x1F947  # :first_place:
        fmt = fmt or (lambda o: o)
        return '\n'.join(
            f'{chr(emoji + i)}: {jump_to_url(fmt(r["ID"])) if _format == "message" else fmt(r["ID"])} (**{plural(r["Stars"]):star}**)'
            for i, r in enumerate(records))

    async def star_guild_stats(self, ctx):
        e = discord.Embed(title=f"{ctx.guild.name}'s Starboard Stats")
        e.set_thumbnail(url=ctx.guild.icon.url)
        e.timestamp = ctx.starboard.channel.created_at
        e.set_footer(text='Adding stars since')

        query = "SELECT COUNT(*) FROM starboard_entries WHERE guild_id=$1;"

        record = await ctx.db.fetchrow(query, ctx.guild.id)
        total_messages = record[0]

        query = """SELECT COUNT(*)
                   FROM starrers
                   INNER JOIN starboard_entries entry
                   ON entry.id = starrers.entry_id
                   WHERE entry.guild_id=$1;
                """

        record = await ctx.db.fetchrow(query, ctx.guild.id)
        total_stars = record[0]

        e.description = f'**{plural(total_messages):message}** starred with a total of **{total_stars}** stars.'
        e.colour = self.bot.colour.darker_red()

        query = """WITH t AS (
                       SELECT
                           entry.author_id AS entry_author_id,
                           starrers.author_id,
                           entry.bot_message_id
                       FROM starrers
                       INNER JOIN starboard_entries entry
                       ON entry.id = starrers.entry_id
                       WHERE entry.guild_id=$1
                   )
                   (
                       SELECT t.entry_author_id AS "ID", 1 AS "Type", COUNT(*) AS "Stars"
                       FROM t
                       WHERE t.entry_author_id IS NOT NULL
                       GROUP BY t.entry_author_id
                       ORDER BY "Stars" DESC
                       LIMIT 3
                   )
                   UNION ALL
                   (
                       SELECT t.author_id AS "ID", 2 AS "Type", COUNT(*) AS "Stars"
                       FROM t
                       GROUP BY t.author_id
                       ORDER BY "Stars" DESC
                       LIMIT 3
                   )
                   UNION ALL
                   (
                       SELECT t.bot_message_id AS "ID", 3 AS "Type", COUNT(*) AS "Stars"
                       FROM t
                       WHERE t.bot_message_id IS NOT NULL
                       GROUP BY t.bot_message_id
                       ORDER BY "Stars" DESC
                       LIMIT 3
                   );
                """

        records = await ctx.db.fetch(query, ctx.guild.id)
        starred_posts = [r for r in records if r['Type'] == 3]
        e.add_field(name='Top Starred Posts', value=self.records_to_value(ctx, starred_posts, _format="message"),
                    inline=False)

        to_mention = lambda o: f'<@{o}>'

        star_receivers = [r for r in records if r['Type'] == 1]
        value = self.records_to_value(ctx, star_receivers, to_mention, default='N/A')
        e.add_field(name='Top Star Receivers', value=value, inline=False)

        star_givers = [r for r in records if r['Type'] == 2]
        value = self.records_to_value(ctx, star_givers, to_mention, default='N/A')
        e.add_field(name='Top Star Givers', value=value, inline=False)

        await ctx.send(embed=e)

    async def star_member_stats(self, ctx, member):
        e = discord.Embed(colour=self.bot.colour.darker_red())
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        query = """WITH t AS (
                       SELECT entry.author_id AS entry_author_id,
                              starrers.author_id,
                              entry.message_id
                       FROM starrers
                       INNER JOIN starboard_entries entry
                       ON entry.id=starrers.entry_id
                       WHERE entry.guild_id=$1
                   )
                   (
                       SELECT '0'::bigint AS "ID", COUNT(*) AS "Stars"
                       FROM t
                       WHERE t.entry_author_id=$2
                   )
                   UNION ALL
                   (
                       SELECT '0'::bigint AS "ID", COUNT(*) AS "Stars"
                       FROM t
                       WHERE t.author_id=$2
                   )
                   UNION ALL
                   (
                       SELECT t.message_id AS "ID", COUNT(*) AS "Stars"
                       FROM t
                       WHERE t.entry_author_id=$2
                       GROUP BY t.message_id
                       ORDER BY "Stars" DESC
                       LIMIT 3
                   )
                """

        records = await ctx.db.fetch(query, ctx.guild.id, member.id)
        received = records[0]['Stars']
        given = records[1]['Stars']
        top_three = records[2:]

        query = """SELECT COUNT(*) FROM starboard_entries WHERE guild_id=$1 AND author_id=$2;"""
        record = await ctx.db.fetchrow(query, ctx.guild.id, member.id)
        messages_starred = record[0]

        e.add_field(name='Messages Starred', value=messages_starred)
        e.add_field(name='Stars Received', value=received)
        e.add_field(name='Stars Given', value=given)

        e.add_field(name='Top Starred Posts', value=self.records_to_value(ctx, top_three, _format="message"),
                    inline=False)

        await ctx.send(embed=e)

    @command(
        star.command,
        name='stats',
        description='Shows statistics on the starboard usage of the server or a member.',
    )
    @requires_starboard()
    @app_commands.describe(member='The member to show stats of, if not given then shows server stats')
    async def star_stats(self, ctx: StarboardContext, *, member: discord.Member = None):
        """Shows statistics on the starboard usage of the server or a member."""

        await ctx.defer()
        if member is None:
            await self.star_guild_stats(ctx)
        else:
            await self.star_member_stats(ctx, member)

    @command(
        star.command,
        name='lock',
        description='Locks the starboard from being processed.'
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @requires_starboard()
    async def star_lock(self, ctx: StarboardContext):
        """Locks the starboard from being processed.

        This is a moderation tool that allows you to temporarily
        disable the starboard to aid in dealing with star spam.

        When the starboard is locked, no new entries are added to
        the starboard as the bot will no longer listen to reactions or
        star/unstar commands.

        To unlock the starboard, use the unlock subcommand.

        To use this command, you need Manage Server permission.
        """

        if ctx.starboard.needs_migration:
            return await ctx.send('<:discord_info:1113421814132117545> Your starboard requires migration!')

        query = "UPDATE starboard SET locked=TRUE WHERE id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.get_starboard.invalidate(self, ctx.guild.id)

        await ctx.send('<:greenTick:1079249732364406854> Starboard is now locked.')

    @command(
        star.command,
        name='unlock',
        description='Unlocks the starboard for re-processing.'
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @requires_starboard()
    async def star_unlock(self, ctx: StarboardContext):
        """Unlocks the starboard for re-processing."""

        if ctx.starboard.needs_migration:
            return await ctx.send('<:discord_info:1113421814132117545> Your starboard requires migration!')

        query = "UPDATE starboard SET locked=FALSE WHERE id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.get_starboard.invalidate(self, ctx.guild.id)

        await ctx.send('<:greenTick:1079249732364406854> Starboard is now unlocked.')

    @command(
        star.command,
        name='limit',
        description='Sets the minimum number of stars required to show up.',
        aliases=['threshold']
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @requires_starboard()
    @app_commands.describe(stars='The number of stars required before it shows up on the board')
    async def star_limit(self, ctx: StarboardContext, stars: int):
        """Sets the minimum number of stars required to show up.

        When this limit is set, messages must have this number
        or more to show up in the starboard channel.

        You cannot have a negative number and the maximum
        star limit you can set is 100.

        Note that messages that previously did not meet the
        limit but now do will still not show up in the starboard
        until starred again.

        You must have Manage Server permissions to use this.
        """

        if ctx.starboard.needs_migration:
            return await ctx.send('Your starboard requires migration!')

        stars = min(max(stars, 1), 100)
        query = "UPDATE starboard SET threshold=$2 WHERE id=$1;"
        await ctx.db.execute(query, ctx.guild.id, stars)
        self.get_starboard.invalidate(self, ctx.guild.id)

        await ctx.send(
            f'<:discord_info:1113421814132117545> Messages now require **{plural(stars):star}** to show up in the starboard.')

    @command(
        star.command,
        name='age',
        description='Sets the maximum age of a message valid for starring.',
    )
    @command_permissions(user=["ban_members", "manage_messages"])
    @requires_starboard()
    @app_commands.describe(
        number='The number of units to set the maximum age to',
        units='The unit of time to use for the number',
    )
    @app_commands.choices(
        units=[
            app_commands.Choice(name='Days', value='days'),
            app_commands.Choice(name='Weeks', value='weeks'),
            app_commands.Choice(name='Months', value='months'),
            app_commands.Choice(name='Years', value='years'),
        ]
    )
    async def star_age(
            self,
            ctx: StarboardContext,
            number: int,
            units: Literal['days', 'weeks', 'months', 'years', 'day', 'week', 'month', 'year'] = 'days',
    ):
        """Sets the maximum age of a message valid for starring.

        By default, the maximum age is 7 days. Any message older
        than this specified age is invalid of being starred.

        To set the limit you must specify a number followed by
        a unit. The valid units are "days", "weeks", "months",
        or "years". They do not have to be pluralized. The
        default unit is "days".

        The number cannot be negative, and it must be a maximum
        of 35. If the unit is years then the cap is 10 years.

        You cannot mix and match units.

        You must have Manage Server permissions to use this.
        """

        if units[-1] != 's':
            units = units + 's'

        number = min(max(number, 1), 35)

        if units == 'years' and number > 10:
            return await ctx.send('<:redTick:1079249771975413910> The maximum is 10 years!')

        query = f"UPDATE starboard SET max_age='{number} {units}'::interval WHERE id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        self.get_starboard.invalidate(self, ctx.guild.id)

        if number == 1:
            age = f'1 {units[:-1]}'
        else:
            age = f'{number} {units}'

        await ctx.send(f'<:discord_info:1113421814132117545> Messages must now be less than **{age}** old to be starred.')


async def setup(bot: Percy):
    await bot.add_cog(Starboard(bot))
