from __future__ import annotations

import asyncio
import datetime
import io
import itertools
import logging
import textwrap
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict

import asyncpg
import discord
import psutil
import pygit2
from discord.ext import commands, tasks
from discord.utils import MISSING
from expiringdict import ExpiringDict

import config
from app.core import Bot, Cog, Context
from app.core.models import command, cooldown, describe, group
from app.core.views import UserInfoView
from app.rendering import AvatarCollage, BarChart, PresenceChart, resize_to_limit
from app.utils import AnsiColor, AnsiStringBuilder, TabularData, Timer, censor_object, get_asset_url, helpers, medal_emoji
from app.utils.pagination import FilePaginator
from app.utils.tasks import executor
from app.utils.timetools import human_timedelta
from config import Emojis, beta, path, repo_url, version

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)


class CommandBatchEntry(TypedDict):
    guild: int | None
    channel: int
    author: int
    used: str
    prefix: str
    command: str
    failed: bool
    app_command: bool
    error: str | None


class AvatarBatchEntry(TypedDict):
    user_id: int
    name: str
    image: bytes


class CommandUsageCount:
    """A counter for command usage by :class:`asyncpg.Record`s."""

    __slots__ = ('success', 'failed', 'total')

    def __init__(self) -> None:
        self.success = 0
        self.failed = 0
        self.total = 0

    def add(self, record: asyncpg.Record) -> None:
        self.success += record['success']
        self.failed += record['failed']
        self.total += record["total"]


class LoggingHandler(logging.Handler):
    def __init__(self, cog: Stats) -> None:
        self.cog: Stats = cog
        super().__init__(logging.INFO)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in ('discord.gateway', 'bot')

    def emit(self, record: logging.LogRecord) -> None:
        self.cog.add_record(record)


class Stats(Cog):
    """Bot Statistics and Information."""

    emoji = '<:graph:1322354647910055967>'

    _presence_map: ClassVar[dict[discord.Status, str]] = {
        discord.Status.online: 'Online',
        discord.Status.idle: 'Idle',
        discord.Status.dnd: 'Do Not Disturb',
        discord.Status.offline: 'Offline'
    }

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.process = psutil.Process()

        self._command_data_batch: list[CommandBatchEntry] = []
        self._avatar_data_batch: list[AvatarBatchEntry] = []

        self._logging_queue = asyncio.Queue()
        self.__loging_worker_task: asyncio.Task | None = None

        self.__LOOPS: list[Any] = [
            self.cleanup_presence_history,
            self.command_insert,
            self.avatar_insert
        ]

        # if we're on our beta instance, we don't want to start those tasks
        if not beta:
            for _task in self.__LOOPS:
                _task.add_exception_type(asyncpg.PostgresConnectionError)
                _task.start()

            self.__loging_worker_task = self.bot.loop.create_task(self.logging_worker())

        self._presence_cache = ExpiringDict(max_len=1000, max_age_seconds=30)

    @tasks.loop(hours=24.0)
    async def cleanup_presence_history(self) -> None:
        """|coro|

        A task that automatically clears all presence history entries that are older than 30 days.
        """
        async with self.bot.db.acquire() as connection:
            await connection.execute(
                """
                    DELETE FROM presence_history
                    WHERE changed_at < (CURRENT_TIMESTAMP - INTERVAL '30 days');
                """
            )

    @tasks.loop(seconds=10.0)
    async def command_insert(self) -> None:
        """|coro|

        A task that inserts the command data batch into the database.

        This task is automatically started after the cog is loaded.
        """
        query = """
            INSERT INTO commands (guild_id, channel_id, author_id, used, prefix, command, failed, app_command, error)
            SELECT x.guild,
                   x.channel,
                   x.author,
                   x.used,
                   x.prefix,
                   x.command,
                   x.failed,
                   x.app_command,
                   x.error
            FROM jsonb_to_recordset($1::jsonb)
                AS x(
                        guild BIGINT,
                        channel BIGINT,
                        author BIGINT,
                        used TIMESTAMP,
                        prefix TEXT,
                        command TEXT,
                        failed BOOLEAN,
                        app_command BOOLEAN,
                        error TEXT
                );
        """

        if self._command_data_batch:
            await self.bot.db.execute(query, self._command_data_batch)
            total = len(self._command_data_batch)
            if total > 1:
                log.info('Registered %s commands to the database.', total)
            self._command_data_batch.clear()

    @tasks.loop(seconds=10.0)
    async def avatar_insert(self) -> None:
        """|coro|

        A task that inserts the avatar data batch into the database.

        This task is automatically started after the cog is loaded.
        """
        query = "SELECT insert_avatar_history_item($1, $2, $3);"
        for data in self._avatar_data_batch:
            await self.bot.db.execute(query, data['user_id'], data['name'], data['image'])
        self._avatar_data_batch.clear()

    def cog_unload(self) -> None:
        for _task in self.__LOOPS:
            _task.cancel()

        if self.__loging_worker_task:
            self.__loging_worker_task.cancel()

    # LOGGING

    async def logging_worker(self) -> None:
        """|coro|

        A task that sends log records to the stats webhook.

        This task is automatically started after the cog is loaded.
        """
        try:
            record = await self._logging_queue.get()

            await self.send_log_record(record)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception('Unhandled exception in logging worker: %s', exc)
            self.__loging_worker_task = self.bot.loop.create_task(self.logging_worker())

    def register_command(self, ctx: Context) -> None:
        """Registers an invoked command from the given context.

        If you want to register a command from an interaction, you'll need to first
        get the context using `bot.get_context` and then pass it to this method.

        Parameters
        ----------
        ctx: `Context`
            The context of the invoked command.
        """
        if ctx is MISSING:
            return
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

        if ctx.is_interaction and ctx.interaction.command:
            content = f'/{ctx.interaction.command.qualified_name}'
        else:
            content = message.content

        log.info('%s: %s in %s: %s', ctx.now.replace(tzinfo=None), message.author, destination, content)
        self._command_data_batch.append(
            CommandBatchEntry(
                guild=guild_id,
                channel=ctx.channel.id,
                author=ctx.author.id,
                used=ctx.now.isoformat(),
                prefix=ctx.prefix,
                command=command,
                failed=ctx.command_failed,
                app_command=is_app_command,
                error=self.bot.command_error_cache.pop(self.bot.make_command_cache_key(ctx), None)
            )
        )

    @Cog.listener()
    async def on_command_completion(self, ctx: Context) -> None:
        self.register_command(ctx)

    @Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        command = interaction.command
        if (
                command is not None
                and interaction.type is discord.InteractionType.application_command
                and not command.__class__.__name__.startswith('Hybrid')
                # ignore hybrid commands because they are handled elsewhere
        ):
            ctx = await self.bot.get_context(interaction)
            ctx.command_failed = interaction.command_failed or ctx.command_failed
            self.register_command(ctx)

    @Cog.listener()
    async def on_socket_event_type(self, event_type: str) -> None:
        self.bot.socket_stats[event_type] += 1

    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Handles a new guild joining the bot.

        Parameters
        ----------
        guild: `discord.Guild`
            The guild that joined.
        """
        await self.bot.wait_until_ready()
        embed = discord.Embed(colour=helpers.Colour.lime_green(), title='New Guild')
        await self.send_guild_stats(embed, guild)

        members: Sequence[discord.Member] | list[discord.Member] = (
            await guild.chunk() if guild.chunked else guild.members
        )
        for member in members:
            try:
                if len(member.mutual_guilds) > 1:
                    continue
            except AttributeError:
                continue
            try:
                avatar: bytes = await member.display_avatar.read()
            except discord.HTTPException as exc:
                if exc.status in (403, 404):
                    continue
                if exc.status >= 500:
                    continue
                log.info(
                    "Unhandled Discord HTTPException while getting avatar for %s (%s)",
                    member.name,
                    member.id,
                )
                continue

            scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
            self._avatar_data_batch.append(
                AvatarBatchEntry(
                    user_id=member.id,
                    name=member.name,
                    image=scaled_avatar.getvalue()
                )
            )

    @Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Handles a new member joining the guild.

        Parameters
        ----------
        member: `discord.Member`
            The member that joined.
        """
        if member.bot:
            return

        if len(member.mutual_guilds) > 1:
            return

        avatar: bytes | None = await self._read_avatar(member)
        if avatar is None:
            return

        scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
        self._avatar_data_batch.append(
            AvatarBatchEntry(
                user_id=member.id,
                name=member.name,
                image=scaled_avatar.getvalue()
            )
        )

    @Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Handles a member updating their avatar.

        Parameters
        ----------
        before: `discord.Member`
            The member before the update.
        after: `discord.Member`
            The member after the update.
        """
        if before.bot:
            return

        if before.display_avatar != after.display_avatar:
            avatar: bytes | None = await self._read_avatar(after)
            if avatar:
                return

            scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
            self._avatar_data_batch.append(
                AvatarBatchEntry(
                    user_id=after.id,
                    name=after.name,
                    image=scaled_avatar.getvalue()
                )
            )

        if before.nick != after.nick and after.nick is not None:
            query = "INSERT INTO item_history (uuid, item_type, item_value) VALUES ($1, $2, $3);"
            async with self.bot.db.acquire() as connection:
                await connection.execute(query, after.id, 'nickname', after.nick)

    @Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        """Handles a user updating their details.

        Parameters
        ----------
        before: `discord.User`
            The user before the update.
        after: `discord.User`
            The user after the update.

        Handles the following updates:
        - Name
        - Discriminator
        - Avatar
        """
        if before.name != after.name:
            query = "INSERT INTO item_history (uuid, item_type, item_value) VALUES ($1, $2, $3);"
            async with self.bot.db.acquire() as connection:
                await connection.execute(query, after.id, 'name', after.name)

        if before.avatar != after.avatar:
            avatar: bytes | None = await self._read_avatar(after)
            if avatar is None:
                return

            scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
            self._avatar_data_batch.append(
                AvatarBatchEntry(
                    user_id=after.id,
                    name=after.name,
                    image=scaled_avatar.getvalue()
                )
            )

    @Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.bot.wait_until_ready()
        embed = discord.Embed(colour=helpers.Colour.light_red(), title='Left Guild')
        await self.send_guild_stats(embed, guild)

    @Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        """Handles a member updating their presence.

        Parameters
        ----------
        before: `discord.Member`
            The member before the update.
        after: `discord.Member`
            The member after the update.
        """
        if before.bot:
            return

        if (await self.bot.db.get_user_config(after.id)).track_presence is False:
            return

        def _make_key(member: discord.Member) -> str:
            return f'status:{member.id}:{member.status}'

        if before.status != after.status:
            if self._presence_cache.get(_make_key(after)):
                return

            self._presence_cache[_make_key(after)] = True

            query: str = "INSERT INTO presence_history (uuid, status, status_before) VALUES ($1, $2, $3);"
            async with self.bot.db.acquire() as connection:
                await connection.execute(
                    query,
                    after.id,
                    self._presence_map.get(after.status),
                    self._presence_map.get(before.status),
                )

    async def _read_avatar(
            self, member: discord.Member | discord.User
    ) -> bytes | None:
        """Reads the avatar of a member.

        Parameters
        ----------
        member: `discord.Member | discord.User`
            The member to read the avatar of.

        Returns
        -------
        `bytes | None`
            The avatar of the member, if it exists.
        """
        try:
            avatar: bytes = await member.display_avatar.read()
        except discord.HTTPException as exc:
            if exc.status in (403, 404):
                return
            if exc.status >= 500:
                await asyncio.sleep(15.0)
                await self._read_avatar(member)
            return log.exception(
                "Unhandled Discord HTTPException while getting avatar for %s (%s)",
                member.name,
                member.id,
            )
        return avatar

    def get_bot_uptime(self, *, brief: bool = False) -> str:
        return human_timedelta(self.bot.startup_timestamp, accuracy=None, brief=brief, suffix=False)

    @staticmethod
    def _format_commit(commit: pygit2.Commit) -> str:
        short, _, _ = commit.message.partition('\n')
        short_sha2 = commit.hex[0:6]
        commit_tz = datetime.timezone(datetime.timedelta(minutes=commit.commit_time_offset))
        commit_time = datetime.datetime.fromtimestamp(commit.commit_time).astimezone(commit_tz)

        offset = discord.utils.format_dt(commit_time.astimezone(datetime.UTC), 'R')
        return f'[`{short_sha2}`]({repo_url}commit/{commit.hex}) {short} ({offset})'

    def get_last_commits(self, count: int = 4, repo_path: str = path) -> str:
        repo = pygit2.Repository(Path(repo_path, '.git'))
        commits = list(itertools.islice(repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL), count))
        return '\n'.join(self._format_commit(c) for c in commits)

    @executor
    def project_stats_counter(self) -> str:
        path = Path(__file__).parent.parent
        ignored = [Path(path / 'venv')]
        files = classes = funcs = comments = lines = characters = 0
        for f in path.rglob('*.py'):
            if any(parent in ignored for parent in f.parents):
                continue
            files += 1
            f_path = Path(f)
            with f_path.open(encoding='utf8', errors='ignore') as of:
                characters += len(f_path.open(encoding='utf8', errors='ignore').read())
                for line in of.readlines():
                    line = line.strip()
                    if line.startswith('class'):
                        classes += 1
                    if line.startswith('def') or line.startswith('async def'):
                        funcs += 1
                    if '#' in line:
                        comments += 1
                    lines += 1

        builder = AnsiStringBuilder()
        builder.append('Files:       ', color=AnsiColor.gray)
        builder.append(str(files), color=AnsiColor.green).newline()
        builder.append('Classes:     ', color=AnsiColor.gray)
        builder.append(str(classes), color=AnsiColor.green).newline()
        builder.append('Functions:   ', color=AnsiColor.gray)
        builder.append(str(funcs), color=AnsiColor.green).newline()
        builder.append('Comments:    ', color=AnsiColor.gray)
        builder.append(str(comments), color=AnsiColor.green).newline()
        builder.append('Lines:       ', color=AnsiColor.gray)
        builder.append(str(lines), color=AnsiColor.green).newline()
        builder.append('Characters:  ', color=AnsiColor.gray)
        builder.append(str(characters), color=AnsiColor.green)

        return str(builder)

    async def get_commands_stats(
            self,
            guild_id: int | None = None,
            author_id: int | None = None,
            *,
            days: int | None = None,
            group_by: Literal['author_id', 'command', 'guild_id'] = 'command',
            limit: int = 5
    ) -> list[asyncpg.Record] | None:
        """|coro|

        Gets the command usage statistics for the given guild and author.

        Notes
        -----
        If `guild_id` and `author_id` are both `None`, the statistics will be global.

        Parameters
        ----------
        guild_id: `int`
            The ID of the guild to get the statistics for.
        author_id: `int`
            The ID of the author to get the statistics for.
        days: `int`
            The number of days to get the statistics for.
        group_by: `Literal['author_id', 'command', 'guild_id']`
            The column to group the statistics by.
            Important: The group by clause with match with your specified columns (author_id, command, guild_id).
        limit: `int`
            The number of commands to get the statistics for.

        Returns
        -------
        `asyncpg.Record`
            The command usage statistics.
        """
        args = ()
        query = f"SELECT {group_by}, COUNT(*) as uses FROM commands"

        def _pref() -> str:
            return "WHERE" if not args else "AND"

        if guild_id:
            query += " WHERE guild_id = $1"
            args += (guild_id,)
        if author_id:
            query += f" {_pref()} author_id = ${len(args) + 1}"
            args += (author_id,)
        if days:
            query += f" {_pref()} used > (CURRENT_TIMESTAMP - ${len(args) + 1}::interval)"
            args += (datetime.timedelta(days=days),)

        query += f" GROUP BY {group_by} ORDER BY uses DESC LIMIT {limit};"
        return await self.bot.db.fetch(query, *args)

    @command(hidden=True, description='Shows the current socket event statistics.')
    async def socketstats(self, ctx: Context) -> None:
        """Shows the current socket event statistics."""
        await self.bot.wait_until_ready()
        delta = discord.utils.utcnow() - self.bot.startup_timestamp
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes
        chart = BarChart(
            data=dict(sorted(self.bot.socket_stats.items(), key=lambda item: item[1], reverse=True)),
            title=f'{total} socket events observed ({cpm:.2f}/minute)')
        images = [chart.create(merge=True)]
        await FilePaginator.start(ctx, entries=images, per_page=1)

    @command(description='Tells you how long the bot has been up for.')
    async def uptime(self, ctx: Context) -> None:
        """Tells you how long the bot has been up for."""
        await self.bot.wait_until_ready()
        await ctx.send(f'Uptime: **{self.get_bot_uptime()}**')

    @command(description='Tells you information about the bot itself.')
    async def about(self, ctx: Context) -> None:
        """Tells you information about the bot itself."""
        await ctx.typing()

        try:
            revision = self.get_last_commits()
        except pygit2.GitError:
            revision = '*Not available.*'

        url = discord.utils.oauth_url(
            client_id=ctx.bot.user.id,
            permissions=discord.Permissions(8),
            scopes=('bot', 'applications.commands'),
        )

        embed = discord.Embed(
            url=url,
            title='Official Bot Invite',
            description='[**Support Server Invite**](https://discord.com/3jSYQ9VNbA)\n\n'
                        'Latest Changes:\n' + revision,
            colour=helpers.Colour.white()
        )

        assert isinstance(config.owners, int)
        owner = ctx.bot.get_user(config.owners)

        embed.set_author(name=owner, icon_url=get_asset_url(owner))
        embed.set_thumbnail(url=get_asset_url(self.bot.user))

        embed.add_field(name='Version', value=version, inline=False)

        total_members = 0
        total_unique = len(self.bot.users)

        text, voice, guilds = 0, 0, 0
        for guild in self.bot.guilds:
            guilds += 1
            if guild.unavailable:
                continue

            total_members += guild.member_count or 0
            for channel in guild.channels:
                match type(channel):
                    case discord.TextChannel:
                        text += 1
                    case discord.VoiceChannel:
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

        file_stats = await self.project_stats_counter()
        embed.add_field(name='File Stats', value=f'```ansi\n{file_stats}```')

        builder = AnsiStringBuilder()
        builder.append('Memory Usage:  ', color=AnsiColor.gray)
        builder.append(f'{memory_usage:.2f} MiB', color=AnsiColor.green).newline()
        builder.append('CPU Usage:     ', color=AnsiColor.gray)
        builder.append(f'{cpu_usage:.2f}%', color=AnsiColor.green).newline()
        builder.append('Disk Usage:    ', color=AnsiColor.gray)
        builder.append(f'{psutil.disk_usage(str(Path(__file__).parent.parent)).percent}%', color=AnsiColor.green).newline()
        embed.add_field(name='System Stats', value=f'```ansi\n{builder!s}```')

        embed.set_footer(
            text=f'Made with discord.py v{discord.__version__}',
            icon_url='https://klappstuhl.me/gallery/jUksiGZDtC.png')
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

    @group(
        name='stats',
        description='Tells you command usage stats for the server or a member.',
        invoke_without_command=True,
        guild_only=True
    )
    @cooldown(1, 5.0, commands.BucketType.guild)
    @describe(member='The member to show stats for.')
    async def stats(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Tells you command usage stats for the server or a member."""
        async with ctx.typing():
            embed = discord.Embed()

            if member is None:
                embed.title = 'Server Command Stats'
                embed.colour = helpers.Colour.white()

                count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(  # type: ignore
                    "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1;",
                    ctx.guild.id
                )

                top_commands = await self.get_commands_stats(ctx.guild.id)
                value = '\n'.join(
                    f'{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)' for i, record in
                    enumerate(top_commands)
                ) or '*No Command Usages available.*'
                embed.add_field(name='Top Commands', value=value, inline=True)

                top_commands_today = await self.get_commands_stats(ctx.guild.id, days=1)
                value = '\n'.join(
                    f'{medal_emoji(index)}: {cmd} (`{uses}` uses)' for (index, (cmd, uses)) in
                    enumerate(top_commands_today)
                ) or '*No Command Usages available.*'
                embed.add_field(name='Top Commands Today', value=value, inline=True)

                # placeholder
                embed.add_field(name='\u200b', value='\u200b', inline=True)

                top_users = await self.get_commands_stats(ctx.guild.id, group_by='author_id')
                value = '\n'.join(
                    f'{medal_emoji(i)}: <@!{record['author_id']}> (`{record['uses']}` bot uses)' for i, record in
                    enumerate(top_users)
                ) or '*No Command Bot Users available.*'
                embed.add_field(name='Top Command Users', value=value, inline=True)

                top_users_today = await self.get_commands_stats(ctx.guild.id, group_by='author_id', days=1)
                value = '\n'.join(
                    f'{medal_emoji(i)}: <@!{record['author_id']}> (`{record['uses']}` bot uses)' for i, record in
                    enumerate(top_users_today)
                ) or '*No Command Bot Users available.*'
                embed.add_field(name='Top Command Users Today', value=value, inline=True)

                embed.set_footer(text='Tracking command usage since')
            else:
                embed.title = 'Command Stats'
                embed.colour = member.colour
                embed.set_author(name=str(member), icon_url=get_asset_url(member))

                count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(  # type: ignore
                    "SELECT COUNT(*), MIN(used) FROM commands WHERE guild_id=$1 AND author_id=$2;",
                    ctx.guild.id, member.id
                )

                most_used = await self.get_commands_stats(ctx.guild.id, member.id)
                value = '\n'.join(
                    f'{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)' for i, record in
                    enumerate(most_used)
                ) or '*No Command Usages available.*'

                embed.add_field(name='Most Used Commands', value=value, inline=False)

                most_used_today = await self.get_commands_stats(ctx.guild.id, member.id, days=1)
                value = '\n'.join(
                    f'{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)' for i, record in
                    enumerate(most_used_today)
                ) or '*No Command Usages available.*'

                embed.add_field(name='Most Used Commands Today', value=value, inline=False)

                embed.set_footer(text='First command used')

            embed.description = f'Total of `{count[0]}` commands used.'
            embed.timestamp = count[1].replace(tzinfo=datetime.UTC) if count[1] else discord.utils.utcnow()

            await ctx.send(embed=embed)

    @stats.command(
        name='global',
        description='Global all time command statistics.',
    )
    async def stats_global(self, ctx: Context) -> None:
        """Global all time command statistics."""
        await ctx.typing()

        total: int = await ctx.db.fetchval("SELECT COUNT(*) FROM commands;")
        embed = discord.Embed(title='Command Stats', colour=helpers.Colour.white())
        embed.description = f'`{total}` commands used.'

        top_commands = await self.get_commands_stats()
        value = '\n'.join(
            f'{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)' for i, record in
            enumerate(top_commands)
        ) or '*No Command Usages available.*'
        embed.add_field(name='Top Commands', value=value, inline=False)

        top_guilds = await self.get_commands_stats(group_by='guild_id')
        value = []
        for i, record in enumerate(top_guilds):
            if record['guild_id'] is None:
                guild = 'Private Message'
            else:
                guild = censor_object(self.bot.blacklist, self.bot.get_guild(record['guild_id']) or f'<Unknown {record['guild_id']}>')
            value.append(f'{medal_emoji(i)}: {guild} (`{record['uses']}` uses)')
        embed.add_field(name='Top Guilds', value='\n'.join(value), inline=False)

        value.clear()

        top_users = await self.get_commands_stats(group_by='author_id')
        for i, record in enumerate(top_users):
            user = censor_object(self.bot.blacklist, self.bot.get_user(record['author_id']) or f'<Unknown {record['author_id']}>')
            value.append(f'{medal_emoji(i)}: {user} (`{record['uses']}` uses)')
        embed.add_field(name='Top Users', value='\n'.join(value), inline=False)

        await ctx.send(embed=embed)

    @stats.command(
        name='today',
        description='Global command statistics for the day.',
    )
    async def stats_today(self, ctx: Context) -> None:
        """Global command statistics for the day."""
        await ctx.typing()

        query = """
            SELECT failed,
                   COUNT(*)
            FROM commands
            WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
            GROUP BY failed;
        """
        total = await ctx.db.fetch(query)
        failed, success, question = 0, 0, 0
        for state, count in total:
            match state:
                case False:
                    success += count
                case True:
                    failed += count
                case _:
                    question += count

        embed = discord.Embed(title='Last 24 Hour Command Stats', colour=helpers.Colour.white())
        embed.description = (
            f'`{failed + success + question}` commands used today. '
            f'(`{success}` succeeded, `{failed}` failed, `{question}` unknown)'
        )

        top_commands = await self.get_commands_stats(days=1)
        value = '\n'.join(
            f'{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)' for i, record in
            enumerate(top_commands)
        ) or '*No Command Usages available.*'
        embed.add_field(name='Top Commands', value=value, inline=False)

        top_guilds = await self.get_commands_stats(group_by='guild_id', days=1)
        value = []
        for i, record in enumerate(top_guilds):
            if record['guild_id'] is None:
                guild = 'Private Message'
            else:
                guild = censor_object(self.bot.blacklist,
                                      self.bot.get_guild(record['guild_id']) or f'<Unknown {record['guild_id']}>')
            value.append(f'{medal_emoji(i)}: {guild} (`{record['uses']}` uses)')
        embed.add_field(name='Top Guilds', value='\n'.join(value), inline=False)

        top_users = await self.get_commands_stats(group_by='author_id', days=1)
        for i, record in enumerate(top_users):
            user = censor_object(self.bot.blacklist,
                                 self.bot.get_user(record['author_id']) or f'<Unknown {record['author_id']}>')
            value.append(f'{medal_emoji(i)}: {user} (`{record['uses']}` uses)')
        embed.add_field(name='Top Users', value='\n'.join(value), inline=False)

        await ctx.send(embed=embed)

    async def send_guild_stats(self, embed: discord.Embed, guild: discord.Guild) -> None:
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

    async def get_presence_history(self, user_id: int, /, *, days: int = 30) -> list[asyncpg.Record]:
        async with self.bot.db.acquire() as connection:
            return await connection.fetch(
                """
                    SELECT status, status_before, changed_at
                    FROM presence_history
                    WHERE uuid = $1
                      AND (changed_at AT TIME ZONE 'UTC') > (CURRENT_TIMESTAMP - $2::interval)
                    ORDER BY changed_at DESC;
                """,
                user_id, datetime.timedelta(days=days),
            )

    async def get_item_history(self, user_id: int, item_type: Literal['name', 'nickname']) -> list[asyncpg.Record]:
        """|coro|

        Fetches the item history for a user.

        Parameters
        ----------
        user_id: `int`
            The user to fetch the item history for.
        item_type: `Literal['name', 'nickname']`
            The type of item to fetch the history for.

        Returns
        -------
        `list[asyncpg.Record]`
            The item history for the user.
        """
        async with self.bot.db.acquire() as connection:
            return await connection.fetch(
                """
                    SELECT item_value, changed_at
                    FROM item_history
                    WHERE uuid = $1
                      AND item_type = $2
                    ORDER BY changed_at DESC;
                """,
                user_id, item_type,
            )

    async def get_avatar_history(self, member: discord.Member | discord.User) -> list[asyncpg.Record]:
        """Fetch the user's avatar history.

        Parameters
        ----------
        member: `discord.Member` | `discord.User`
            The user whose avatar history is to be fetched.

        Returns
        -------
        list[asyncpg.Record]
            The avatar history of the user.
        """
        return await self.bot.db.fetch(
            """
                SELECT avatar, changed_at
                FROM avatar_history
                WHERE uuid = $1
                ORDER BY changed_at LIMIT 100;
            """,
            member.id,
        )

    @command(
        'names',
        alias='ns',
        description='Shows the username history of a user.',
        hybrid=True,
        guild_only=True
    )
    @describe(member='The member to show the username history for.')
    async def names(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member = member or ctx.author

        usernames: list[asyncpg.Record] = await self.get_item_history(user.id, 'name')
        nicknames: list[asyncpg.Record] = await self.get_item_history(user.id, 'nickname')

        if not usernames and not nicknames:
            await ctx.send_error('No name history found.')
            return

        un_text = ', '.join(f'`{name}` {discord.utils.format_dt(changed_at, 'R')}' for name, changed_at in usernames)
        nn_text = ', '.join(f'`{name}` {discord.utils.format_dt(changed_at, 'R')}' for name, changed_at in usernames)
        await ctx.send(
            f"""
            ### Username History for {user}
            **Usernames:** {un_text or '*No usernames found.*'}
            **Nicknames:** {nn_text or '*No nicknames found.*'}
            """
        )

    @command(
        'lastseen',
        alias='ls',
        description='Shows when a user was last seen.',
        hybrid=True,
        guild_only=True
    )
    @describe(member='The member to show the last seen for.')
    async def last_seen(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member = member or ctx.author
        records = await self.get_presence_history(user.id, days=30)

        if not records:
            await ctx.send_error('No presence history found.')
            return

        last_seen = records[0]['changed_at']

        member = 'You were' if user == ctx.author else f'{user} was'
        await ctx.send(f'{member} last seen *{discord.utils.format_dt(last_seen, 'R')}*')

    @command(
        'avatarhistory',
        description='Shows the avatar history of a user.',
        alias='avyh',
        hybrid=True,
        guild_only=True
    )
    @describe(member='The member to show the avatar history for.')
    async def avatar_history(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows the avatar history of a user."""
        user: discord.Member = member or ctx.author
        await ctx.defer(typing=True)

        async with ctx.channel.typing():
            with Timer() as timer:
                history = await self.get_avatar_history(user)

                if not history:
                    await ctx.send_error('No avatar history found.')
                    return

                fetching_time = timer.reset()

                avatars = [x['avatar'] for x in history]
                if not avatars:
                    return

                collage = AvatarCollage(avatars)
                file = await asyncio.to_thread(collage.create)

        embed = discord.Embed(
            title=f'Avatar Collage for {user}',
            description=(
                f'`{'Fetching':<{12}}:` {fetching_time:.3f}s\n'
                f'`{'Generating':<{12}}:` {timer.seconds:.3f}s\n\n'
                f'Showing `{len(history)}` of up to `100` changes.'
            ),
            timestamp=history[-1]['changed_at'],
            colour=helpers.Colour.white()
        )
        embed.set_image(url=f'attachment://{file.filename if file else 'collage.png'}')
        embed.set_footer(text='Last updated')
        await ctx.send(embed=embed, file=file)

    @command(
        'presence',
        alias='ps',
        description='Shows the presence history of a user.',
        hybrid=True,
        guild_only=True
    )
    @describe(member='The member to show the presence history for.')
    async def presence(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member = member or ctx.author
        query_days = 30

        async with ctx.channel.typing():
            with Timer() as timer:
                history: list[asyncpg.Record] = await self.get_presence_history(user.id, days=query_days)

                if not history:
                    await ctx.send_error('No presence history found.')
                    return

                fetching_time = timer.reset()

                record_dict: dict[datetime.datetime, Any] = {
                    record['changed_at']: [
                        record['status'],
                        record['status_before'],
                    ]
                    for record in history
                }

                status_timers: dict[str, float] = {
                    'Online': 0,
                    'Idle': 0,
                    'Do Not Disturb': 0,
                    'Offline': 0,
                }

                for i, (changed_at, statuses) in enumerate(record_dict.items()):
                    if i != 0:
                        status_timers[statuses[1]] += (list(record_dict.keys())[i - 1] - changed_at).total_seconds()

                if all(value == 0 for value in status_timers.values()):
                    await ctx.send_error('Not enough data to generate a chart.')
                    return

                analyzing_time = timer.reset()

                presence_instance = PresenceChart(
                    labels=['Online', 'Offline', 'DND', 'Idle'],
                    colors=['#43b581', '#747f8d', '#f04747', '#fba31c'],
                    values=[
                        int(status_timers['Online']),
                        int(status_timers['Offline']),
                        int(status_timers['Do Not Disturb']),
                        int(status_timers['Idle']),
                    ]
                )
                canvas: discord.File = await asyncio.to_thread(presence_instance.create)

        embed = discord.Embed(
            title=f'Past 1 Month User Activity of {user}',
            description=(
                f'`{'Fetching':<{12}}:` {fetching_time:.3f}s\n'
                f'`{'Analyzing':<{12}}:` {analyzing_time:.3f}s\n'
                f'`{'Generating':<{12}}:` {timer.seconds:.3f}s'
            ),
            timestamp=min(record_dict.keys()),
            colour=helpers.Colour.white()
        )
        embed.set_image(url=f'attachment://{canvas.filename}')
        embed.set_footer(text='Watching since')
        await ctx.send(embed=embed, file=canvas)

    @command(
        name='userinfo',
        alias='ui',
        description='Shows information about a user.',
        hybrid=True,
        guild_only=True
    )
    @describe(member='The member to show information for.')
    async def userinfo(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user = member or ctx.author
        await ctx.defer()

        embed = discord.Embed(colour=helpers.Colour.white())

        informations: list[str] = []
        guild_related: list[str] = []

        embed.set_author(name=str(user))

        informations.append(f'**Name:** {user.mention}')
        informations.append(f'**ID:** `{user.id}`')
        informations.append(f'**is Bot:** `{user.bot}`')

        records = await self.get_presence_history(user.id, days=30)
        last_seen = discord.utils.format_dt(records[0]['changed_at'], 'R') if records else '`Unknown`'
        informations.append(f'**Last Seen:** {last_seen}')

        informations.append(f'**Created:** {discord.utils.format_dt(user.created_at, 'R')}')
        informations.append(f'**Shared Servers:** {len(user.mutual_guilds)}')
        informations.append(f'**System User:** `{user.system}`')

        embed.add_field(name='User Information', value='\n'.join(informations), inline=False)

        guild_related.append(f'**Joined:** {discord.utils.format_dt(user.joined_at, 'R')}')
        guild_related.append(f'**Join Position:** `{sum(m.joined_at < user.joined_at for m in user.guild.members) + 1}/{len(user.guild.members)}`')
        guild_related.append(f'**Top Role:** {user.top_role.mention}')
        guild_related.append(f'**Colour:** `{user.colour}`')

        badges_to_emoji = {
            'partner': '<:partner:1322355086822735972>',  # Emoji Server
            'verified_bot_developer': '<:earlydev:1322337994124034048>',  # Klappstuhl's Hideout
            'hypesquad_balance': '<:balance:1322354569866383402>',  # Emoji Server
            'hypesquad_bravery': '<:bravery:1322354587491110922>',  # Emoji Server
            'hypesquad_brilliance': '<:brilliance:1322354595179135047>',  # Klappstuhl's Hideout
            'bug_hunter': '<:bug_hunter_1:1322362990602883072>',  # Klappstuhl's Hideout
            'hypesquad': '<:hypesquad_events:1322363349719060540>',  # Emoji Server
            'early_supporter': '<:earlysupporter:1322363580867285013>',  # Klappstuhl's Hideout
            'bug_hunter_level_2': '<:bug_hunter_2:1322362999314583602>',  # Klappstuhl's Hideout
            'staff': '<:staff_badge:1322355128719769640>',  # Emoji Server
            'discord_certified_moderator': '<:mod_badge:1322337933428260874>',  # Emoji Server
            'active_developer': '<:active_developer:1322337889782202519>',  # Playground
        }

        misc_flags_descriptions = {
            'team_user': 'Application Team User',
            'system': 'System User',
            'spammer': 'Spammer',
            'verified_bot': 'Verified Bot',
            'bot_http_interactions': 'HTTP Interactions Bot',
        }

        set_flags = {flag for flag, value in user.public_flags if value}
        subset_flags = set_flags & badges_to_emoji.keys()
        badges = [badges_to_emoji[flag] for flag in subset_flags]

        if ctx.guild is not None and ctx.guild.owner_id == user.id:
            badges.append('<:owner:1322355079109541940>')  # Emoji Server

        if isinstance(user, discord.Member) and user.premium_since is not None:
            guild_related.append(f'**Boosting Since:** `{discord.utils.format_dt(user.premium_since, 'R')}`')
            badges.append('<:booster:1322354580184633344>')  # Emoji Server

        if badges:
            embed.description = ''.join(badges)

        custom_activity = next((act for act in getattr(user, 'activities', []) if isinstance(act, discord.CustomActivity)), None)
        activity = (
            f'`{discord.utils.remove_markdown(custom_activity.name)}`'
            if custom_activity and custom_activity.name else None
        )
        if activity:
            guild_related.append(f'**Custom Activity:** {activity}')

        voice = getattr(user, 'voice', None)
        if voice is not None:
            vc = voice.channel
            other_people = len(vc.members) - 1
            voice = f'`{vc.name}` with {other_people} others' if other_people else f'`{vc.name}` by themselves'
            guild_related.append(f'**Voice:** {voice}')

        remaining_flags = (set_flags - subset_flags) & misc_flags_descriptions.keys()
        if remaining_flags:
            guild_related.append(
                f'**Flags:** {', '.join(misc_flags_descriptions[flag] for flag in remaining_flags)}'
            )

        perms = user.guild_permissions.value
        guild_related.append(f'**Permissions:** [`{perms}`](https://discordapi.com/permissions.html#{perms})')

        embed.add_field(name='Guild Information', value='\n'.join(guild_related), inline=False)

        if colour := user.colour.value:
            embed.colour = colour

        embed.set_thumbnail(url=get_asset_url(user))

        user = await self.bot.fetch_user(user.id)
        if user.banner:
            embed.set_image(url=user.banner.url)

        embed.set_footer(
            text='Buttons can also be called by using the commands: avyh, names, ps'
        )

        await ctx.send(embed=embed, view=UserInfoView(ctx, user))

    @Cog.listener()
    async def on_command_error(self, ctx: Context, error: Exception) -> None:
        self.register_command(ctx)
        error = getattr(error, 'original', error)

        if not isinstance(error, (commands.CommandInvokeError, commands.ConversionError)):
            return

        blacklist = (
            discord.Forbidden, discord.NotFound
        )
        if isinstance(error, blacklist):
            return

        embed = discord.Embed(title=f'{Emojis.warning} Command Error', colour=helpers.Colour.burgundy())
        embed.add_field(name='Name', value=ctx.command.qualified_name)
        embed.add_field(name='Author',
                        value=f'[{ctx.author}](https://discord.com/users/{ctx.author.id}) (ID: {ctx.author.id})')

        fmt = f'Channel: [#{ctx.channel}]({ctx.channel.jump_url}) (ID: {ctx.channel.id})\n'
        if ctx.guild:
            fmt += f'Guild: {ctx.guild} (ID: {ctx.guild.id})'
        else:
            fmt += 'Guild: *<Private Message>*'

        embed.add_field(name='Location', value=fmt, inline=False)
        embed.add_field(name='Content', value=textwrap.shorten(ctx.message.content, width=1024))

        exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        embed.description = f'```py\n{exc}\n```'
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text='occurred at')
        await self.bot.stats_webhook.send(embed=embed)

    def add_record(self, record: logging.LogRecord) -> None:
        self._logging_queue.put_nowait(record)

    async def send_log_record(self, record: logging.LogRecord) -> None:
        attributes = {'INFO': Emojis.info, 'WARNING': Emojis.warning}

        emoji = attributes.get(record.levelname, '\N{CROSS MARK}')
        dt = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        msg = textwrap.shorten(f'{emoji} {discord.utils.format_dt(dt, style='F')} {record.message}', width=1990)
        if record.name == 'discord.gateway':
            username = 'Gateway'
            avatar_url = 'https://klappstuhl.me/gallery/hVBcEmbqsw.png'
        else:
            username = f'{record.name} Logger'
            avatar_url = discord.utils.MISSING

        await self.bot.stats_webhook.send(msg, username=username, avatar_url=avatar_url)

    @command(hidden=True, description='Shows the current log level.')
    @commands.is_owner()
    async def bothealth(self, ctx: Context) -> None:
        """Various bot health monitoring tools."""

        HEALTHY = helpers.Colour.lime_green()
        UNHEALTHY = helpers.Colour.darker_red()
        WARNING = helpers.Colour.energy_yellow()
        total_warnings = 0

        embed = discord.Embed(title='Bot Health Report', colour=HEALTHY)

        db = self.bot.db._internal_pool
        total_waiting = len(db._queue._getters)
        current_generation = db._generation

        description = [
            f'Total `Pool.acquire` Waiters: {total_waiting}',
            f'Current Pool Generation: {current_generation}',
            f'Connections In Use: {len(db._holders) - db._queue.qsize()}']

        questionable_connections = 0
        connection_value = []
        for index, holder in enumerate(db._holders, start=1):
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

        cogs_directory = str(Path(__file__).parent)
        tasks_directory = str(Path('discord', 'ext', 'tasks', '__init__.py'))
        inner_tasks = [t for t in all_tasks if cogs_directory in repr(t) or tasks_directory in repr(t)]

        bad_inner_tasks = ', '.join(hex(id(t)) for t in inner_tasks if t.done() and t._exception is not None)
        total_warnings += bool(bad_inner_tasks)
        embed.add_field(name='Inner Tasks', value=f'Total: {len(inner_tasks)}\nFailed: {bad_inner_tasks or 'None'}')
        embed.add_field(name='Events Waiting', value=f'Total: {len(event_tasks)}', inline=False)

        command_waiters = len(self._command_data_batch)
        description.append(f'Commands Waiting: {command_waiters}')

        avatar_waiters = len(self._avatar_data_batch)
        description.append(f'Avatars Waiting: {avatar_waiters}')

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

    @command(hidden=True, description='Shows the current gateway traffic interaction with the bot.')
    @commands.is_owner()
    async def gateway(self, ctx: Context) -> None:
        """Gateway related stats."""
        yesterday = discord.utils.utcnow() - datetime.timedelta(days=1)
        colour = helpers.Colour.white()

        identifies = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.identifies.items()
        }
        resumes = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.resumes.items()
        }

        total_identifies = sum(identifies.values())

        builder = [
            f'Total RESUME(s): `{sum(resumes.values())}`',
            f'Total IDENTIFY(s): `{total_identifies}`',
        ]

        # shard_count = len(self.bot.shards)
        # if total_identifies > (shard_count * 10):
        #     issues = 2 + (total_identifies // 10) - shard_count
        # else:
        #     issues = 0
        #
        # for shard_id, shard in self.bot.shards.items():
        #     badge = None
        #     if shard.is_closed():
        #         badge = Emojis.Status.offline
        #         issues += 1
        #     elif shard._parent._task and shard._parent._task.done():
        #         exc = shard._parent._task.exception()
        #         if exc is not None:
        #             badge = '\N{FIRE}'
        #             issues += 1
        #         else:
        #             badge = '\U0001f504'
        #
        #     if badge is None:
        #         badge = Emojis.Status.online
        #
        #     stats = []
        #     identify = identifies.get(shard_id, 0)
        #     resume = resumes.get(shard_id, 0)
        #     if resume != 0:
        #         stats.append(f'R: {resume}')
        #     if identify != 0:
        #         stats.append(f'ID: {identify}')
        #
        #     if stats:
        #         builder.append(f'Shard ID {shard_id}: {badge} ({', '.join(stats)})')
        #     else:
        #         builder.append(f'Shard ID {shard_id}: {badge}')
        #
        # if issues == 0:
        #     colour = helpers.Colour.lime_green()
        # elif issues < len(self.bot.shards) // 4:
        #     colour = helpers.Colour.energy_yellow()
        # else:
        #     colour = helpers.Colour.light_red()

        embed = discord.Embed(colour=colour, title='Gateway (last 24 hours)')
        embed.description = '\n'.join(builder)
        embed.set_footer(text='None warnings')
        await ctx.send(embed=embed)

    @staticmethod
    async def tabulate_query(ctx: Context, query: str, *args: Any) -> None:
        records = await ctx.db.fetch(query, *args)

        if len(records) == 0:
            await ctx.send_error('No results found.')
            return

        headers = list(records[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        rendered = table.render()

        fp = io.BytesIO(rendered.strip().encode('utf-8'))
        await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))

    @group(
        'command',
        invoke_without_command=True,
        hidden=True,
        description='Shows the current command usage statistics.',
    )
    async def _cmd(self, ctx: Context) -> None:
        """Shows the current command usage statistics."""
        if self.bot.is_owner(ctx.author) and ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_cmd.command(name='stats', description='Shows the current command usage statistics.')
    @describe(limit='The number of commands to display.')
    @commands.is_owner()
    async def command_stats(self, ctx: Context, *, limit: int = 12) -> None:
        """Shows the current command usage statistics.
        Note: Use a negative number to display from the bottom.
        """
        counter = self.bot.command_stats
        total = sum(counter.values())
        slash_commands = self.bot.command_types_used[True]

        delta = discord.utils.utcnow() - self.bot.startup_timestamp
        minutes = delta.total_seconds() / 60
        cpm = total / minutes

        if limit > 0:
            common = counter.most_common(limit)
            title = f'Top `{limit}` Commands'
        else:
            common = counter.most_common()[limit:]
            title = f'Bottom `{limit}` Commands'

        chart = BarChart(
            data=dict(sorted(dict(common).items(), key=lambda item: item[1], reverse=True)),
            title=f'{total} total commands used ({slash_commands} slash command uses) ({cpm:.2f}/minute)')
        images = [chart.create(merge=True)]
        await ctx.send(f'## {title}')
        await FilePaginator.start(ctx, entries=images, per_page=1)

    @_cmd.group(
        name='history',
        hidden=True,
        invoke_without_command=True,
        description='Command history related commands.',
    )
    @describe(limit='The limit of records to show.')
    @commands.is_owner()
    async def command_history(self, ctx: Context, limit: int = 15) -> None:
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

    @command_history.command(
        name='for',
        hidden=True,
        description='Command history for a command.',
    )
    @describe(days='The amount of days to look back.', command='The command to look for.')
    @commands.is_owner()
    async def command_history_for(self, ctx: Context, days: int = 7, *, command: str) -> None:
        """Command history for a command."""
        async with ctx.channel.typing():
            query = """
                SELECT *,
                       t.success + t.failed AS "total"
                FROM (SELECT guild_id,
                             SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                             SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                      FROM commands
                      WHERE command = $1
                        AND used > (CURRENT_TIMESTAMP - $2::interval)
                      GROUP BY guild_id) AS t
                ORDER BY "total" DESC
                LIMIT 30;
            """
            await self.tabulate_query(ctx, query, command, datetime.timedelta(days=days))

    @command_history.command(
        name='guild',
        hidden=True,
        aliases=['server'],
        description='Command history for a guild.',
    )
    @describe(guild_id='The guild to show the command history for.')
    @commands.is_owner()
    async def command_history_guild(self, ctx: Context, guild_id: int) -> None:
        """Command history for a guild."""
        async with ctx.channel.typing():
            query = """
                SELECT CASE failed
                           WHEN TRUE THEN command || ' [!]'
                           ELSE command
                           END AS "command",
                       channel_id,
                       author_id,
                       used
                FROM commands
                WHERE guild_id = $1
                ORDER BY used DESC
                LIMIT 15;
            """
            await self.tabulate_query(ctx, query, guild_id)

    @command_history.command(
        name='user',
        hidden=True,
        aliases=['member'],
        description='Command history for a user.',
    )
    @describe(user_id='The user to show the command history for.')
    @commands.is_owner()
    async def command_history_user(self, ctx: Context, user_id: int) -> None:
        """Command history for a user."""
        async with ctx.channel.typing():
            query = """
                SELECT CASE failed
                           WHEN TRUE THEN command || ' [!]'
                           ELSE command
                           END AS "command",
                       guild_id,
                       used
                FROM commands
                WHERE author_id = $1
                ORDER BY used DESC
                LIMIT 20;
            """
            await self.tabulate_query(ctx, query, user_id)

    @command_history.command(
        name='log',
        hidden=True,
        description='Command history log for the last N days.',
    )
    @describe(days='The amount of days to look back.')
    @commands.is_owner()
    async def command_history_log(self, ctx: Context, days: int = 7) -> None:
        """Command history log for the last N days."""
        async with ctx.channel.typing():
            all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}
            records = await self.get_commands_stats(days=days)
            for record in records:
                if record['command'] in all_commands:
                    all_commands[record['command']] = record['uses']

            as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
            table = TabularData()
            table.set_columns(['Command', "uses"])
            table.add_rows(tup for tup in as_data)
            rendered = table.render()

            embed = discord.Embed(title='Summary', colour=discord.Colour.green())
            embed.set_footer(text='Since').timestamp = discord.utils.utcnow() - datetime.timedelta(days=days)

            top_ten = '\n'.join(f'{record['command']}: {record['uses']}' for record in records[:10])
            bottom_ten = '\n'.join(f'{record['command']}: {record['uses']}' for record in records[-10:])
            embed.add_field(name='Top 10', value=top_ten)
            embed.add_field(name='Bottom 10', value=bottom_ten)

            unused = ', '.join(name for name, uses in as_data if uses == 0)
            if len(unused) > 1024:
                unused = 'Way too many...'

            embed.add_field(name='Unused', value=unused, inline=False)

            await ctx.send(
                embed=embed,
                file=discord.File(io.BytesIO(rendered.encode()), filename='full_results.accesslog')
            )

    @command_history.command(
        name='cog',
        hidden=True,
        description='Command history for a cog or grouped by a cog.'
    )
    @describe(days='The amount of days to look back.', cog_name='The cog to show the command history for.')
    @commands.is_owner()
    async def command_history_cog(self, ctx: Context, days: int = 7, *, cog_name: str | None = None) -> None:
        """Command history for a cog or grouped by a cog."""
        async with ctx.channel.typing():
            interval = datetime.timedelta(days=days)
            if cog_name is not None:
                cog = self.bot.get_cog(cog_name)
                if cog is None:
                    await ctx.send_error(f'Unknown Cog: {cog_name}')
                    return

                query = """
                    SELECT *,
                           t.success + t.failed AS "total"
                    FROM (SELECT command,
                                 SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                                 SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                          FROM commands
                          WHERE command = any ($1::text[])
                            AND used > (CURRENT_TIMESTAMP - $2::interval)
                          GROUP BY command) AS t
                    ORDER BY "total" DESC
                    LIMIT 30;
                """
                return await self.tabulate_query(ctx, query, [c.qualified_name for c in cog.walk_commands()], interval)

            query = """
                SELECT *,
                       t.success + t.failed AS "total"
                FROM (SELECT command,
                             SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                             SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                      FROM commands
                      WHERE used > (CURRENT_TIMESTAMP - $1::interval)
                      GROUP BY command) AS t;
            """
            data = defaultdict(CommandUsageCount)
            records = await ctx.db.fetch(query, interval)
            for record in records:
                command = self.bot.get_command(record['command'])
                if command is None or command.cog is None:
                    data['No Cog'].add(record)
                else:
                    data[command.cog.qualified_name].add(record)

            table = TabularData()
            table.set_columns(['Cog', 'Success', 'Failed', "total"])
            data = sorted(
                [(cog, e.success, e.failed, e.total) for cog, e in data.items()], key=lambda t: t[-1],
                reverse=True
            )
            table.add_rows(data)
            rendered = table.render()
            await ctx.safe_send(f'```\n{rendered}\n```')


async def setup(bot: Bot) -> None:
    if not hasattr(bot, 'command_stats'):
        bot.command_stats = Counter()
    if not hasattr(bot, 'socket_stats'):
        bot.socket_stats = Counter()
    if not hasattr(bot, 'command_types_used'):
        bot.command_types_used = Counter()

    cog = Stats(bot)
    await bot.add_cog(cog)

    bot.logging_handler = handler = LoggingHandler(cog)
    logging.getLogger().addHandler(handler)


async def teardown(bot: Bot) -> None:
    logging.getLogger().removeHandler(bot.log_handler)
    del bot.log_handler
