from __future__ import annotations

import asyncio
import datetime
import gc
import io
import itertools
import logging
import os
import re
import sys
import textwrap
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, TypedDict

import asyncpg
import discord
import psutil
import pygit2
from discord.ext import tasks
from sqlalchemy import func, Integer, String, DateTime, Boolean, Column, select, join, case, BigInteger, Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing_extensions import Annotated

from cogs.utils.paginator import FilePaginator
from launcher import get_logger
from .meta import COMMAND_ICON_URL, INFO_ICON_URL
from .utils import formats, timetools, helpers, commands
from .utils.constants import BOT_BASE_FOLDER
from .utils.converters import get_asset_url
from .utils.formats import censor_object
from .utils.lock import lock
from .utils.tasks import executor
from .utils.render import Render

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import GuildContext, Context

log = get_logger(__name__)

BaseTable = declarative_base()


class CommandTable(BaseTable):
    __tablename__ = 'commands'

    id = Column(Integer, primary_key=True)
    command = Column(String)
    prefix = Column(String)
    author_id = Column(Integer)
    used = Column(DateTime)
    failed = Column(Boolean)
    success = Column(Boolean)
    guild_id = Column(BigInteger)


class DataBatchEntry(TypedDict):
    guild: Optional[int]
    channel: int
    author: int
    used: str
    prefix: str
    command: str
    failed: bool
    app_command: bool


class CommandUsageCount:
    __slots__ = ('success', 'failed', 'total')

    def __init__(self):
        self.success = 0
        self.failed = 0
        self.total = 0

    def add(self, record: asyncpg.Record):
        self.success += record['success']
        self.failed += record['failed']
        self.total += record['total']


class LoggingHandler(logging.Handler):
    def __init__(self, cog: Stats):
        self.cog: Stats = cog
        super().__init__(logging.INFO)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in ('discord.gateway', 'bot')

    def emit(self, record: logging.LogRecord) -> None:
        self.cog.add_record(record)


def hex_value(arg: str) -> int:
    return int(arg, base=16)


def object_at(addr: int) -> Optional[Any]:
    for o in gc.get_objects():
        if id(o) == addr:
            return o
    return None


class Stats(commands.Cog):
    """Bot Statistics and Information."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.process = psutil.Process()

        self._data_batch: list[DataBatchEntry] = []

        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

        self._logging_queue = asyncio.Queue()
        self.logging_worker.start()

        self.render: Render = Render  # type: ignore

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='graph', id=1104490238312718417)

    @lock('Stats', 'command_batch', wait=True)
    async def bulk_insert(self) -> None:
        query = """
            INSERT INTO commands (guild_id, channel_id, author_id, used, prefix, command, failed, app_command)
            SELECT x.guild, x.channel, x.author, x.used, x.prefix, x.command, x.failed, x.app_command
            FROM jsonb_to_recordset($1::jsonb) AS
            x(
                guild BIGINT,
                channel BIGINT,
                author BIGINT,
                used TIMESTAMP,
                prefix TEXT,
                command TEXT,
                failed BOOLEAN,
                app_command BOOLEAN
            )
        """

        if self._data_batch:
            await self.bot.pool.execute(query, self._data_batch)
            total = len(self._data_batch)
            if total > 1:
                log.info('Registered %s commands to the database.', total)
            self._data_batch.clear()

    def cog_unload(self):
        self.bulk_insert_loop.stop()
        self.logging_worker.cancel()

    @tasks.loop(seconds=10.0)
    async def bulk_insert_loop(self):
        await self.bulk_insert()

    @tasks.loop(seconds=0.0)
    async def logging_worker(self):
        record = await self._logging_queue.get()
        await self.send_log_record(record)

    @lock('Stats', 'command_batch', wait=True)
    async def send_command_patch(self, to_send: dict[str, Any]):
        self._data_batch.append(to_send)

    async def register_command(self, ctx: Context) -> None:
        if ctx.command is None:
            return

        command = ctx.command.qualified_name
        is_app_command = ctx.interaction is not None
        self.bot.command_stats[command] += 1
        self.bot.command_types_used[is_app_command] += 1
        message = ctx.message
        if ctx.guild is None:
            destination = 'Private Message'
            guild_id = None
        else:
            destination = f'#{message.channel} ({message.guild})'
            guild_id = ctx.guild.id

        if ctx.interaction and ctx.interaction.command:
            content = f'/{ctx.interaction.command.qualified_name}'
        else:
            content = message.content

        log.info(f'{message.created_at.replace(tzinfo=None)}: {message.author} in {destination}: {content}')
        await self.send_command_patch(
            {
                'guild': guild_id,
                'channel': ctx.channel.id,
                'author': ctx.author.id,
                'used': message.created_at.isoformat(),
                'prefix': ctx.prefix,
                'command': command,
                'failed': ctx.command_failed,
                'app_command': is_app_command,
            }
        )

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: Context):
        await self.register_command(ctx)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        command = interaction.command
        if (
                command is not None
                and interaction.type is discord.InteractionType.application_command
                and not command.__class__.__name__.startswith('Hybrid')  # ignore hybrid commands
        ):
            ctx = await self.bot.get_context(interaction)
            ctx.command_failed = interaction.command_failed or ctx.command_failed
            await self.register_command(ctx)

    @commands.Cog.listener()
    async def on_socket_event_type(self, event_type: str):
        self.bot.socket_stats[event_type] += 1

    @commands.command(
        commands.group,
        name='command',
        invoke_without_command=True,
        hidden=True,
        description='Shows the current command usage statistics.',
    )
    async def _cmd(self, ctx: Context):
        """Shows the current command usage statistics."""
        if self.bot.is_owner(ctx.author) and ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands.command(_cmd.command, name='stats', description='Shows the current command usage statistics.')
    @commands.is_owner()
    async def command_stats(self, ctx: Context, *, limit: int = 12):
        """Shows the current command usage statistics.
        Note: Use a negative number for bottom instead of top.
        """
        counter = self.bot.command_stats
        total = sum(counter.values())
        slash_commands = self.bot.command_types_used[True]

        delta = discord.utils.utcnow() - self.bot.launched_at
        minutes = delta.total_seconds() / 60
        cpm = total / minutes

        if limit > 0:
            common = counter.most_common(limit)
            title = f'Top `{limit}` Commands'
        else:
            common = counter.most_common()[limit:]
            title = f'Bottom `{limit}` Commands'

        images = self.render.generate_bar_chart(
            dict(sorted({k: v for k, v in common}.items(), key=lambda item: item[1], reverse=True)),
            title=f'{total} total commands used ({slash_commands} slash command uses) ({cpm:.2f}/minute)')
        await ctx.send(f'## {title}')
        await FilePaginator.start(ctx, entries=images, per_page=1)

    @commands.command(
        commands.core_command,
        hidden=True,
        description='Shows the current socket event statistics.',
    )
    async def socketstats(self, ctx: Context):
        delta = discord.utils.utcnow() - self.bot.launched_at
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes
        images = self.render.generate_bar_chart(
            dict(sorted(self.bot.socket_stats.items(), key=lambda item: item[1], reverse=True)),
            title=f'{total:,} socket events observed ({cpm:.2f}/minute)')
        await FilePaginator.start(ctx, entries=images, per_page=1)

    def get_bot_uptime(self, *, brief: bool = False) -> str:
        return timetools.human_timedelta(self.bot.launched_at, accuracy=None, brief=brief, suffix=False)

    @commands.command(commands.core_command, description='Tells you how long the bot has been up for.')
    async def uptime(self, ctx: Context):
        """Tells you how long the bot has been up for."""
        await ctx.send(f'Uptime: **{self.get_bot_uptime()}**')

    @executor
    def line_counter(self) -> str:
        path = Path(__file__).parent.parent
        ignored = [Path(os.path.join(path, 'venv'))]
        files = classes = funcs = comments = lines = characters = 0
        for f in path.rglob(f'*.py'):
            if any(parent in ignored for parent in f.parents):
                continue
            files += 1
            with open(f, encoding='utf8', errors='ignore') as of:
                characters += len(open(f, encoding='utf8', errors='ignore').read())
                for line in of.readlines():
                    line = line.strip()
                    if line.startswith('class'):
                        classes += 1
                    if line.startswith('def') or line.startswith('async def'):
                        funcs += 1
                    if '#' in line:
                        comments += 1
                    lines += 1
        stats = {
            'Files': files,
            'Classes': classes,
            'Functions': funcs,
            'Comments': comments,
            'Lines': lines,
            'Characters': characters
        }
        return '\n'.join(f'{k}: {v}' for k, v in stats.items())

    @staticmethod
    def format_commit(commit: pygit2.Commit) -> str:
        short, _, _ = commit.message.partition('\n')
        short_sha2 = commit.hex[0:6]
        commit_tz = datetime.timezone(datetime.timedelta(minutes=commit.commit_time_offset))
        commit_time = datetime.datetime.fromtimestamp(commit.commit_time).astimezone(commit_tz)

        offset = discord.utils.format_dt(commit_time.astimezone(datetime.timezone.utc), 'R')
        return f'[`{short_sha2}`](https://github.com/klappstuhlpy/Percy/commit/{commit.hex}) {short} ({offset})'

    def get_last_commits(self, count=4, repo_path: str = BOT_BASE_FOLDER) -> str:
        repo = pygit2.Repository(os.path.join(repo_path, '.git'))
        commits = list(itertools.islice(repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL), count))
        return '\n'.join(self.format_commit(c) for c in commits)

    @commands.command()
    async def about(self, ctx: Context):
        """Tells you information about the bot itself."""

        revision = self.get_last_commits()
        embed = discord.Embed(description='Latest Changes:\n' + revision)
        embed.title = 'Official Bot Server Invite'
        embed.url = 'https://discord.gg/eKwMtGydqh'
        embed.colour = self.bot.colour.darker_red()

        embed.set_author(name=str(self.bot.owner), icon_url=get_asset_url(self.bot.owner))
        embed.set_thumbnail(url=get_asset_url(self.bot.user))

        total_members = 0
        total_unique = len(self.bot.users)

        CHANNEL_MAP = {
            discord.TextChannel: 'text',
            discord.VoiceChannel: 'voice',
        }

        text, voice, guilds = 0, 0, 0
        for guild in self.bot.guilds:
            guilds += 1
            if guild.unavailable:
                continue

            total_members += guild.member_count or 0
            for channel in guild.channels:
                match (CHANNEL_MAP.get(type(channel))):
                    case 'text':
                        text += 1
                    case 'voice':
                        voice += 1

        embed.add_field(name='Members', value=f'`{total_members}` total\n`{total_unique}` unique\n'
                                              f'Bot percentage: `{(total_unique / total_members):.2%}`')
        embed.add_field(name='Channels', value=f'`{text + voice}` total\n`{text}` text\n`{voice}` voice')

        memory_usage = self.process.memory_full_info().uss / 1024 ** 2
        cpu_usage = self.process.cpu_percent() / psutil.cpu_count()

        embed.add_field(name='Guilds', value=guilds)
        embed.add_field(name='Commands run since last reboot', value=sum(self.bot.command_stats.values()))
        embed.add_field(name='Uptime', value=self.get_bot_uptime(brief=True))
        embed.add_field(name='​', value='​')

        file_stats = await self.line_counter()
        embed.add_field(name='File Stats', value=f'```py\n{file_stats}```')
        embed.add_field(
            name='Process',
            value=f'```py\n'
                  f'CPU: {cpu_usage:.2f}% CPU\n'
                  f'Memory: {memory_usage:.2f} MiB | {psutil.virtual_memory().percent}%\n'
                  f'Disk: {psutil.disk_usage(str(Path(__file__).parent.parent)).percent}%```')

        embed.set_footer(
            text=f'Made with discord.py v{discord.__version__}',
            icon_url='https://images.klappstuhl.me/gallery/UYzvwImyRS.png')
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

    @about.before_invoke
    async def about_invoke(self, ctx: Context):
        await ctx.typing()

    @staticmethod
    async def show_guild_stats(ctx: GuildContext) -> None:
        medals = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        embed = discord.Embed(title='Server Command Stats', colour=helpers.Colour.darker_red())

        query = "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1;"
        count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(query, ctx.guild.id)  # type: ignore

        embed.description = f'Total of `{count[0]}` commands used.'
        if count[1]:
            timestamp = count[1].replace(tzinfo=datetime.timezone.utc)
        else:
            timestamp = discord.utils.utcnow()

        embed.set_footer(text='Tracking command usage since').timestamp = timestamp

        query = """
            SELECT command,
                  COUNT(*) as "uses"
            FROM commands
            WHERE guild_id=$1
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = (
                '\n'.join(
                    f'{medals[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
                or '*No Command Usages available.*'
        )

        embed.add_field(name='Top Commands', value=value, inline=True)

        query = """
            SELECT command,
                  COUNT(*) as "uses"
            FROM commands
            WHERE guild_id=$1
            AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = (
                '\n'.join(
                    f'{medals[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
                or '*No Command Usages available.*'
        )
        embed.add_field(name='Top Commands Today', value=value, inline=True)
        embed.add_field(name='\u200b', value='\u200b', inline=True)

        query = """
            SELECT author_id,
                  COUNT(*) AS "uses"
            FROM commands
            WHERE guild_id=$1
            GROUP BY author_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = (
                '\n'.join(
                    f'{medals[index]}: <@!{author_id}> (`{uses}` bot uses)' for (index, (author_id, uses)) in
                    enumerate(records)
                )
                or '*No Command Bot Users available.*'
        )

        embed.add_field(name='Top Command Users', value=value, inline=True)

        query = """
            SELECT author_id,
                  COUNT(*) AS "uses"
            FROM commands
            WHERE guild_id=$1
            AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY author_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = (
                '\n'.join(
                    f'{medals[index]}: <@!{author_id}> (`{uses}` bot uses)' for (index, (author_id, uses)) in
                    enumerate(records)
                )
                or '*No Command Bot Users available.*'
        )

        embed.add_field(name='Top Command Users Today', value=value, inline=True)
        await ctx.send(embed=embed)

    @staticmethod
    async def show_member_stats(ctx: GuildContext, member: discord.Member) -> None:
        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        embed = discord.Embed(title='Command Stats', colour=member.colour)
        embed.set_author(name=str(member), icon_url=get_asset_url(member))

        query = "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1 AND author_id=$2;"
        count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(query, ctx.guild.id, member.id)  # type: ignore

        embed.description = f'Total of `{count[0]}` commands used.'
        if count[1]:
            timestamp = count[1].replace(tzinfo=datetime.timezone.utc)
        else:
            timestamp = discord.utils.utcnow()

        embed.set_footer(text='First command used').timestamp = timestamp

        query = """
            SELECT command,
                  COUNT(*) as "uses"
            FROM commands
            WHERE guild_id=$1 AND author_id=$2
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        value = (
                '\n'.join(
                    f'{lookup[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
                or '*No Command Usages available.*'
        )

        embed.add_field(name='Most Used Commands', value=value, inline=False)

        query = """
            SELECT command,
                  COUNT(*) as "uses"
            FROM commands
            WHERE guild_id=$1
            AND author_id=$2
            AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        value = (
                '\n'.join(
                    f'{lookup[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
                or '*No Command Usages available.*'
        )

        embed.add_field(name='Most Used Commands Today', value=value, inline=False)
        await ctx.send(embed=embed)

    @commands.command(
        commands.group,
        name='stats',
        description='Tells you command usage stats for the server or a member.',
        invoke_without_command=True,
        cooldown=commands.CooldownMap(rate=1, per=30.0, type=commands.BucketType.member)
    )
    @commands.guild_only()
    async def stats(self, ctx: GuildContext, *, member: Annotated[discord.Member, commands.MemberConverter] = None):
        """Tells you command usage stats for the server or a member."""
        async with ctx.typing():
            if member is None:
                await self.show_guild_stats(ctx)
            else:
                await self.show_member_stats(ctx, member)

    # noinspection PyTypeChecker
    @commands.command(
        stats.command,
        name='for',
        description='Tells you the global stats for a command.',
        cooldown=commands.CooldownMap(rate=1, per=15.0, type=commands.BucketType.guild)
    )
    async def stats_for(self, ctx: Context, *, command: str):
        """Tells you the global stats for a command."""

        async with ctx.channel.typing():
            # NOTE: This is experimental!

            # We use here AsyncAlchemy to improve the performance of the query because is a big one
            # This might be a bit overkill, but it works
            # Note: first time using ORM so this might be a bit messy

            record = await ctx.db.execute("SELECT * FROM commands WHERE command = $1 LIMIT 1;", command)
            if record == 'SELECT 0':
                return await ctx.stick(False, 'No command called {command!r} found in the database.')

            # First, check if the command is in the database
            # because we want to avoid running this big query if we don't need to

            async_session = sessionmaker(self.bot.alchemy_engine, expire_on_commit=False,
                                         class_=AsyncSession)  # type: ignore
            async with async_session() as session:
                assert isinstance(session, AsyncSession)

                prefix_counts = (
                    select(
                        CommandTable.prefix,
                        func.count().label('most_invoked_with_count')
                    )
                    .filter(CommandTable.command == command)
                    .group_by(CommandTable.prefix)
                    .order_by(func.count().desc())
                    .limit(1)
                    .subquery('prefix_counts')
                )

                invoke_counts = (
                    select(
                        CommandTable.command,
                        func.count().label('total'),
                        func.sum(case((CommandTable.failed, 1), else_=0)).label('failed'),
                        func.sum(case((CommandTable.failed, 0), else_=1)).label('success')
                    )
                    .filter(CommandTable.command == command)
                    .group_by(CommandTable.command)
                    .subquery('invoke_counts')
                )

                user_counts = (
                    select(
                        CommandTable.author_id,
                        func.count().label('most_invoked_by_count')
                    )
                    .filter(CommandTable.command == command)
                    .group_by(CommandTable.author_id)
                    .order_by(func.count().desc())
                    .limit(1)
                    .subquery('user_counts')
                )

                guild_counts = (
                    select(
                        CommandTable.command,
                        func.count().label('guild_total')
                    )
                    .filter(CommandTable.command == command, CommandTable.guild_id == ctx.guild.id)
                    .group_by(CommandTable.command)
                    .limit(1)
                    .subquery('guild_counts')
                )

                x = (
                    select(
                        CommandTable.command,
                        CommandTable.prefix,
                        CommandTable.author_id,
                        func.min(CommandTable.used).label('first_used')
                    )
                    .filter(CommandTable.command == command)
                    .group_by(CommandTable.command, CommandTable.prefix, CommandTable.author_id)
                    .subquery('x')
                )

                stmt = (
                    select(
                        x.c.prefix.label('most_invoked_with'),
                        x.c.author_id.label('most_invoked_by'),
                        x.c.first_used,
                        guild_counts.c.guild_total,
                        invoke_counts.c.failed,
                        invoke_counts.c.success,
                        invoke_counts.c.total,
                        user_counts.c.most_invoked_by_count,
                        prefix_counts.c.most_invoked_with_count
                    )
                    .select_from(
                        join(
                            join(
                                join(
                                    x, invoke_counts, x.c.command == invoke_counts.c.command
                                ),
                                user_counts, x.c.author_id == user_counts.c.author_id
                            ),
                            prefix_counts, x.c.prefix == prefix_counts.c.prefix
                        )
                        .outerjoin(guild_counts, guild_counts.c.command == x.c.command)
                    )
                )

                result: Result = await session.execute(stmt)
                rows = result.fetchall()

                assert rows is not None  # can't be None

                record = {}  # Our new 'asyncpg.Record'-like object
                for row in rows:
                    record |= row._mapping  # noqa

                # didn't find a better way to do this because rows returns only the values,
                # what's a bit heavy to deal with on changes, so we use a mapping,
                # so I just did it like this

            embed = discord.Embed(colour=helpers.Colour.darker_red())
            embed.set_author(name='Command Statistic', icon_url=INFO_ICON_URL)

            resolved = self.bot.resolve_command(command)

            if resolved:
                embed.description = resolved.description or '*No help available.*'

                cog = getattr(resolved, 'cog', getattr(resolved, 'binding', None))
                if cog:
                    dp_emoji = getattr(cog, 'display_emoji', None)
                    embed.title = f'{dp_emoji} • {command}'
                else:
                    embed.title = command
            else:
                embed.title = command

            prefix = record['most_invoked_with']

            author_id = record['most_invoked_by']
            most_invoked_by = censor_object(
                self.bot.blacklist, self.bot.get_user(author_id) or f'<Unknown {author_id}>')

            embed.add_field(name='Most Invoked By',
                            value=f'{most_invoked_by} (**{record['most_invoked_by_count']}** times)', inline=False)
            embed.add_field(name='Most Invoked with',
                            value=f'`{prefix}` (**{record['most_invoked_with_count']}** times)',
                            inline=False)
            embed.add_field(name='**USES**',
                            value=f'Total: **{record['total']}**\n'
                                  f'Failed: **{record['failed']}**\n'
                                  f'Success: **{record['success']}**\n\n'
                                  f'Invoked in this Guild: **{record['guild_total']}** times',
                            inline=False)

            first_used = record['first_used']
            if first_used:
                first_used = first_used.replace(tzinfo=datetime.timezone.utc)
            else:
                first_used = discord.utils.utcnow()

            embed.set_footer(text='First command used at', icon_url=COMMAND_ICON_URL).timestamp = first_used
            embed.set_thumbnail(url=get_asset_url(ctx.guild))
            await ctx.send(embed=embed)

    @commands.command(
        stats.command,
        name='global',
        description='Global all time command statistics.',
    )
    @commands.is_owner()
    async def stats_global(self, ctx: Context):
        """Global all time command statistics."""

        query = "SELECT COUNT(*) FROM commands;"
        total: tuple[int] = await ctx.db.fetchrow(query)  # type: ignore

        embed = discord.Embed(title='Command Stats', colour=helpers.Colour.darker_red())
        embed.description = f'`{total[0]}` commands used.'

        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        query = """
            SELECT command, COUNT(*) AS "uses"
            FROM commands
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = '\n'.join(
            f'{lookup[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
        embed.add_field(name='Top Commands', value=value, inline=False)

        query = """
            SELECT guild_id, COUNT(*) AS "uses"
            FROM commands
            GROUP BY guild_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if guild_id is None:
                guild = 'Private Message'
            else:
                guild = censor_object(self.bot.blacklist, self.bot.get_guild(guild_id) or f'<Unknown {guild_id}>')

            emoji = lookup[index]
            value.append(f'{emoji}: {guild} (`{uses}` uses)')

        embed.add_field(name='Top Guilds', value='\n'.join(value), inline=False)

        query = """
            SELECT author_id, COUNT(*) AS "uses"
            FROM commands
            GROUP BY author_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = []
        for (index, (author_id, uses)) in enumerate(records):
            user = censor_object(self.bot.blacklist, self.bot.get_user(author_id) or f'<Unknown {author_id}>')
            emoji = lookup[index]
            value.append(f'{emoji}: {user} (`{uses}` uses)')

        embed.add_field(name='Top Users', value='\n'.join(value), inline=False)
        await ctx.send(embed=embed)

    @commands.command(
        stats.command,
        name='today',
        description='Global command statistics for the day.',
    )
    @commands.is_owner()
    async def stats_today(self, ctx: Context):
        """Global command statistics for the day."""

        query = "SELECT failed, COUNT(*) FROM commands WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day') GROUP BY failed;"
        total = await ctx.db.fetch(query)
        failed = 0
        success = 0
        question = 0
        for state, count in total:
            if state is False:
                success += count
            elif state is True:
                failed += count
            else:
                question += count

        embed = discord.Embed(title='Last 24 Hour Command Stats', colour=helpers.Colour.darker_red())
        embed.description = (
            f'`{failed + success + question}` commands used today. '
            f'(`{success}` succeeded, `{failed}` failed, `{question}` unknown)'
        )

        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        query = """
            SELECT command, COUNT(*) AS "uses"
            FROM commands
            WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY command
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = '\n'.join(
            f'{lookup[index]}: {command} (`{uses}` uses)' for (index, (command, uses)) in enumerate(records))
        embed.add_field(name='Top Commands', value=value, inline=False)

        query = """
            SELECT guild_id, COUNT(*) AS "uses"
            FROM commands
            WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY guild_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = []
        for (index, (guild_id, uses)) in enumerate(records):
            if guild_id is None:
                guild = 'Private Message'
            else:
                guild = censor_object(self.bot.blacklist, self.bot.get_guild(guild_id) or f'<Unknown {guild_id}>')
            emoji = lookup[index]
            value.append(f'{emoji}: {guild} (`{uses}` uses)')

        embed.add_field(name='Top Guilds', value='\n'.join(value), inline=False)

        query = """
            SELECT author_id, COUNT(*) AS "uses"
            FROM commands
            WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY author_id
            ORDER BY 'uses' DESC
            LIMIT 5;
        """

        records = await ctx.db.fetch(query)
        value = []
        for (index, (author_id, uses)) in enumerate(records):
            user = censor_object(self.bot.blacklist, self.bot.get_user(author_id) or f'<Unknown {author_id}>')
            emoji = lookup[index]
            value.append(f'{emoji}: {user} ({uses} uses)')

        embed.add_field(name='Top Users', value='\n'.join(value), inline=False)
        await ctx.send(embed=embed)

    async def send_guild_stats(self, embed: discord.Embed, guild: discord.Guild):
        embed.add_field(name='Name', value=guild.name)
        embed.add_field(name='ID', value=guild.id)
        embed.add_field(name='Shard ID', value=guild.shard_id or 'N/A')
        embed.add_field(name='Owner', value=f'{guild.owner} (ID: `{guild.owner_id}`)')

        bots = sum(m.bot for m in guild.members)
        total = guild.member_count or 1
        embed.add_field(name='Members', value=str(total))
        embed.add_field(name='Bots', value=f'{bots} ({bots / total:.2%})')
        embed.set_thumbnail(url=get_asset_url(guild))

        if guild.me:
            embed.timestamp = guild.me.joined_at

        await self.bot.stats_webhook.send(embed=embed)

    @stats_today.before_invoke
    @stats_global.before_invoke
    async def before_stats_invoke(self, ctx: Context):
        await ctx.typing()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self.bot.wait_until_ready()
        embed = discord.Embed(colour=0x53DDA4, title='New Guild')
        await self.send_guild_stats(embed, guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        await self.bot.wait_until_ready()
        embed = discord.Embed(colour=0xDD5F53, title='Left Guild')
        await self.send_guild_stats(embed, guild)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: Context, error: Exception) -> None:
        await self.register_command(ctx)
        if not isinstance(error, (commands.CommandInvokeError, commands.ConversionError)):
            return

        error = error.original
        if isinstance(error, (discord.Forbidden, discord.NotFound)):
            return

        embed = discord.Embed(title='<:warning:1113421726861238363> Command Error', colour=0x99002b)
        embed.add_field(name='Name', value=ctx.command.qualified_name)
        embed.add_field(name='Author',
                        value=f'[{ctx.author}](https://discord.com/users/{ctx.author.id}) (ID: {ctx.author.id})')

        fmt = f'Channel: [#{ctx.channel}]({ctx.channel.jump_url}) (ID: {ctx.channel.id})'
        if ctx.guild:
            fmt = f'{fmt}\nGuild: {ctx.guild} (ID: {ctx.guild.id})'
        else:
            fmt = f'{fmt}\nGuild: *<Private Message>*'

        embed.add_field(name='Location', value=fmt, inline=False)
        embed.add_field(name='Content', value=textwrap.shorten(ctx.message.content, width=1024))

        exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        embed.description = f'### Retrieved Traceback\n```py\n{exc}\n```'
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text='Occured at')
        await self.bot.stats_webhook.send(embed=embed)

    def add_record(self, record: logging.LogRecord) -> None:
        self._logging_queue.put_nowait(record)

    async def send_log_record(self, record: logging.LogRecord) -> None:
        attributes = {'INFO': '<:discord_info:1113421814132117545>', 'WARNING': '<:warning:1113421726861238363>'}

        emoji = attributes.get(record.levelname, '\N{CROSS MARK}')
        dt = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        msg = textwrap.shorten(f'{emoji} {discord.utils.format_dt(dt, style='F')} {record.message}', width=1990)
        if record.name == 'discord.gateway':
            username = 'Gateway'
            avatar_url = 'https://images.klappstuhl.me/gallery/mTuDFXPDrx.png'
        else:
            username = f'{record.name} Logger'
            avatar_url = discord.utils.MISSING

        await self.bot.stats_webhook.send(msg, username=username, avatar_url=avatar_url)

    # noinspection PyProtectedMember
    @commands.command(commands.core_command, hidden=True)
    @commands.is_owner()
    async def bothealth(self, ctx: Context):
        """Various bot health monitoring tools."""

        HEALTHY = discord.Colour(value=0x43B581)
        UNHEALTHY = discord.Colour(value=0xF04947)
        WARNING = discord.Colour(value=0xF09E47)
        total_warnings = 0

        embed = discord.Embed(title='Bot Health Report', colour=HEALTHY)

        pool = self.bot.pool
        total_waiting = len(pool._queue._getters)
        current_generation = pool._generation

        description = [
            f'Total `Pool.acquire` Waiters: {total_waiting}',
            f'Current Pool Generation: {current_generation}',
            f'Connections In Use: {len(pool._holders) - pool._queue.qsize()}']

        questionable_connections = 0
        connection_value = []
        for index, holder in enumerate(pool._holders, start=1):
            generation = holder._generation
            in_use = holder._in_use is not None
            is_closed = holder._con is None or holder._con.is_closed()
            display = f'gen={holder._generation} in_use={in_use} closed={is_closed}'
            questionable_connections += any((in_use, generation != current_generation))
            connection_value.append(f'<Holder i={index} {display}>')

        joined_value = '\n'.join(connection_value)
        embed.add_field(name='Connections', value=f'```py\n{joined_value}\n```', inline=False)

        being_spammed = self.bot.spam_control.current_spammers

        description.append(f'Current Spammers: {', '.join(str(being_spammed)) if being_spammed else 'None'}')
        description.append(f'Questionable Connections: {questionable_connections}')

        total_warnings += questionable_connections
        if being_spammed:
            embed.colour = WARNING
            total_warnings += 1

        all_tasks = asyncio.all_tasks(loop=self.bot.loop)
        event_tasks = [t for t in all_tasks if 'Client._run_event' in repr(t) and not t.done()]

        cogs_directory = os.path.dirname(__file__)
        tasks_directory = os.path.join('discord', 'ext', 'tasks', '__init__.py')
        inner_tasks = [t for t in all_tasks if cogs_directory in repr(t) or tasks_directory in repr(t)]

        bad_inner_tasks = ', '.join(hex(id(t)) for t in inner_tasks if t.done() and t._exception is not None)
        total_warnings += bool(bad_inner_tasks)
        embed.add_field(name='Inner Tasks', value=f'Total: {len(inner_tasks)}\nFailed: {bad_inner_tasks or 'None'}')
        embed.add_field(name='Events Waiting', value=f'Total: {len(event_tasks)}', inline=False)

        command_waiters = len(self._data_batch)
        description.append(f'Commands Waiting: {command_waiters}')

        memory_usage = self.process.memory_full_info().uss / 1024 ** 2
        cpu_usage = self.process.cpu_percent() / psutil.cpu_count()
        embed.add_field(name='Process', value=f'{memory_usage:.2f} MiB\n{cpu_usage:.2f}% CPU', inline=False)

        global_rate_limit = not self.bot.http._global_over.is_set()
        description.append(f'Global Rate Limit: {global_rate_limit}')

        if command_waiters >= 8:
            total_warnings += 1
            embed.colour = WARNING

        if global_rate_limit or total_warnings >= 9:
            embed.colour = UNHEALTHY

        embed.set_footer(text=f'{total_warnings} warning(s)')
        embed.description = '\n'.join(description)
        await ctx.send(embed=embed)

    @commands.command(commands.core_command, hidden=True)
    @commands.is_owner()
    async def gateway(self, ctx: Context):
        """Gateway related stats."""

        yesterday = discord.utils.utcnow() - datetime.timedelta(days=1)

        # fmt: off
        identifies = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.identifies.items()
        }
        resumes = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.resumes.items()
        }
        # fmt: on

        total_identifies = sum(identifies.values())

        builder = [
            f'Total RESUME(s): `{sum(resumes.values())}`',
            f'Total IDENTIFY(s): `{total_identifies}`',
        ]

        """shard_count = len(self.bot.shards)
        if total_identifies > (shard_count * 10):
            issues = 2 + (total_identifies // 10) - shard_count
        else:
            issues = 0

        for shard_id, shard in self.bot.shards.items():
            badge = None
            if shard.is_closed():
                badge = '<:offline:1085666365689573438>'
                issues += 1
            elif shard._parent._task and shard._parent._task.done():
                exc = shard._parent._task.exception()
                if exc is not None:
                    badge = '\N{FIRE}'
                    issues += 1
                else:
                    badge = '\U0001f504'

            if badge is None:
                badge = '<:online:1085666365689573438>'

            stats = []
            identify = identifies.get(shard_id, 0)
            resume = resumes.get(shard_id, 0)
            if resume != 0:
                stats.append(f'R: {resume}')
            if identify != 0:
                stats.append(f'ID: {identify}')

            if stats:
                builder.append(f'Shard ID {shard_id}: {badge} ({', '.join(stats)})')
            else:
                builder.append(f'Shard ID {shard_id}: {badge}')"""

        """if issues == 0:
            colour = 0x43B581
        elif issues < len(self.bot.shards) // 4:
            colour = 0xF09E47
        else:
            colour = 0xF04947"""

        embed = discord.Embed(colour=0x43B581, title='Gateway (last 24 hours)')
        embed.description = '\n'.join(builder)
        embed.set_footer(text=f'None warnings')
        await ctx.send(embed=embed)

    @commands.command(commands.core_command, hidden=True)
    @commands.is_owner()
    async def list_tasks(self, ctx: Context):
        """List all tasks."""
        _tasks = asyncio.all_tasks(loop=self.bot.loop)
        table = formats.TabularData()
        table.set_columns(['Memory ID', 'Name', 'Object'])

        def strip_memory_id(s: str) -> str:
            return (s.split(' ')[-1])[:-1]

        table.add_rows(
            (strip_memory_id(str(task.get_coro())), task.get_name(), str(task.get_coro()).split(' ')[2]) for task in
            _tasks)
        render = table.render()
        render = re.sub(r'```\w?.*', '', render, re.RegexFlag.M)

        pages = commands.Paginator(prefix='```ansi', suffix='```', max_size=2000)
        for line in render.splitlines():
            pages.add_line(line)

        for page in pages.pages:
            await ctx.send(page)

    @commands.command(
        commands.core_command,
        hidden=True,
        aliases=['cancel_task'],
    )
    @commands.is_owner()
    async def debug_task(self, ctx: Context, memory_id: Annotated[int, hex_value]):
        """Debug a task by a memory location."""
        task = object_at(memory_id)
        if task is None or not isinstance(task, asyncio.Task):
            return await ctx.send(f'Could not find Task object at `{hex(memory_id)}`.')

        if ctx.invoked_with == 'cancel_task':
            task.cancel()
            return await ctx.send(f'Cancelled task object {task!r}.')

        paginator = commands.Paginator(prefix='```py')
        fp = io.StringIO()
        frames = len(task.get_stack())
        paginator.add_line(f'# Total Frames: {frames}')
        task.print_stack(file=fp)

        for line in fp.getvalue().splitlines():
            paginator.add_line(line)

        for page in paginator.pages:
            await ctx.send(page)

    @staticmethod
    async def tabulate_query(ctx: Context, query: str, *args: Any):
        records = await ctx.db.fetch(query, *args)

        if len(records) == 0:
            return await ctx.send('No results found.')

        headers = list(records[0].keys())
        table = formats.TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        render = table.render()

        fp = io.BytesIO(render.strip().encode('utf-8'))
        await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))

    @commands.command(
        _cmd.group,
        name='history',
        hidden=True,
        invoke_without_command=True,
        description='Command history related commands.',
    )
    @commands.is_owner()
    async def command_history(self, ctx: Context, limit: int = 15):
        """Command history."""

        async with ctx.channel.typing():
            query = f"""
                SELECT
                    CASE failed
                        WHEN TRUE THEN command || ' [!]'
                        ELSE command
                    END AS "command",
                    to_char(used, 'Mon DD HH12:MI:SS AM') AS "invoked",
                    author_id,
                    guild_id
                FROM commands
                ORDER BY used DESC
                LIMIT {limit};
            """
            await self.tabulate_query(ctx, query)

    @commands.command(
        command_history.command,
        name='for',
        hidden=True,
    )
    @commands.is_owner()
    async def command_history_for(self, ctx: Context, days: Annotated[int, Optional[int]] = 7, *, command: str):
        """Command history for a command."""

        async with ctx.channel.typing():
            query = """
                SELECT 
                    *, t.success + t.failed AS "total"
                FROM (
                   SELECT guild_id,
                          SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                          SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                   FROM commands
                   WHERE command=$1
                   AND used > (CURRENT_TIMESTAMP - $2::interval)
                   GROUP BY guild_id
                ) AS t
                ORDER BY 'total' DESC
                LIMIT 30;
            """

            await self.tabulate_query(ctx, query, command, datetime.timedelta(days=days))

    @commands.command(
        command_history.command,
        name='guild',
        hidden=True,
        aliases=['server'],
    )
    @commands.is_owner()
    async def command_history_guild(self, ctx: Context, guild_id: int):
        """Command history for a guild."""

        async with ctx.channel.typing():
            query = """
                SELECT
                    CASE failed
                        WHEN TRUE THEN command || ' [!]'
                        ELSE command
                    END AS "command",
                    channel_id,
                    author_id,
                    used
                FROM commands
                WHERE guild_id=$1
                ORDER BY used DESC
                LIMIT 15;
            """
            await self.tabulate_query(ctx, query, guild_id)

    @commands.command(
        command_history.command,
        name='user',
        hidden=True,
        aliases=['member'],
    )
    @commands.is_owner()
    async def command_history_user(self, ctx: Context, user_id: int):
        """Command history for a user."""

        async with ctx.channel.typing():
            query = """
                SELECT
                    CASE failed
                        WHEN TRUE THEN command || ' [!]'
                        ELSE command
                    END AS "command",
                    guild_id,
                    used
                FROM commands
                WHERE author_id=$1
                ORDER BY used DESC
                LIMIT 20;
            """
            await self.tabulate_query(ctx, query, user_id)

    @commands.command(
        command_history.command,
        name='log',
        hidden=True,
    )
    @commands.is_owner()
    async def command_history_log(self, ctx: Context, days: int = 7):
        """Command history log for the last N days."""

        async with ctx.channel.typing():
            query = """
                SELECT 
                    command, 
                    COUNT(*)
                FROM commands
                WHERE used > (CURRENT_TIMESTAMP - $1::interval)
                GROUP BY command
                ORDER BY 2 DESC
            """

            all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}

            records = await ctx.db.fetch(query, datetime.timedelta(days=days))
            for name, uses in records:
                if name in all_commands:
                    all_commands[name] = uses

            as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
            table = formats.TabularData()
            table.set_columns(['Command', 'Uses'])
            table.add_rows(tup for tup in as_data)
            render = table.render()

            embed = discord.Embed(title='Summary', colour=discord.Colour.green())
            embed.set_footer(text='Since').timestamp = discord.utils.utcnow() - datetime.timedelta(days=days)

            top_ten = '\n'.join(f'{command}: {uses}' for command, uses in records[:10])
            bottom_ten = '\n'.join(f'{command}: {uses}' for command, uses in records[-10:])
            embed.add_field(name='Top 10', value=top_ten)
            embed.add_field(name='Bottom 10', value=bottom_ten)

            unused = ', '.join(name for name, uses in as_data if uses == 0)
            if len(unused) > 1024:
                unused = 'Way too many...'

            embed.add_field(name='Unused', value=unused, inline=False)

            await ctx.send(embed=embed,
                           file=discord.File(io.BytesIO(render.encode()), filename='full_results.accesslog'))

    @commands.command(
        command_history.command,
        name='cog',
        hidden=True,
    )
    @commands.is_owner()
    async def command_history_cog(self, ctx: Context, days: Annotated[int, Optional[int]] = 7, *, cog_name: str = None):
        """Command history for a cog or grouped by a cog."""

        async with ctx.channel.typing():
            interval = datetime.timedelta(days=days)
            if cog_name is not None:
                cog = self.bot.get_cog(cog_name)
                if cog is None:
                    return await ctx.send(f'Unknown cog: {cog_name}')

                query = """
                    SELECT 
                        *, 
                        t.success + t.failed AS "total"
                    FROM (
                       SELECT command,
                              SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                              SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                       FROM commands
                       WHERE command = any($1::text[])
                       AND used > (CURRENT_TIMESTAMP - $2::interval)
                       GROUP BY command
                    ) AS t
                    ORDER BY 'total' DESC
                    LIMIT 30;
                """
                return await self.tabulate_query(ctx, query, [c.qualified_name for c in cog.walk_commands()], interval)

            query = """
                SELECT 
                    *, 
                    t.success + t.failed AS "total"
                FROM (
                   SELECT command,
                          SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                          SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                   FROM commands
                   WHERE used > (CURRENT_TIMESTAMP - $1::interval)
                   GROUP BY command
                ) AS t;
            """

            data = defaultdict(CommandUsageCount)
            records = await ctx.db.fetch(query, interval)
            for record in records:
                command = self.bot.get_command(record['command'])
                if command is None or command.cog is None:
                    data['No Cog'].add(record)
                else:
                    data[command.cog.qualified_name].add(record)  # type: ignore

            table = formats.TabularData()
            table.set_columns(['Cog', 'Success', 'Failed', 'Total'])
            data = sorted([(cog, e.success, e.failed, e.total) for cog, e in data.items()], key=lambda t: t[-1],
                          reverse=True)

            table.add_rows(data)
            render = table.render()
            await ctx.safe_send(f'```\n{render}\n```')


old_on_error = commands.Bot.on_error


async def on_error(self: Percy, event: str, *args: Any, **kwargs: Any) -> None:  # noqa
    (exc_type, exc, tb) = sys.exc_info()
    if isinstance(exc, commands.CommandInvokeError):
        return

    # Check if there is a 'bypass_log' attribute in the exception object
    if hasattr(exc, 'bypass_log'):
        return

    embed = discord.Embed(title='<:warning:1113421726861238363> Event Error', colour=0x99002b)
    embed.add_field(name='Event', value=event)
    trace = "".join(traceback.format_exception(exc_type, exc, tb))
    embed.description = f'```py\n{trace}\n```'
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text='Occurred at')

    args_str = ['```py']
    for index, arg in enumerate(args):
        args_str.append(f'[{index}]: {arg!r}')
    args_str.append('```')
    embed.add_field(name='Args', value='\n'.join(args_str), inline=False)
    hook: discord.Webhook = self.stats_webhook

    try:
        await hook.send(embed=embed)
    except (discord.HTTPException, ValueError):
        pass


async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    command = interaction.command
    error = getattr(error, 'original', error)

    if isinstance(error, (discord.Forbidden, discord.NotFound)):
        return

    hook: discord.Webhook = interaction.client.stats_webhook
    embed = discord.Embed(
        title='<:warning:1113421726861238363> App Command Error', timestamp=interaction.created_at, colour=0x99002b)

    if command is not None:
        # Check if there is a 'bypass_log' attribute in the exception object
        if to_bypass := command.extras.get('bypass_error', None):
            if isinstance(error, to_bypass):
                return

        if command._has_any_error_handlers():  # noqa
            return

        embed.add_field(name='Name', value=command.qualified_name)

    embed.add_field(
        name='User',
        value=f'[{interaction.user}](https://discord.com/users/{interaction.user.id}) (ID: {interaction.user.id})')

    fmt = f'Channel: [#{interaction.channel}]({interaction.channel.jump_url}) (ID: {interaction.channel_id})'
    if interaction.guild:
        fmt = f'{fmt}\nGuild: {interaction.guild} (ID: {interaction.guild.id})'
    else:
        fmt = f'{fmt}\nGuild: *<Private Message>*'

    embed.add_field(name='Location', value=fmt, inline=False)

    namespace: dict = interaction.namespace.__dict__
    embed.add_field(name='Namespace(s)', value=' '.join(f'{k}: {v!r}' for k, v in namespace.items()), inline=False)

    exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
    embed.description = f'### Retrieved Traceback\n```py\n{exc}\n```'
    embed.set_footer(text='Occured at')

    try:
        await hook.send(embed=embed)
    except (discord.HTTPException, ValueError):
        pass


async def setup(bot: Percy):
    if not hasattr(bot, 'command_stats'):
        bot.command_stats = Counter()

    if not hasattr(bot, 'socket_stats'):
        bot.socket_stats = Counter()

    if not hasattr(bot, 'command_types_used'):
        bot.command_types_used = Counter()

    cog = Stats(bot)
    await bot.add_cog(cog)

    bot.logging_handler = handler = LoggingHandler(cog)
    get_logger().addHandler(handler)
    commands.Bot.on_error = on_error
    bot.old_tree_error = bot.tree.on_error
    bot.tree.on_error = on_app_command_error


async def teardown(bot: Percy):
    commands.Bot.on_error = old_on_error
    get_logger().removeHandler(bot.logging_handler)
    bot.tree.on_error = bot.old_tree_error
    del bot.logging_handler
