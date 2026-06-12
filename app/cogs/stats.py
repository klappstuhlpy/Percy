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
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
from discord.utils import MISSING
from expiringdict import ExpiringDict

import config
from app.cogs.games.models import Game
from app.core import Bot, Cog, Context
from app.core.models import command, cooldown, describe, group
from app.core.pagination import FilePaginator
from app.core.views import UserInfoView
from app.rendering import resize_to_limit
from app.services import (
    ConnectionState,
    HealthLevel,
    assess_bot_health,
    count_code_stats,
    summarize_gateway_traffic,
    summarize_presence,
)
from app.utils import (
    AnsiColor,
    AnsiStringBuilder,
    TabularData,
    Timer,
    censor_object,
    fnumb,
    get_asset_url,
    helpers,
    medal_emoji,
)
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

    __slots__ = ("failed", "success", "total")

    def __init__(self) -> None:
        self.success = 0
        self.failed = 0
        self.total = 0

    def add(self, record: asyncpg.Record) -> None:
        self.success += record["success"]
        self.failed += record["failed"]
        self.total += record["total"]


class LoggingHandler(logging.Handler):
    def __init__(self, cog: Stats) -> None:
        self.cog: Stats = cog
        super().__init__(logging.INFO)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in ("discord.gateway", "bot")

    def emit(self, record: logging.LogRecord) -> None:
        self.cog.add_record(record)


class Stats(Cog):
    """Bot Statistics and Information."""

    emoji = "<:graph:1322354647910055967>"

    _presence_map: ClassVar[dict[discord.Status, str]] = {
        discord.Status.online: "Online",
        discord.Status.idle: "Idle",
        discord.Status.dnd: "Do Not Disturb",
        discord.Status.offline: "Offline",
    }

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.process = psutil.Process()

        self._command_data_batch: list[CommandBatchEntry] = []
        self._avatar_data_batch: list[AvatarBatchEntry] = []

        self._logging_queue = asyncio.Queue()
        self.__loging_worker_task: asyncio.Task | None = None

        self.__LOOPS: list[Any] = [self.cleanup_presence_history, self.command_insert, self.avatar_insert]

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
        await self.bot.db.stats.delete_old_presence_history()

    @tasks.loop(seconds=10.0)
    async def command_insert(self) -> None:
        """|coro|

        A task that inserts the command data batch into the database.

        This task is automatically started after the cog is loaded.
        """
        if self._command_data_batch:
            await self.bot.db.stats.insert_commands(self._command_data_batch)
            total = len(self._command_data_batch)
            if total > 1:
                log.info("Registered %s commands to the database.", total)
            self._command_data_batch.clear()

    @tasks.loop(seconds=10.0)
    async def avatar_insert(self) -> None:
        """|coro|

        A task that inserts the avatar data batch into the database.

        This task is automatically started after the cog is loaded.
        """
        for data in self._avatar_data_batch:
            await self.bot.db.stats.insert_avatar(data["user_id"], data["name"], data["image"])
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
            log.exception("Unhandled exception in logging worker: %s", exc)
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
            return None
        if ctx.command is None:
            return None

        cmd_name: str = ctx.command.qualified_name
        is_app_command = ctx.interaction is not None
        self.bot.command_stats[cmd_name] += 1
        self.bot.command_types_used[is_app_command] += 1
        message = ctx.message
        if ctx.guild is None:
            destination = "Private Message"
            guild_id = None
        else:
            destination = f"#{message.channel} ({message.guild})"
            guild_id = ctx.guild.id

        if ctx.is_interaction and ctx.interaction is not None and ctx.interaction.command:
            content = f"/{ctx.interaction.command.qualified_name}"
        else:
            content = message.content

        log.info("%s: %s in %s: %s", ctx.now.replace(tzinfo=None), message.author, destination, content)
        self._command_data_batch.append(
            CommandBatchEntry(
                guild=guild_id,
                channel=ctx.channel.id,
                author=ctx.author.id,
                used=ctx.now.isoformat(),
                prefix=ctx.prefix or "",
                command=cmd_name,
                failed=ctx.command_failed,
                app_command=is_app_command,
                error=self.bot.command_error_cache.pop(self.bot.make_command_cache_key(ctx), None),
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
            and not command.__class__.__name__.startswith("Hybrid")
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
        embed = discord.Embed(colour=helpers.Colour.lime_green(), title="New Guild")
        await self.send_guild_stats(embed, guild)

        members: Sequence[discord.Member] | list[discord.Member] = await guild.chunk() if guild.chunked else guild.members
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
                AvatarBatchEntry(user_id=member.id, name=member.name, image=scaled_avatar.getvalue())
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
            return None

        if len(member.mutual_guilds) > 1:
            return None

        avatar: bytes | None = await self._read_avatar(member)
        if avatar is None:
            return None

        scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
        self._avatar_data_batch.append(AvatarBatchEntry(user_id=member.id, name=member.name, image=scaled_avatar.getvalue()))

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
            return None

        if before.display_avatar != after.display_avatar:
            avatar: bytes | None = await self._read_avatar(after)
            if avatar:
                return None

            scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
            self._avatar_data_batch.append(
                AvatarBatchEntry(user_id=after.id, name=after.name, image=scaled_avatar.getvalue())
            )

        if before.nick != after.nick and after.nick is not None:
            await self.bot.db.stats.insert_item_history(after.id, "nickname", after.nick)

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
            await self.bot.db.stats.insert_item_history(after.id, "name", after.name)

        if before.avatar != after.avatar:
            avatar: bytes | None = await self._read_avatar(after)
            if avatar is None:
                return None

            scaled_avatar: io.BytesIO = await asyncio.to_thread(resize_to_limit, io.BytesIO(avatar))  # type: ignore
            self._avatar_data_batch.append(
                AvatarBatchEntry(user_id=after.id, name=after.name, image=scaled_avatar.getvalue())
            )

    @Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.bot.wait_until_ready()
        embed = discord.Embed(colour=helpers.Colour.light_red(), title="Left Guild")
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
            return None

        if not (await self.bot.db.get_user_config(after.id)).track_presence:  # type: ignore[misc]
            return None

        def _make_key(member: discord.Member) -> str:
            return f"status:{member.id}:{member.status}"

        if before.status != after.status:
            if self._presence_cache.get(_make_key(after)):
                return None

            self._presence_cache[_make_key(after)] = True

            await self.bot.db.stats.insert_presence(
                after.id,
                self._presence_map.get(after.status),
                self._presence_map.get(before.status),
            )

    async def _read_avatar(self, member: discord.Member | discord.User) -> bytes | None:
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
                return None
            if exc.status >= 500:
                await asyncio.sleep(15.0)
                await self._read_avatar(member)
            log.exception(
                "Unhandled Discord HTTPException while getting avatar for %s (%s)",
                member.name,
                member.id,
            )
            return
        return avatar

    def get_bot_uptime(self, *, brief: bool = False) -> str:
        return human_timedelta(self.bot.startup_timestamp, accuracy=None, brief=brief, suffix=False)

    @staticmethod
    def _format_commit(commit: pygit2.Commit) -> str:
        short, _, _ = commit.message.partition("\n")
        short_sha2 = str(commit.id)[0:6]
        commit_tz = datetime.timezone(datetime.timedelta(minutes=commit.commit_time_offset))
        commit_time = datetime.datetime.fromtimestamp(commit.commit_time).astimezone(commit_tz)

        offset = discord.utils.format_dt(commit_time.astimezone(datetime.UTC), "R")
        return f"[`{short_sha2}`]({repo_url}commit/{commit.id!s}) {short} ({offset})"

    def get_last_commits(self, count: int = 4, repo_path: str = str(path)) -> str:
        repo = pygit2.Repository(Path(repo_path, ".git"))
        commits = list(itertools.islice(repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL), count))  # type: ignore[arg-type]
        return "\n".join(self._format_commit(c) for c in commits)

    @executor
    def project_stats_counter(self) -> str:
        root = Path(__file__).parent.parent
        stats = count_code_stats(root, ignored=[root / "venv"])

        builder = AnsiStringBuilder()
        builder.append("Files:       ", color=AnsiColor.gray)
        builder.append(str(stats.files), color=AnsiColor.green).newline()
        builder.append("Classes:     ", color=AnsiColor.gray)
        builder.append(str(stats.classes), color=AnsiColor.green).newline()
        builder.append("Functions:   ", color=AnsiColor.gray)
        builder.append(str(stats.functions), color=AnsiColor.green).newline()
        builder.append("Comments:    ", color=AnsiColor.gray)
        builder.append(str(stats.comments), color=AnsiColor.green).newline()
        builder.append("Lines:       ", color=AnsiColor.gray)
        builder.append(str(stats.lines), color=AnsiColor.green).newline()
        builder.append("Characters:  ", color=AnsiColor.gray)
        builder.append(str(stats.characters), color=AnsiColor.green)

        return str(builder)

    async def get_commands_stats(
        self,
        guild_id: int | None = None,
        author_id: int | None = None,
        *,
        days: int | None = None,
        group_by: Literal["author_id", "command", "guild_id"] = "command",
        limit: int = 5,
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
        return await self.bot.db.stats.get_command_usage(guild_id, author_id, days=days, group_by=group_by, limit=limit)

    @command(hidden=True, description="Shows the current socket event statistics.")
    async def socketstats(self, ctx: Context) -> None:
        """Shows the current socket event statistics."""
        await self.bot.wait_until_ready()
        delta = discord.utils.utcnow() - self.bot.startup_timestamp
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes
        image = await self.bot.render.bar_chart(
            dict(sorted(self.bot.socket_stats.items(), key=lambda item: item[1], reverse=True)),
            f"{total} socket events observed ({cpm:.2f}/minute)",
            merge=True,
        )
        await FilePaginator.start(ctx, entries=[image], per_page=1)  # type: ignore[arg-type]

    @command(description="Tells you how long the bot has been up for.")
    async def uptime(self, ctx: Context) -> None:
        """Tells you how long the bot has been up for."""
        await self.bot.wait_until_ready()
        await ctx.send(f"Uptime: **{self.get_bot_uptime()}**")

    @command(description="Tells you information about the bot itself.")
    async def about(self, ctx: Context) -> None:
        """Tells you information about the bot itself."""
        await ctx.typing()

        try:
            revision = self.get_last_commits()
        except pygit2.GitError:
            revision = "*Not available.*"

        assert ctx.bot.user is not None
        url = discord.utils.oauth_url(
            client_id=ctx.bot.user.id,
            permissions=discord.Permissions(8),
            scopes=("bot", "applications.commands"),
        )

        embed = discord.Embed(
            url=url,
            title="Official Bot Invite",
            description="[**Support Server Invite**](https://discord.com/3jSYQ9VNbA)\n\nLatest Changes:\n" + revision,
            colour=helpers.Colour.white(),
        )

        assert isinstance(config.owners, int)
        owner = ctx.bot.get_user(config.owners)

        embed.set_author(name=str(owner), icon_url=get_asset_url(owner) if owner else None)
        embed.set_thumbnail(url=get_asset_url(self.bot.user) if self.bot.user else None)  # type: ignore

        embed.add_field(name="Version", value=version, inline=False)

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

        embed.add_field(
            name="Members",
            value=f"`{total_members}` total\n`{total_unique}` unique\n"
            f"Bot percentage: `{(total_unique / total_members):.2%}`",
        )
        embed.add_field(name="Channels", value=f"`{text + voice}` total\n`{text}` text\n`{voice}` voice")

        memory_usage = self.process.memory_full_info().uss / 1024**2
        cpu_usage = self.process.cpu_percent() / (psutil.cpu_count() or 1)

        embed.add_field(name="Guilds", value=guilds)
        embed.add_field(name="Commands run since last reboot", value=sum(self.bot.command_stats.values()))
        embed.add_field(name="Uptime", value=self.get_bot_uptime(brief=True))
        embed.add_field(name="​", value="​")

        file_stats = await self.project_stats_counter()
        embed.add_field(name="File Stats", value=f"```ansi\n{file_stats}```")

        builder = AnsiStringBuilder()
        builder.append("Memory Usage:  ", color=AnsiColor.gray)
        builder.append(f"{memory_usage:.2f} MiB", color=AnsiColor.green).newline()
        builder.append("CPU Usage:     ", color=AnsiColor.gray)
        builder.append(f"{cpu_usage:.2f}%", color=AnsiColor.green).newline()
        builder.append("Disk Usage:    ", color=AnsiColor.gray)
        builder.append(f"{psutil.disk_usage(str(Path(__file__).parent.parent)).percent}%", color=AnsiColor.green).newline()
        embed.add_field(name="System Stats", value=f"```ansi\n{builder!s}```")

        embed.set_footer(
            text=f"Made with discord.py v{discord.__version__}", icon_url="https://klappstuhl.me/gallery/raw/lVUYV.png"
        )
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)

    @group(
        name="stats",
        description="Tells you command usage stats for the server or a member.",
        invoke_without_command=True,
        guild_only=True,
    )
    @cooldown(1, 5.0, commands.BucketType.guild)
    @describe(member="The member to show stats for.")
    async def stats(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Tells you command usage stats for the server or a member."""
        assert ctx.guild is not None
        async with ctx.typing():
            embed = discord.Embed()

            if member is None:
                embed.title = "Server Command Stats"
                embed.colour = helpers.Colour.white()

                count: tuple[int, datetime.datetime] = await ctx.db.stats.get_command_summary(  # type: ignore
                    ctx.guild.id
                )

                top_commands = await self.get_commands_stats(ctx.guild.id) or []
                value = (
                    "\n".join(
                        f"{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)"
                        for i, record in enumerate(top_commands)
                    )
                    or "*No Command Usages available.*"
                )
                embed.add_field(name="Top Commands", value=value, inline=True)

                top_commands_today = await self.get_commands_stats(ctx.guild.id, days=1) or []
                value = (
                    "\n".join(
                        f"{medal_emoji(index)}: {cmd} (`{uses}` uses)"
                        for (index, (cmd, uses)) in enumerate(top_commands_today)
                    )
                    or "*No Command Usages available.*"
                )
                embed.add_field(name="Top Commands Today", value=value, inline=True)

                # placeholder
                embed.add_field(name="\u200b", value="\u200b", inline=True)

                top_users = await self.get_commands_stats(ctx.guild.id, group_by="author_id") or []
                value = (
                    "\n".join(
                        f"{medal_emoji(i)}: <@!{record['author_id']}> (`{record['uses']}` bot uses)"
                        for i, record in enumerate(top_users)
                    )
                    or "*No Command Bot Users available.*"
                )
                embed.add_field(name="Top Command Users", value=value, inline=True)

                top_users_today = await self.get_commands_stats(ctx.guild.id, group_by="author_id", days=1) or []
                value = (
                    "\n".join(
                        f"{medal_emoji(i)}: <@!{record['author_id']}> (`{record['uses']}` bot uses)"
                        for i, record in enumerate(top_users_today)
                    )
                    or "*No Command Bot Users available.*"
                )
                embed.add_field(name="Top Command Users Today", value=value, inline=True)

                embed.set_footer(text="Tracking command usage since")
            else:
                embed.title = "Command Stats"
                embed.colour = member.colour
                embed.set_author(name=str(member), icon_url=get_asset_url(member))

                count: tuple[int, datetime.datetime] = await ctx.db.stats.get_command_summary(  # type: ignore
                    ctx.guild.id, member.id
                )

                most_used = await self.get_commands_stats(ctx.guild.id, member.id) or []
                value = (
                    "\n".join(
                        f"{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)"
                        for i, record in enumerate(most_used)
                    )
                    or "*No Command Usages available.*"
                )

                embed.add_field(name="Most Used Commands", value=value, inline=False)

                most_used_today = await self.get_commands_stats(ctx.guild.id, member.id, days=1) or []
                value = (
                    "\n".join(
                        f"{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)"
                        for i, record in enumerate(most_used_today)
                    )
                    or "*No Command Usages available.*"
                )

                embed.add_field(name="Most Used Commands Today", value=value, inline=False)

                embed.set_footer(text="First command used")

            embed.description = f"Total of `{count[0]}` commands used."
            embed.timestamp = count[1].replace(tzinfo=datetime.UTC) if count[1] else discord.utils.utcnow()

            await ctx.send(embed=embed)

    @stats.command(
        name="global",
        description="Global all time command statistics.",
    )
    async def stats_global(self, ctx: Context) -> None:
        """Global all time command statistics."""
        await ctx.typing()

        total: int = await ctx.db.stats.count_all_commands()
        embed = discord.Embed(title="Command Stats", colour=helpers.Colour.white())
        embed.description = f"`{total}` commands used."

        top_commands = await self.get_commands_stats() or []
        value = (
            "\n".join(
                f"{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)" for i, record in enumerate(top_commands)
            )
            or "*No Command Usages available.*"
        )
        embed.add_field(name="Top Commands", value=value, inline=False)

        top_guilds = await self.get_commands_stats(group_by="guild_id") or []
        value = []
        for i, record in enumerate(top_guilds):
            if record["guild_id"] is None:
                guild = "Private Message"
            else:
                guild = censor_object(
                    self.bot.blacklist, self.bot.get_guild(record["guild_id"]) or f"<Unknown {record['guild_id']}>"
                )
            value.append(f"{medal_emoji(i)}: {guild} (`{record['uses']}` uses)")
        embed.add_field(name="Top Guilds", value="\n".join(value), inline=False)

        value.clear()

        top_users = await self.get_commands_stats(group_by="author_id") or []
        for i, record in enumerate(top_users):
            user = censor_object(
                self.bot.blacklist, self.bot.get_user(record["author_id"]) or f"<Unknown {record['author_id']}>"
            )
            value.append(f"{medal_emoji(i)}: {user} (`{record['uses']}` uses)")
        embed.add_field(name="Top Users", value="\n".join(value), inline=False)

        await ctx.send(embed=embed)

    @stats.command(
        name="today",
        description="Global command statistics for the day.",
    )
    async def stats_today(self, ctx: Context) -> None:
        """Global command statistics for the day."""
        await ctx.typing()

        total = await ctx.db.stats.get_daily_status_counts()
        failed, success, question = 0, 0, 0
        for state, count in total:
            match state:
                case False:
                    success += count
                case True:
                    failed += count
                case _:
                    question += count

        embed = discord.Embed(title="Last 24 Hour Command Stats", colour=helpers.Colour.white())
        embed.description = (
            f"`{failed + success + question}` commands used today. "
            f"(`{success}` succeeded, `{failed}` failed, `{question}` unknown)"
        )

        top_commands = await self.get_commands_stats(days=1) or []
        value = (
            "\n".join(
                f"{medal_emoji(i)}: {record['command']} (`{record['uses']}` uses)" for i, record in enumerate(top_commands)
            )
            or "*No Command Usages available.*"
        )
        embed.add_field(name="Top Commands", value=value, inline=False)

        top_guilds = await self.get_commands_stats(group_by="guild_id", days=1) or []
        value = []
        for i, record in enumerate(top_guilds):
            if record["guild_id"] is None:
                guild = "Private Message"
            else:
                guild = censor_object(
                    self.bot.blacklist, self.bot.get_guild(record["guild_id"]) or f"<Unknown {record['guild_id']}>"
                )
            value.append(f"{medal_emoji(i)}: {guild} (`{record['uses']}` uses)")
        embed.add_field(name="Top Guilds", value="\n".join(value), inline=False)

        top_users = await self.get_commands_stats(group_by="author_id", days=1) or []
        for i, record in enumerate(top_users):
            user = censor_object(
                self.bot.blacklist, self.bot.get_user(record["author_id"]) or f"<Unknown {record['author_id']}>"
            )
            value.append(f"{medal_emoji(i)}: {user} (`{record['uses']}` uses)")
        embed.add_field(name="Top Users", value="\n".join(value), inline=False)

        await ctx.send(embed=embed)

    async def game_autocomplete(self, _: discord.Interaction, current: str) -> list[Choice[str]]:
        """Autocomplete over the tracked games."""
        return [
            Choice(name=f"{game.icon} {game.label}", value=game.value)
            for game in Game
            if current.lower() in game.label.lower()
        ][:25]

    @staticmethod
    def _format_streak(current_streak: int) -> str:
        """Pretty-prints an active win/loss streak."""
        if current_streak > 0:
            return f"\N{FIRE} {current_streak}W"
        if current_streak < 0:
            return f"\N{SNOWFLAKE} {abs(current_streak)}L"
        return "—"

    @staticmethod
    def _winrate(won: int, played: int) -> float:
        return (won / played * 100) if played else 0.0

    @stats.command(
        name="games",
        alias="game",
        description="Shows a member's game record (wins, losses, win-rate and profit).",
    )
    @describe(member="The member to show game stats for.")
    async def stats_games(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows a member's per-game record across all casino & party games."""
        assert ctx.guild is not None
        target = member or ctx.author

        rows = await ctx.db.game_stats.get_member_games(ctx.guild.id, target.id)
        if not rows:
            who = "You have" if target == ctx.author else f"**{target.display_name}** has"
            await ctx.send_info(f"{who} not played any tracked games yet.")
            return

        totals = await ctx.db.game_stats.get_member_totals(ctx.guild.id, target.id)
        assert totals is not None

        embed = discord.Embed(title="Game Stats", colour=target.colour)
        embed.set_author(name=str(target), icon_url=get_asset_url(target))

        win_rate = self._winrate(totals["won"], totals["played"])
        embed.description = (
            f"**{totals['played']}** rounds played • **{totals['won']}**W / **{totals['lost']}**L "
            f"({win_rate:.0f}% win-rate)\n"
            f"Net profit: {Emojis.Economy.cash} **{fnumb(totals['profit'])}** • "
            f"Biggest win: {Emojis.Economy.cash} **{fnumb(totals['biggest_win'])}**"
        )

        for record in rows:
            game = Game(record["game"])
            rate = self._winrate(record["won"], record["played"])
            value = (
                f"`{record['played']}` played • `{record['won']}`W / `{record['lost']}`L"
                f"{f' / `{record['tied']}`T' if record['tied'] else ''} • **{rate:.0f}%**\n"
                f"Net: {Emojis.Economy.cash} **{fnumb(record['profit'])}** • "
                f"Streak: {self._format_streak(record['current_streak'])} "
                f"(best {record['best_streak']}W)"
            )
            embed.add_field(name=f"{game.icon} {game.label}", value=value, inline=False)

        await ctx.send(embed=embed)

    @stats.command(
        name="gameboard",
        alias="gametop",
        description="Server leaderboard for games, ranked by wins.",
    )
    @describe(game="Limit the leaderboard to a single game (defaults to all games combined).")
    @app_commands.autocomplete(game=game_autocomplete)  # type: ignore
    async def stats_gameboard(self, ctx: Context, *, game: str | None = None) -> None:
        """Server-wide game leaderboard, ranked by total wins (ties broken by win-rate)."""
        assert ctx.guild is not None

        selected: Game | None = None
        if game is not None:
            try:
                selected = Game(game.lower())
            except ValueError:
                match = next((g for g in Game if g.label.lower() == game.lower()), None)
                if match is None:
                    await ctx.send_error(
                        f"Unknown game **{game}**. Choose one of: "
                        + ", ".join(g.label for g in Game)
                        + "."
                    )
                    return
                selected = match

        rows = await ctx.db.game_stats.get_leaderboard(
            ctx.guild.id, game=selected.value if selected else None, metric="won", limit=10
        )
        if not rows:
            scope = f" for **{selected.label}**" if selected else ""
            await ctx.send_info(f"No game results have been recorded{scope} yet.")
            return

        title = f"{selected.icon} {selected.label} Leaderboard" if selected else "🏆 Game Leaderboard"
        embed = discord.Embed(title=title, colour=helpers.Colour.white())
        embed.description = (
            f"Top players in **{ctx.guild.name}**"
            + ("" if selected else " across all games")
            + ", ranked by wins."
        )

        lines = []
        for i, record in enumerate(rows):
            rate = self._winrate(record["won"], record["played"])
            lines.append(
                f"{medal_emoji(i)} <@!{record['user_id']}> — **{record['won']}**W / **{record['lost']}**L "
                f"({rate:.0f}%) • {Emojis.Economy.cash} **{fnumb(record['profit'])}**"
            )
        embed.add_field(name="Rankings", value="\n".join(lines), inline=False)

        if not selected:
            overview = await ctx.db.game_stats.get_guild_overview(ctx.guild.id)
            if overview:
                popular = "\n".join(
                    f"{Game(r['game']).icon} **{Game(r['game']).label}** — `{r['played']}` rounds"
                    for r in overview[:5]
                )
                embed.add_field(name="Most Played", value=popular, inline=False)

        embed.set_footer(text="Use /stats games to see your own record.")
        await ctx.send(embed=embed)

    async def send_guild_stats(self, embed: discord.Embed, guild: discord.Guild) -> None:
        embed.add_field(name="Name", value=guild.name)
        embed.add_field(name="ID", value=guild.id)
        embed.add_field(name="Shard ID", value=guild.shard_id or "N/A")
        embed.add_field(name="Owner", value=f"{guild.owner} (ID: `{guild.owner_id}`)")

        bots = sum(m.bot for m in guild.members)
        total = guild.member_count or 1
        embed.add_field(name="Members", value=str(total))
        embed.add_field(name="Bots", value=f"{bots} ({bots / total:.2%})")
        embed.set_thumbnail(url=get_asset_url(guild))

        if guild.me:
            embed.timestamp = guild.me.joined_at

        await self.bot.stats_webhook.send(embed=embed)

    async def get_presence_history(self, user_id: int, /, *, days: int = 30) -> list[asyncpg.Record]:
        return await self.bot.db.stats.get_presence_history(user_id, days=days)

    async def get_item_history(self, user_id: int, item_type: Literal["name", "nickname"]) -> list[asyncpg.Record]:
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
        return await self.bot.db.stats.get_item_history(user_id, item_type)

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
        return await self.bot.db.stats.get_avatar_history(member.id)

    @command("names", alias="ns", description="Shows the username history of a user.", hybrid=True, guild_only=True)
    @describe(member="The member to show the username history for.")
    async def names(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member | discord.User = member or ctx.author

        usernames: list[asyncpg.Record] = await self.get_item_history(user.id, "name")
        nicknames: list[asyncpg.Record] = await self.get_item_history(user.id, "nickname")

        if not usernames and not nicknames:
            await ctx.send_error("No name history found.")
            return

        un_text = ", ".join(f"`{name}` {discord.utils.format_dt(changed_at, 'R')}" for name, changed_at in usernames)
        nn_text = ", ".join(f"`{name}` {discord.utils.format_dt(changed_at, 'R')}" for name, changed_at in usernames)
        await ctx.send(
            f"""
            ### Username History for {user}
            **Usernames:** {un_text or "*No usernames found.*"}
            **Nicknames:** {nn_text or "*No nicknames found.*"}
            """
        )

    @command("lastseen", alias="ls", description="Shows when a user was last seen.", hybrid=True, guild_only=True)
    @describe(member="The member to show the last seen for.")
    async def last_seen(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member | discord.User = member or ctx.author
        records = await self.get_presence_history(user.id, days=30)

        if not records:
            await ctx.send_error("No presence history found.")
            return

        last_seen = records[0]["changed_at"]

        subject = "You were" if user == ctx.author else f"{user} was"
        await ctx.send(f"{subject} last seen *{discord.utils.format_dt(last_seen, 'R')}*")

    @command("avatarhistory", description="Shows the avatar history of a user.", alias="avyh", hybrid=True, guild_only=True)
    @describe(member="The member to show the avatar history for.")
    async def avatar_history(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows the avatar history of a user."""
        user: discord.Member | discord.User = member or ctx.author
        await ctx.defer(typing=True)

        async with ctx.channel.typing():
            with Timer() as timer:
                history = await self.get_avatar_history(user)

                if not history:
                    await ctx.send_error("No avatar history found.")
                    return

                fetching_time = timer.reset()

                avatars = [x["avatar"] for x in history]
                if not avatars:
                    return

                file = await self.bot.render.avatar_collage(avatars)

        embed = discord.Embed(
            title=f"Avatar Collage for {user}",
            description=(
                f"`{'Fetching':<{12}}:` {fetching_time:.3f}s\n"
                f"`{'Generating':<{12}}:` {timer.seconds:.3f}s\n\n"
                f"Showing `{len(history)}` of up to `100` changes."
            ),
            timestamp=history[-1]["changed_at"],
            colour=helpers.Colour.white(),
        )
        embed.set_image(url=f"attachment://{file.filename if file else 'collage.png'}")
        embed.set_footer(text="Last updated")
        await ctx.send(embed=embed, file=file)

    @command("presence", alias="ps", description="Shows the presence history of a user.", hybrid=True, guild_only=True)
    @describe(member="The member to show the presence history for.")
    async def presence(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member | discord.User = member or ctx.author
        query_days = 30

        async with ctx.channel.typing():
            with Timer() as timer:
                history: list[asyncpg.Record] = await self.get_presence_history(user.id, days=query_days)

                if not history:
                    await ctx.send_error("No presence history found.")
                    return

                fetching_time = timer.reset()

                breakdown = summarize_presence((record["changed_at"], record["status_before"]) for record in history)

                if not breakdown.has_data:
                    await ctx.send_error("Not enough data to generate a chart.")
                    return

                analyzing_time = timer.reset()

                canvas: discord.File = await self.bot.render.presence_chart(
                    labels=["Online", "Offline", "DND", "Idle"],
                    colors=["#43b581", "#747f8d", "#f04747", "#fba31c"],
                    values=[
                        int(breakdown.durations["Online"]),
                        int(breakdown.durations["Offline"]),
                        int(breakdown.durations["Do Not Disturb"]),
                        int(breakdown.durations["Idle"]),
                    ],
                )

        embed = discord.Embed(
            title=f"Past 1 Month User Activity of {user}",
            description=(
                f"`{'Fetching':<{12}}:` {fetching_time:.3f}s\n"
                f"`{'Analyzing':<{12}}:` {analyzing_time:.3f}s\n"
                f"`{'Generating':<{12}}:` {timer.seconds:.3f}s"
            ),
            timestamp=breakdown.earliest,
            colour=helpers.Colour.white(),
        )
        embed.set_image(url=f"attachment://{canvas.filename}")
        embed.set_footer(text="Watching since")
        await ctx.send(embed=embed, file=canvas)

    @command(name="userinfo", alias="ui", description="Shows information about a user.", hybrid=True, guild_only=True)
    @describe(member="The member to show information for.")
    async def userinfo(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        assert isinstance(ctx.author, discord.Member)
        user: discord.Member | discord.User = member or ctx.author
        await ctx.defer()

        embed = discord.Embed(colour=helpers.Colour.white())

        informations: list[str] = []
        guild_related: list[str] = []

        embed.set_author(name=str(user))

        informations.append(f"**Name:** {user.mention}")
        informations.append(f"**ID:** `{user.id}`")
        informations.append(f"**is Bot:** `{user.bot}`")

        records = await self.get_presence_history(user.id, days=30)
        last_seen = discord.utils.format_dt(records[0]["changed_at"], "R") if records else "`Unknown`"
        informations.append(f"**Last Seen:** {last_seen}")

        informations.append(f"**Created:** {discord.utils.format_dt(user.created_at, 'R')}")
        informations.append(f"**Shared Servers:** {len(user.mutual_guilds)}")
        informations.append(f"**System User:** `{user.system}`")

        embed.add_field(name="User Information", value="\n".join(informations), inline=False)

        guild_related.append(f"**Joined:** {discord.utils.format_dt(user.joined_at, 'R') if user.joined_at else 'Unknown'}")
        guild_related.append(
            f"**Join Position:** `{sum(m.joined_at < user.joined_at for m in user.guild.members if m.joined_at and user.joined_at) + 1}/{len(user.guild.members)}`"  # type: ignore
        )
        guild_related.append(f"**Top Role:** {user.top_role.mention}")
        guild_related.append(f"**Colour:** `{user.colour}`")

        badges_to_emoji = {
            "partner": "<:partner:1322355086822735972>",  # Emoji Server
            "verified_bot_developer": "<:earlydev:1322337994124034048>",  # Klappstuhl's Hideout
            "hypesquad_balance": "<:balance:1322354569866383402>",  # Emoji Server
            "hypesquad_bravery": "<:bravery:1322354587491110922>",  # Emoji Server
            "hypesquad_brilliance": "<:brilliance:1322354595179135047>",  # Klappstuhl's Hideout
            "bug_hunter": "<:bug_hunter_1:1322362990602883072>",  # Klappstuhl's Hideout
            "hypesquad": "<:hypesquad_events:1322363349719060540>",  # Emoji Server
            "early_supporter": "<:earlysupporter:1322363580867285013>",  # Klappstuhl's Hideout
            "bug_hunter_level_2": "<:bug_hunter_2:1322362999314583602>",  # Klappstuhl's Hideout
            "staff": "<:staff_badge:1322355128719769640>",  # Emoji Server
            "discord_certified_moderator": "<:mod_badge:1322337933428260874>",  # Emoji Server
            "active_developer": "<:active_developer:1322337889782202519>",  # Playground
        }

        misc_flags_descriptions = {
            "team_user": "Application Team User",
            "system": "System User",
            "spammer": "Spammer",
            "verified_bot": "Verified Bot",
            "bot_http_interactions": "HTTP Interactions Bot",
        }

        set_flags = {flag for flag, value in user.public_flags if value}
        subset_flags = set_flags & badges_to_emoji.keys()
        badges = [badges_to_emoji[flag] for flag in subset_flags]

        if ctx.guild is not None and ctx.guild.owner_id == user.id:
            badges.append("<:owner:1322355079109541940>")  # Emoji Server

        if isinstance(user, discord.Member) and user.premium_since is not None:
            guild_related.append(f"**Boosting Since:** `{discord.utils.format_dt(user.premium_since, 'R')}`")
            badges.append("<:booster:1322354580184633344>")  # Emoji Server

        if badges:
            embed.description = "".join(badges)

        custom_activity = next(
            (act for act in getattr(user, "activities", []) if isinstance(act, discord.CustomActivity)), None
        )
        activity = (
            f"`{discord.utils.remove_markdown(custom_activity.name)}`" if custom_activity and custom_activity.name else None
        )
        if activity:
            guild_related.append(f"**Custom Activity:** {activity}")

        voice = getattr(user, "voice", None)
        if voice is not None:
            vc = voice.channel
            other_people = len(vc.members) - 1
            voice = f"`{vc.name}` with {other_people} others" if other_people else f"`{vc.name}` by themselves"
            guild_related.append(f"**Voice:** {voice}")

        remaining_flags = (set_flags - subset_flags) & misc_flags_descriptions.keys()
        if remaining_flags:
            guild_related.append(f"**Flags:** {', '.join(misc_flags_descriptions[flag] for flag in remaining_flags)}")

        perms = user.guild_permissions.value
        guild_related.append(f"**Permissions:** [`{perms}`](https://discordapi.com/permissions.html#{perms})")

        embed.add_field(name="Guild Information", value="\n".join(guild_related), inline=False)

        if colour := user.colour.value:
            embed.colour = colour

        embed.set_thumbnail(url=get_asset_url(user))

        user = await self.bot.fetch_user(user.id)
        if user.banner:
            embed.set_image(url=user.banner.url)

        embed.set_footer(text="Buttons can also be called by using the commands: avyh, names, ps")

        await ctx.send(embed=embed, view=UserInfoView(ctx, user))

    @Cog.listener()
    async def on_command_error(self, ctx: Context, error: Exception) -> None:
        self.register_command(ctx)
        error = getattr(error, "original", error)

        if not isinstance(error, (commands.CommandInvokeError, commands.ConversionError)):
            return

        blacklist = (discord.Forbidden, discord.NotFound)
        if isinstance(error, blacklist):
            return

        embed = discord.Embed(title=f"{Emojis.warning} Command Error", colour=helpers.Colour.burgundy())
        embed.add_field(name="Name", value=ctx.command.qualified_name)
        embed.add_field(
            name="Author", value=f"[{ctx.author}](https://discord.com/users/{ctx.author.id}) (ID: {ctx.author.id})"
        )

        fmt = f"Channel: [#{ctx.channel}]({ctx.channel.jump_url}) (ID: {ctx.channel.id})\n"
        if ctx.guild:
            fmt += f"Guild: {ctx.guild} (ID: {ctx.guild.id})"
        else:
            fmt += "Guild: *<Private Message>*"

        embed.add_field(name="Location", value=fmt, inline=False)
        embed.add_field(name="Content", value=textwrap.shorten(ctx.message.content, width=1024))

        exc = "".join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        embed.description = f"```py\n{exc}\n```"
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text="occurred at")
        await self.bot.stats_webhook.send(embed=embed)

    def add_record(self, record: logging.LogRecord) -> None:
        self._logging_queue.put_nowait(record)

    async def send_log_record(self, record: logging.LogRecord) -> None:
        attributes = {"INFO": Emojis.info, "WARNING": Emojis.warning}

        emoji = attributes.get(record.levelname, "\N{CROSS MARK}")
        dt = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        msg = textwrap.shorten(f"{emoji} {discord.utils.format_dt(dt, style='F')} {record.message}", width=1990)
        if record.name == "discord.gateway":
            username = "Gateway"
            avatar_url = "https://klappstuhl.me/gallery/raw/xNZqq.png"
        else:
            username = f"{record.name} Logger"
            avatar_url = discord.utils.MISSING

        await self.bot.stats_webhook.send(msg, username=username, avatar_url=avatar_url)

    @command(hidden=True, description="Shows the current log level.")
    @commands.is_owner()
    async def bothealth(self, ctx: Context) -> None:
        """Various bot health monitoring tools."""

        LEVEL_COLOURS = {
            HealthLevel.HEALTHY: helpers.Colour.lime_green(),
            HealthLevel.WARNING: helpers.Colour.energy_yellow(),
            HealthLevel.UNHEALTHY: helpers.Colour.darker_red(),
        }

        embed = discord.Embed(title="Bot Health Report")

        # Gather the raw runtime observations; the analysis is delegated to the service.
        db = self.bot.db._internal_pool
        total_waiting = len(db._queue._getters)  # type: ignore[union-attr]
        current_generation = db._generation
        connections = [
            ConnectionState(
                generation=holder._generation,
                in_use=holder._in_use is not None,
                is_closed=holder._con is None or holder._con.is_closed(),
            )
            for holder in db._holders
        ]

        being_spammed = self.bot.spam_control.current_spammers
        command_waiters = len(self._command_data_batch)
        global_rate_limit = not self.bot.http._global_over.is_set()

        all_tasks = asyncio.all_tasks(loop=self.bot.loop)
        event_tasks = [t for t in all_tasks if "Client._run_event" in repr(t) and not t.done()]
        cogs_directory = str(Path(__file__).parent)
        tasks_directory = str(Path("discord", "ext", "tasks", "__init__.py"))
        inner_tasks = [t for t in all_tasks if cogs_directory in repr(t) or tasks_directory in repr(t)]
        bad_inner_tasks = ", ".join(hex(id(t)) for t in inner_tasks if t.done() and t._exception is not None)

        report = assess_bot_health(
            connections,
            current_generation=current_generation,
            is_being_spammed=bool(being_spammed),
            command_waiters=command_waiters,
            has_failed_inner_tasks=bool(bad_inner_tasks),
            global_rate_limit=global_rate_limit,
        )

        description = [
            f"Total `Pool.acquire` Waiters: {total_waiting}",
            f"Current Pool Generation: {current_generation}",
            f"Connections In Use: {len(db._holders) - db._queue.qsize()}",  # type: ignore[union-attr]
            f"Current Spammers: {', '.join(str(being_spammed)) if being_spammed else 'None'}",
            f"Questionable Connections: {report.questionable_connections}",
            f"Commands Waiting: {command_waiters}",
            f"Avatars Waiting: {len(self._avatar_data_batch)}",
            f"Global Rate Limit: {global_rate_limit}",
        ]

        connection_value = "\n".join(
            f"<Holder i={index} gen={c.generation} in_use={c.in_use} closed={c.is_closed}>"
            for index, c in enumerate(connections, start=1)
        )
        embed.add_field(name="Connections", value=f"```py\n{connection_value}\n```", inline=False)
        embed.add_field(name="Inner Tasks", value=f"Total: {len(inner_tasks)}\nFailed: {bad_inner_tasks or 'None'}")
        embed.add_field(name="Events Waiting", value=f"Total: {len(event_tasks)}", inline=False)

        memory_usage = self.process.memory_full_info().uss / 1024**2
        cpu_usage = self.process.cpu_percent() / (psutil.cpu_count() or 1)
        embed.add_field(name="Process", value=f"{memory_usage:.2f} MiB\n{cpu_usage:.2f}% CPU", inline=False)

        embed.colour = LEVEL_COLOURS[report.level]
        embed.set_footer(text=f"{report.warnings} warning(s)")
        embed.description = "\n".join(description)
        await ctx.send(embed=embed)

    @command(hidden=True, description="Shows the current gateway traffic interaction with the bot.")
    @commands.is_owner()
    async def gateway(self, ctx: Context) -> None:
        """Gateway related stats."""
        yesterday = discord.utils.utcnow() - datetime.timedelta(days=1)
        colour = helpers.Colour.white()

        traffic = summarize_gateway_traffic(self.bot.identifies, self.bot.resumes, since=yesterday)

        builder = [
            f"Total RESUME(s): `{traffic.total_resumes}`",
            f"Total IDENTIFY(s): `{traffic.total_identifies}`",
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

        embed = discord.Embed(colour=colour, title="Gateway (last 24 hours)")
        embed.description = "\n".join(builder)
        embed.set_footer(text="None warnings")
        await ctx.send(embed=embed)

    @staticmethod
    async def send_records_table(ctx: Context, records: list[asyncpg.Record]) -> None:
        """Renders a list of records as a plaintext table and sends it as a file."""
        if len(records) == 0:
            await ctx.send_error("No results found.")
            return

        headers = list(records[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        rendered = table.render()

        fp = io.BytesIO(rendered.strip().encode("utf-8"))
        await ctx.send("Too many results...", file=discord.File(fp, "results.sql"))

    @group(
        "command",
        invoke_without_command=True,
        hidden=True,
        description="Shows the current command usage statistics.",
    )
    async def _cmd(self, ctx: Context) -> None:
        """Shows the current command usage statistics."""
        if await self.bot.is_owner(ctx.author) and ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_cmd.command(name="stats", description="Shows the current command usage statistics.")
    @describe(limit="The number of commands to display.")
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
            title = f"Top `{limit}` Commands"
        else:
            common = counter.most_common()[limit:]
            title = f"Bottom `{limit}` Commands"

        image = await self.bot.render.bar_chart(
            dict(sorted(dict(common).items(), key=lambda item: item[1], reverse=True)),
            f"{total} total commands used ({slash_commands} slash command uses) ({cpm:.2f}/minute)",
            merge=True,
        )
        await ctx.send(f"## {title}")
        await FilePaginator.start(ctx, entries=[image], per_page=1)  # type: ignore[arg-type]

    @_cmd.group(
        name="history",
        hidden=True,
        invoke_without_command=True,
        description="Command history related commands.",
    )
    @describe(limit="The limit of records to show.")
    @commands.is_owner()
    async def command_history(self, ctx: Context, limit: int = 15) -> None:
        """Command history."""
        async with ctx.channel.typing():
            records = await self.bot.db.stats.get_recent_command_history(limit)
            await self.send_records_table(ctx, records)

    @command_history.command(
        name="for",
        hidden=True,
        description="Command history for a command.",
    )
    @describe(days="The amount of days to look back.", command="The command to look for.")
    @commands.is_owner()
    async def command_history_for(self, ctx: Context, days: int = 7, *, command: str) -> None:
        """Command history for a command."""
        async with ctx.channel.typing():
            records = await self.bot.db.stats.get_command_history_for(command, datetime.timedelta(days=days))
            await self.send_records_table(ctx, records)

    @command_history.command(
        name="guild",
        hidden=True,
        aliases=["server"],
        description="Command history for a guild.",
    )
    @describe(guild_id="The guild to show the command history for.")
    @commands.is_owner()
    async def command_history_guild(self, ctx: Context, guild_id: int) -> None:
        """Command history for a guild."""
        async with ctx.channel.typing():
            records = await self.bot.db.stats.get_command_history_guild(guild_id)
            await self.send_records_table(ctx, records)

    @command_history.command(
        name="user",
        hidden=True,
        aliases=["member"],
        description="Command history for a user.",
    )
    @describe(user_id="The user to show the command history for.")
    @commands.is_owner()
    async def command_history_user(self, ctx: Context, user_id: int) -> None:
        """Command history for a user."""
        async with ctx.channel.typing():
            records = await self.bot.db.stats.get_command_history_user(user_id)
            await self.send_records_table(ctx, records)

    @command_history.command(
        name="log",
        hidden=True,
        description="Command history log for the last N days.",
    )
    @describe(days="The amount of days to look back.")
    @commands.is_owner()
    async def command_history_log(self, ctx: Context, days: int = 7) -> None:
        """Command history log for the last N days."""
        async with ctx.channel.typing():
            all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}
            records = await self.get_commands_stats(days=days) or []
            for record in records:
                if record["command"] in all_commands:
                    all_commands[record["command"]] = record["uses"]

            as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
            table = TabularData()
            table.set_columns(["Command", "uses"])
            table.add_rows(tup for tup in as_data)
            rendered = table.render()

            embed = discord.Embed(title="Summary", colour=discord.Colour.green())
            embed.set_footer(text="Since").timestamp = discord.utils.utcnow() - datetime.timedelta(days=days)

            top_ten = "\n".join(f"{record['command']}: {record['uses']}" for record in records[:10])
            bottom_ten = "\n".join(f"{record['command']}: {record['uses']}" for record in records[-10:])
            embed.add_field(name="Top 10", value=top_ten)
            embed.add_field(name="Bottom 10", value=bottom_ten)

            unused = ", ".join(name for name, uses in as_data if uses == 0)
            if len(unused) > 1024:
                unused = "Way too many..."

            embed.add_field(name="Unused", value=unused, inline=False)

            await ctx.send(embed=embed, file=discord.File(io.BytesIO(rendered.encode()), filename="full_results.accesslog"))

    @command_history.command(name="cog", hidden=True, description="Command history for a cog or grouped by a cog.")
    @describe(days="The amount of days to look back.", cog_name="The cog to show the command history for.")
    @commands.is_owner()
    async def command_history_cog(self, ctx: Context, days: int = 7, *, cog_name: str | None = None) -> None:
        """Command history for a cog or grouped by a cog."""
        async with ctx.channel.typing():
            interval = datetime.timedelta(days=days)
            if cog_name is not None:
                cog = self.bot.get_cog(cog_name)
                if cog is None:
                    await ctx.send_error(f"Unknown Cog: {cog_name}")
                    return

                records = await self.bot.db.stats.get_command_history_by_cog(
                    [c.qualified_name for c in cog.walk_commands()], interval
                )
                return await self.send_records_table(ctx, records)

            data = defaultdict(CommandUsageCount)
            records = await self.bot.db.stats.get_command_history_grouped(interval)
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

    bot.log_handler = handler = LoggingHandler(cog)
    logging.getLogger().addHandler(handler)


async def teardown(bot: Bot) -> None:
    logging.getLogger().removeHandler(bot.log_handler)
    del bot.log_handler
