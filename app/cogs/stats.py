from __future__ import annotations

import asyncio
import datetime
import io
import logging
import math
import subprocess
import textwrap
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict

import aiohttp
import asyncpg
import discord
import psutil
import wavelink
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks
from discord.utils import MISSING
from expiringdict import ExpiringDict

import config
from app.cogs.games.models import Game
from app.core import Accent, Bot, Cog, Context, NoticeView, make_notice
from app.core.models import command, cooldown, describe, group
from app.core.views import LayoutView, UserInfoView
from app.rendering import resize_to_limit
from app.services import (
    BurstCollapser,
    ConnectionState,
    DailyNumbers,
    HealthChange,
    HealthLevel,
    HealthTracker,
    LavalinkMetrics,
    StartupFacts,
    assess_bot_health,
    count_code_stats,
    daily_summary_fields,
    describe_health_change,
    feed_is_healthy,
    parse_lavalink_metrics,
    rewrite_gateway_line,
    startup_fields,
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
from app.utils.webhooks import send_webhook
from config import Emojis, beta, get_full_version, path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

#: When the daily summary lands in the log channel.
DAILY_SUMMARY_TIME = datetime.time(hour=8, tzinfo=datetime.UTC)
#: Gateway latency above this (seconds) for HIGH_LATENCY_CHECKS checks in a row is reported.
HIGH_LATENCY = 1.0
HIGH_LATENCY_CHECKS = 3
#: At most one gateway line per this many seconds; the rest are folded into a count.
GATEWAY_WINDOW = 60.0
#: A Lavalink node must stay disconnected this long (seconds) before it is reported; a
#: reconnect within it posts nothing.
LAVALINK_GRACE = 60.0


#: Discord's caps for one Components V2 message.
MAX_COMPONENTS = 40
MAX_TEXT = 4000
#: Text budget for card bodies; the rest is headroom for headings, field names and footers.
_CARD_BUDGET = 3600


def _fit(text: str, limit: int) -> str:
    """Trims ``text`` to ``limit`` characters on a line boundary where possible."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 2)
    return text[: cut if cut > 0 else limit - 2] + "\n…"


def _since(dt: datetime.datetime | None, style: Literal["R", "D", "F"] = "D") -> str:
    """Formats a timestamp column; ``commands.used`` is naive UTC, the history tables are aware."""
    if dt is None:
        return "*never*"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return discord.utils.format_dt(dt, style)


def _ranking(rows: Iterable[tuple[str, int]], *, unit: str = "uses", empty: str = "*Nothing recorded yet.*") -> str:
    """One medal-prefixed line per ``(label, count)``; ranks start at 1 so first place gets gold."""
    return "\n".join(
        f"{medal_emoji(rank)} {label} (`{count}` {unit})" for rank, (label, count) in enumerate(rows, start=1)
    ) or empty


def stats_card(
    title: str,
    description: str | None = None,
    *,
    fields: Sequence[tuple[str, str]] = (),
    footer: str | None = None,
    accent: Accent | discord.Colour | int = Accent.brand,
    thumbnail: str | None = None,
) -> NoticeView:
    """:func:`make_notice` with field values trimmed so the card always fits Discord's text cap."""
    description = _fit(description, _CARD_BUDGET // 3) if description else None
    remaining = _CARD_BUDGET - len(description or "")
    fitted = []
    for index, (name, value) in enumerate(fields):
        # Fair share of what is left, so a short field hands its headroom to the next one.
        value = _fit(value, remaining // (len(fields) - index))
        remaining -= len(value)
        fitted.append((name, value))
    return make_notice(title, description, accent=accent, thumbnail=thumbnail or None, fields=fitted, footer=footer)


def chart_card(
    title: str,
    description: str | None,
    filename: str,
    *,
    footer: str | None = None,
    accent: Accent | discord.Colour | int = Accent.brand,
) -> NoticeView:
    """A card around a rendered image; send it with ``file=`` carrying ``filename``."""
    colour = accent.colour if isinstance(accent, Accent) else accent
    container = discord.ui.Container(accent_colour=colour)
    container.add_item(discord.ui.TextDisplay(f"## {title}\n{description}" if description else f"## {title}"))
    container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}")))
    if footer:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
    return NoticeView(container)


def failure_ranking(
    rows: Iterable[tuple[str, int, int]], *, min_uses: int = 5
) -> list[tuple[str, int, int, float]]:
    """``(command, total, failed, rate%)`` for commands with failures, worst rate first."""
    ranked = [(name, total, bad, bad / total * 100) for name, total, bad in rows if total >= min_uses and bad]
    return sorted(ranked, key=lambda r: (-r[3], -r[2]))


def activity_grid(rows: Iterable[Mapping[str, Any]]) -> list[list[int]]:
    """Folds ``(dow, hour, count)`` rows (dow 0 = Sunday) into ``grid[weekday][hour]``, Monday first."""
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        grid[(r["dow"] - 1) % 7][r["hour"]] += r["count"]
    return grid


def hour_totals(rows: Iterable[Mapping[str, Any]]) -> list[int]:
    """Commands per hour of day (24 buckets) from ``(dow, hour, count)`` rows."""
    totals = [0] * 24
    for r in rows:
        totals[r["hour"]] += r["count"]
    return totals


def daily_series(rows: Iterable[Mapping[str, Any]], days: int) -> tuple[list[str], list[int]]:
    """Zero-filled ``days`` daily buckets ending today (naive UTC buckets from ``date_trunc``)."""
    by_day = {r["bucket"].date(): r["value"] for r in rows}
    today = datetime.datetime.now(datetime.UTC).date()
    day_list = [today - datetime.timedelta(days=i) for i in range(days - 1, -1, -1)]
    return [d.strftime("%d %b") for d in day_list], [by_day.get(d, 0) for d in day_list]


def _streak(current: int, *, fire: str = "🔥", snowflake: str = "❄") -> str:
    if current > 0:
        return f"{fire} {current}W"
    if current < 0:
        return f"{snowflake} {abs(current)}L"
    return "—"


def _winrate(won: int, played: int) -> float:
    return (won / played * 100) if played else 0.0


def _icon(game: Game) -> str:
    """``game.icon``, tolerating games added to the enum without a badge (``Game.icon`` raises)."""
    try:
        return game.icon
    except KeyError:
        return "🎮"


def game_stats_card(
    name: str,
    display_name: str,
    totals: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    cash: str = "💵",
    fire: str = "🔥",
    snowflake: str = "❄",
) -> NoticeView:
    """A member's game record: totals up top, one compact block per game (most played first).

    Every game shares one text block, so the card stays a handful of components however many
    games exist; the block is trimmed to the text cap instead.
    """
    rate = _winrate(totals["won"], totals["played"])
    summary = (
        f"**{totals['played']}** rounds • **{totals['won']}**W / **{totals['lost']}**L ({rate:.0f}% win-rate)\n"
        f"Net profit: {cash} **{fnumb(totals['profit'])}** • Biggest win: {cash} **{fnumb(totals['biggest_win'])}**\n"
        f"Streak: {_streak(totals['current_streak'])} • Best: {_streak(totals['best_streak'])}"
    )
    blocks = []
    for record in rows:
        game = Game(record["game"])
        tied = f" / `{record['tied']}`T" if record["tied"] else ""
        blocks.append(
            f"**{_icon(game)} {game.label}** — `{record['played']}` played • `{record['won']}`W / `{record['lost']}`L"
            f"{tied} • **{_winrate(record['won'], record['played']):.0f}%**\n"
            f"-# Net {cash} {fnumb(record['profit'])} • Streak "
            f"{_streak(record['current_streak'], fire=fire, snowflake=snowflake)} (best {record['best_streak']}W)"
        )
    return stats_card(
        f"{name}'s Game Stats",
        f"-# {display_name} has played **{totals['played']}** games in this server.",
        fields=[("Overall", summary), ("Per game", "\n".join(blocks) or "*No games yet.*")],
        footer="Want to play? See ?help Games",
    )


def _make_presence_key(member: discord.Member) -> str:
    return f"status:{member.id}:{member.status}"


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
        # Only discord.py's connection lines, and those are rewritten before they are posted:
        # a raw line would carry the gateway session id.
        return record.name in ("discord.gateway", "discord.client")

    def emit(self, record: logging.LogRecord) -> None:
        # The heartbeat thread logs too, and an asyncio.Queue is not thread-safe.
        try:
            self.cog.bot.loop.call_soon_threadsafe(self.cog.add_record, record)
        except RuntimeError:  # the loop is already closed during shutdown
            pass


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

        self.__LOOPS: list[Any] = [
            self.cleanup_presence_history,
            self.command_insert,
            self.avatar_insert,
            self.health_check,
            self.daily_summary,
        ]

        # What the private log channel posts: see app/services/log_channel.py.
        self._gateway_burst = BurstCollapser(window=GATEWAY_WINDOW)
        self._disconnected_at: float | None = None
        self._health = HealthTracker()
        self._lavalink_pending: dict[str, asyncio.Task] = {}
        self._latency_samples: list[float] = []
        self._guilds_joined = 0
        self._guilds_left = 0
        self._startup_card_task: asyncio.Task | None = None
        self.process.cpu_percent()  # prime it: the first call always reads 0.0

        # if we're on our beta instance, we don't want to start those tasks
        if not beta:
            for _task in self.__LOOPS:
                _task.add_exception_type(asyncpg.PostgresConnectionError)
                _task.start()

            self.__loging_worker_task = self.bot.loop.create_task(self.logging_worker())
            self._startup_card_task = self.bot.loop.create_task(self.send_startup_card())

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
        if self._startup_card_task:
            self._startup_card_task.cancel()
        for pending in self._lavalink_pending.values():
            pending.cancel()

    # LOG CHANNEL

    async def _post(self, content: str | None = None, *, embed: discord.Embed | None = None, **kwargs: Any) -> None:
        """Sends to the log channel; never raises, never on beta."""
        if beta:
            return
        try:
            await send_webhook(self.bot.stats_webhook, content or MISSING, embed=embed or MISSING, **kwargs)
        except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as exc:
            log.warning("Could not post to the log channel: %s", exc)

    async def report_health(self, change: HealthChange | None) -> None:
        if change is None:
            return
        emoji = Emojis.success if change.ok else Emojis.error
        await self._post(f"{emoji} {describe_health_change(change)}", username="Health")

    async def send_startup_card(self) -> None:
        """|coro|

        Posts the startup card once per process, after the first READY (never on a RESUME
        or a cog reload).
        """
        await self.bot.wait_until_ready()
        await self.bot.wait_until_setup_finished()
        if self.bot.startup_card_sent:
            return
        self.bot.startup_card_sent = True

        now = time.monotonic()
        nodes = list(wavelink.Pool.nodes.values())
        lavalink = any(node.status is wavelink.NodeStatus.CONNECTED for node in nodes)
        for node in nodes:
            self._health.observe(
                f"lavalink:{node.identifier}", node.status is wavelink.NodeStatus.CONNECTED, now=now)

        ai: bool | None = None
        if self.bot.ai.enabled:
            ai = (await self.bot.ai.health(max_age=300)).reachable
            self._health.observe("ai", ai, now=now)

        schema_version: int | None = None
        pending_timers: int | None = None
        try:
            schema_version = await self.bot.db.stats.get_schema_version()
            pending_timers = await self.bot.db.timers.count_pending()
            db_ok = True
        except Exception as exc:
            log.warning("Startup card: database check failed: %s", exc)
            db_ok = False
        self._health.observe("database", db_ok, now=now)

        facts = StartupFacts(
            version=get_full_version(),
            guilds=len(self.bot.guilds),
            members=sum(guild.member_count or 0 for guild in self.bot.guilds),
            shards=self.bot.shard_count or 1,
            latency=self.bot.latency,
            startup_seconds=self.bot.startup_timestamp.timestamp() - self.process.create_time(),
            lavalink=lavalink,
            ai=ai,
            db_ok=db_ok,
            schema_version=schema_version,
            pending_timers=pending_timers,
        )
        embed = discord.Embed(
            title=f"{Emojis.success} Percy is online",
            colour=helpers.Colour.lime_green(),
            timestamp=self.bot.startup_timestamp,
        )
        for name, value in startup_fields(facts):
            embed.add_field(name=name, value=value)
        await self._post(embed=embed)

    @tasks.loop(seconds=60.0)
    async def health_check(self) -> None:
        """|coro|

        Posts when a component goes down or comes back. Lavalink is reported from its own
        events; this covers gateway latency, the database, the AI engine and the feeds.
        """
        try:
            await self._check_health()
        except Exception:
            # A loop that raises stops for good.
            log.exception("Log channel health check failed.")

    async def _check_health(self) -> None:
        now = time.monotonic()

        latency = self.bot.latency
        if math.isfinite(latency):
            self._latency_samples.append(latency)
            await self.report_health(self._health.observe(
                "latency", latency < HIGH_LATENCY, now=now, label="Gateway latency",
                detail=f"{latency * 1000:.0f} ms for the last {HIGH_LATENCY_CHECKS} minutes",
                confirm=HIGH_LATENCY_CHECKS, problem="high",
            ))

        pool = self.bot.db.pool_stats()
        if pool.waiting and pool.in_use >= pool.max_size:
            db_ok, detail = False, f"connection pool exhausted ({pool.waiting} waiting for {pool.max_size})"
        else:
            try:
                await asyncio.wait_for(self.bot.db.stats.get_schema_version(), timeout=10)
                db_ok, detail = True, None
            except Exception as exc:
                db_ok, detail = False, f"not reachable ({type(exc).__name__})"
        await self.report_health(self._health.observe(
            "database", db_ok, now=now, label="Database", detail=detail, confirm=2))

        if self.bot.ai.enabled:
            report = await self.bot.ai.health(max_age=55)
            await self.report_health(self._health.observe(
                "ai", report.reachable, now=now, label="AI engine", detail="not reachable", confirm=2))

        try:
            sources = await self.bot.db.news.list_sources()
        except asyncpg.UndefinedTableError:  # V43 not applied yet
            sources = []
        for source in sources:
            status = source["last_status"]
            if status is None:
                continue
            kind = "Comic feed" if source["media"] == "comic" else "News feed"
            await self.report_health(self._health.observe(
                f"feed:{source['slug']}", feed_is_healthy(status), now=now,
                label=f"{kind} {source['name']}", detail=f"last poll returned `{status[:80]}`",
            ))

    @health_check.before_loop
    async def _before_health_check(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=DAILY_SUMMARY_TIME)
    async def daily_summary(self) -> None:
        """|coro|

        Posts the day's aggregates: guild changes, commands, mod cases, uptime, latency and
        process load. Counts only, never who.
        """
        try:
            await self._send_daily_summary()
        except Exception:
            log.exception("Daily summary failed.")

    async def _send_daily_summary(self) -> None:
        now = discord.utils.utcnow()
        day_ago = now - datetime.timedelta(days=1)

        status_counts = await self.bot.db.stats.get_daily_status_counts()
        top = await self.bot.db.stats.get_command_usage(days=1, limit=5)
        cases = await self.bot.db.cases.count_created_since(day_ago)

        numbers = DailyNumbers(
            guilds_joined=self._guilds_joined,
            guilds_left=self._guilds_left,
            guilds_total=len(self.bot.guilds),
            guilds_full_day=self.bot.startup_timestamp <= day_ago,
            commands_run=sum(count for _, count in status_counts),
            command_errors=sum(count for failed, count in status_counts if failed is True),
            top_commands=[(record["command"], record["uses"]) for record in top],
            mod_cases=cases,
            uptime_seconds=(now - self.bot.startup_timestamp).total_seconds(),
            latency_samples=self._latency_samples,
            memory_mib=self.process.memory_full_info().uss / 1024**2,
            cpu_percent=self.process.cpu_percent() / (psutil.cpu_count() or 1),
        )
        embed = discord.Embed(title=f"{Emojis.info} Daily summary", colour=helpers.Colour.white(), timestamp=now)
        for name, value in daily_summary_fields(numbers):
            embed.add_field(name=name, value=value)
        embed.set_footer(text="Last 24 hours")
        await self._post(embed=embed)

        self._guilds_joined = self._guilds_left = 0
        self._latency_samples.clear()

    @daily_summary.before_loop
    async def _before_daily_summary(self) -> None:
        await self.bot.wait_until_ready()

    @Cog.listener()
    async def on_disconnect(self) -> None:
        # Wall clock, to compare against LogRecord.created for "Reconnected after 12 s".
        self._disconnected_at = time.time()

    @Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        pending = self._lavalink_pending.pop(payload.node.identifier, None)
        if pending:
            pending.cancel()
        await self._observe_lavalink(payload.node, True)

    @Cog.listener()
    async def on_wavelink_node_disconnected(self, payload: wavelink.NodeDisconnectedEventPayload) -> None:
        node = payload.node
        if node.identifier not in self._lavalink_pending:
            self._lavalink_pending[node.identifier] = asyncio.create_task(self._report_lavalink_down(node))

    async def _report_lavalink_down(self, node: wavelink.Node) -> None:
        await asyncio.sleep(LAVALINK_GRACE)
        self._lavalink_pending.pop(node.identifier, None)
        if node.status is not wavelink.NodeStatus.CONNECTED:
            await self._observe_lavalink(node, False)

    async def _observe_lavalink(self, node: wavelink.Node, ok: bool) -> None:
        await self.report_health(self._health.observe(
            f"lavalink:{node.identifier}", ok, now=time.monotonic(), label="Lavalink",
            detail="node disconnected, music is unavailable",
        ))

    # LOGGING

    async def logging_worker(self) -> None:
        """|coro|

        A task that sends log records to the stats webhook.

        This task is automatically started after the cog is loaded.
        """
        try:
            while True:
                try:
                    record = await asyncio.wait_for(self._logging_queue.get(), timeout=5.0)
                except TimeoutError:
                    if line := self._gateway_burst.flush(time.monotonic()):
                        await self._send_gateway_line(line, time.time())
                    continue
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

        # Log which command ran, never the text it was invoked with: arguments are message
        # content, and message content is not written anywhere outside Discord.
        if ctx.is_interaction and ctx.interaction is not None and ctx.interaction.command:
            invoked = f"/{ctx.interaction.command.qualified_name}"
        else:
            invoked = f"{ctx.prefix or ''}{cmd_name}"

        log.info("%s: %s in %s: %s", ctx.now.replace(tzinfo=None), message.author, destination, invoked)
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
        self._guilds_joined += 1
        embed = discord.Embed(colour=helpers.Colour.lime_green(), title="New Guild")
        await self.send_guild_stats(embed, guild)

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

        if not (await self.bot.db.get_user_config(member.id)).track_history:  # type: ignore[misc]
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

        if not (await self.bot.db.get_user_config(after.id)).track_history:  # type: ignore[misc]
            return None

        if before.display_avatar != after.display_avatar:
            avatar: bytes | None = await self._read_avatar(after)
            if avatar is None:
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
        if after.bot:
            return None

        if not (await self.bot.db.get_user_config(after.id)).track_history:  # type: ignore[misc]
            return None

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
        self._guilds_left += 1
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
            return

        if self.bot.feature_flags.is_cog_disabled("Stats"):
            return

        if not (await self.bot.db.get_user_config(after.id)).track_presence:  # type: ignore[misc]
            return

        if before.status == after.status:
            return

        if self._presence_cache.get(_make_presence_key(after)):
            return
        self._presence_cache[_make_presence_key(after)] = True

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
            if exc.status in (403, 404, 415):
                return None
            if exc.status >= 500:
                await asyncio.sleep(15.0)
                return await self._read_avatar(member)
            log.exception(
                "Unhandled Discord HTTPException while getting avatar for %s (%s)",
                member.name,
                member.id,
            )
            return None
        return avatar

    def get_bot_uptime(self, *, brief: bool = False) -> str:
        return human_timedelta(self.bot.startup_timestamp, accuracy=None, brief=brief, suffix=False)

    @staticmethod
    def _format_commit(line: str) -> str:
        sha, timestamp, short = line.split("\x1f", 2)
        commit_time = datetime.datetime.fromtimestamp(int(timestamp), datetime.UTC)
        offset = discord.utils.format_dt(commit_time, "R")
        return f"[`{sha[:6]}`]({config.repo_url}commit/{sha}) {short} ({offset})"

    def get_last_commits(self, count: int = 4, repo_path: str = str(path)) -> str:
        out = subprocess.run(
            ["git", "log", f"-{count}", "--topo-order", "--format=%H%x1f%ct%x1f%s"],
            cwd=repo_path, capture_output=True, text=True, encoding="utf-8", timeout=5, check=True,
        ).stdout
        return "\n".join(self._format_commit(line) for line in out.splitlines())

    @executor
    def project_stats_counter(self) -> str:
        root = Path(__file__).parent.parent
        stats = count_code_stats(root, ignored=[root / "venv"])

        builder = AnsiStringBuilder()
        builder.append("Files:       ", color=AnsiColor.white, bold=True)
        builder.append(str(stats.files), color=AnsiColor.green).newline()
        builder.append("Classes:     ", color=AnsiColor.white, bold=True)
        builder.append(str(stats.classes), color=AnsiColor.green).newline()
        builder.append("Functions:   ", color=AnsiColor.white, bold=True)
        builder.append(str(stats.functions), color=AnsiColor.green).newline()
        builder.append("Comments:    ", color=AnsiColor.white, bold=True)
        builder.append(str(stats.comments), color=AnsiColor.green).newline()
        builder.append("Lines:       ", color=AnsiColor.white, bold=True)
        builder.append(str(stats.lines), color=AnsiColor.green).newline()
        builder.append("Characters:  ", color=AnsiColor.white, bold=True)
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
        total = sum(self.bot.socket_stats.values())
        cpm = total / self._minutes_up()
        image = await self.bot.render.bar_chart(
            dict(sorted(self.bot.socket_stats.items(), key=lambda item: item[1], reverse=True)),
            f"{total} socket events observed ({cpm:.2f}/minute)",
            merge=True,
        )
        assert isinstance(image, discord.File)
        view = chart_card(
            "Socket Events", f"`{total}` events since {_since(self.bot.startup_timestamp, 'R')}", image.filename
        )
        await ctx.send(view=view, file=image)

    def _minutes_up(self) -> float:
        """Minutes since startup, floored so a just-booted bot never divides by zero."""
        return max((discord.utils.utcnow() - self.bot.startup_timestamp).total_seconds() / 60, 1.0)

    @command(description="Tells you how long the bot has been up for.")
    async def uptime(self, ctx: Context) -> None:
        """Tells you how long the bot has been up for."""
        await self.bot.wait_until_ready()
        view = stats_card(
            "Uptime",
            f"**{self.get_bot_uptime()}**",
            footer=f"Online since {_since(self.bot.startup_timestamp, 'F')}",
        )
        await ctx.send(view=view)

    @command(description="Tells you information about the bot itself.")
    async def about(self, ctx: Context) -> None:
        """Tells you information about the bot itself."""
        await ctx.typing()

        try:
            revision = self.get_last_commits()
        except (OSError, subprocess.SubprocessError):
            revision = "*Not available.*"

        assert ctx.bot.user is not None
        oauth_url = discord.utils.oauth_url(
            client_id=ctx.bot.user.id,
            permissions=discord.Permissions(8),
            scopes=("bot", "applications.commands"),
        )
        assert isinstance(config.owners, int)
        owner = ctx.bot.get_user(config.owners)

        container = discord.ui.Container(accent_colour=Accent.brand.colour)

        container.add_item(
            discord.ui.Section(
                f"## {ctx.bot.user}\n"
                f"-# Version: `{get_full_version()}`\n"
                f"### Latest Commits:\n{revision}",
                accessory=discord.ui.Thumbnail(get_asset_url(self.bot.user) if self.bot.user else None)
            )
        )
        container.add_item(discord.ui.Separator())

        total_members = 0
        total_unique = len(self.bot.users)
        bots = sum(user.bot for user in self.bot.users)

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

        container.add_item(
            discord.ui.TextDisplay(
                f"### Stats\n"
                f"Members: `{total_members}` total | `{total_unique}` unique | "
                f"`{bots / max(total_unique, 1):.2%}` bots\n"
                f"Channels: `{text + voice}` total | `{text}` text | `{voice}` voice\n"
                f"Guilds: `{guilds}`\n"
                f"Commands run since last reboot: `{sum(self.bot.command_stats.values())}`"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                f"### System Stats\n"
                f"Uptime: `{self.get_bot_uptime(brief=True)}`\n"
                f"Memory Usage: `{psutil.virtual_memory().percent:.2f}%`\n"
                f"CPU Usage: `{psutil.cpu_percent():.2f}%`\n"
                f"Disk Usage: `{psutil.disk_usage(str(Path(__file__).parent.parent)).percent:.2f}%`"
            )
        )
        container.add_item(discord.ui.Separator())

        file_stats = await self.project_stats_counter()
        container.add_item(
            discord.ui.TextDisplay(
                f"## File Stats\n"
                f"```ansi\n{file_stats}```"
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.TextDisplay(
                f"-# made by **{owner}** with **discord.py v{discord.__version__}**"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.ActionRow(
                discord.ui.Button(label="Invite", url=oauth_url, style=discord.ButtonStyle.link),
                discord.ui.Button(label='Support', url=config.support_server, style=discord.ButtonStyle.link),
                discord.ui.Button(label='Source', url=config.repo_url, style=discord.ButtonStyle.link),
                discord.ui.Button(label='Dashboard', url=config.website, style=discord.ButtonStyle.link)
            )
        )

        view = LayoutView()
        view.add_item(container)
        await ctx.send(view=view)

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
        async with ctx.progress("Fetching command statistics..."):
            stats = ctx.db.stats
            guild_id = ctx.guild.id
            if member is None:
                count, top, top_today, users, users_today = await asyncio.gather(
                    stats.get_command_summary(guild_id),
                    self.get_commands_stats(guild_id),
                    self.get_commands_stats(guild_id, days=1),
                    self.get_commands_stats(guild_id, group_by="author_id"),
                    self.get_commands_stats(guild_id, group_by="author_id", days=1),
                )
                view = stats_card(
                    "Server Command Stats",
                    f"Total of `{count[0]}` commands used.",
                    fields=[
                        ("Top Commands", _ranking((r["command"], r["uses"]) for r in top or [])),
                        ("Top Commands Today", _ranking((r["command"], r["uses"]) for r in top_today or [])),
                        ("Top Command Users", _ranking((f"<@{r['author_id']}>", r["uses"]) for r in users or [])),
                        (
                            "Top Command Users Today",
                            _ranking((f"<@{r['author_id']}>", r["uses"]) for r in users_today or []),
                        ),
                    ],
                    footer=f"Tracking command usage since {_since(count[1])}",
                    thumbnail=get_asset_url(ctx.guild),
                )
            else:
                count, most_used, most_used_today = await asyncio.gather(
                    stats.get_command_summary(guild_id, member.id),
                    self.get_commands_stats(guild_id, member.id),
                    self.get_commands_stats(guild_id, member.id, days=1),
                )
                view = stats_card(
                    f"Command Stats for {member.display_name}",
                    f"Total of `{count[0]}` commands used.",
                    fields=[
                        ("Most Used Commands", _ranking((r["command"], r["uses"]) for r in most_used or [])),
                        ("Most Used Today", _ranking((r["command"], r["uses"]) for r in most_used_today or [])),
                    ],
                    footer=f"First command used {_since(count[1])}",
                    accent=member.colour if member.colour.value else Accent.brand,
                    thumbnail=get_asset_url(member),
                )

        await ctx.send(view=view)

    @stats.command(
        name="global",
        description="Global all time command statistics.",
    )
    async def stats_global(self, ctx: Context) -> None:
        """Global all time command statistics."""
        await ctx.typing()
        total: int = await ctx.db.stats.count_all_commands()
        view = stats_card("Global Command Stats", f"`{total}` commands used.", fields=await self._global_fields())
        await ctx.send(view=view)

    async def _global_fields(self, *, days: int | None = None) -> list[tuple[str, str]]:
        """Top commands, guilds and users bot-wide, optionally over the trailing ``days``."""
        top_commands, top_guilds, top_users = await asyncio.gather(
            self.get_commands_stats(days=days),
            self.get_commands_stats(group_by="guild_id", days=days),
            self.get_commands_stats(group_by="author_id", days=days),
        )

        def guild_label(guild_id: int | None) -> str:
            if guild_id is None:
                return "Private Message"
            return censor_object(self.bot.blacklist, self.bot.get_guild(guild_id) or f"<Unknown {guild_id}>")

        def user_label(user_id: int) -> str:
            return censor_object(self.bot.blacklist, self.bot.get_user(user_id) or f"<Unknown {user_id}>")

        return [
            ("Top Commands", _ranking((r["command"], r["uses"]) for r in top_commands or [])),
            ("Top Guilds", _ranking((guild_label(r["guild_id"]), r["uses"]) for r in top_guilds or [])),
            ("Top Users", _ranking((user_label(r["author_id"]), r["uses"]) for r in top_users or [])),
        ]

    @stats.command(
        name="today",
        description="Global command statistics for the day.",
    )
    async def stats_today(self, ctx: Context) -> None:
        """Global command statistics for the day."""
        await ctx.typing()

        failed, success, unknown = 0, 0, 0
        for state, count in await ctx.db.stats.get_daily_status_counts():
            match state:
                case False:
                    success += count
                case True:
                    failed += count
                case _:
                    unknown += count

        view = stats_card(
            "Last 24 Hour Command Stats",
            f"`{failed + success + unknown}` commands used today "
            f"(`{success}` succeeded, `{failed}` failed, `{unknown}` unknown).",
            fields=await self._global_fields(days=1),
            accent=Accent.error if failed > success else Accent.brand,
        )
        await ctx.send(view=view)

    @stats.command(name="failures", description="Which commands fail most often in this server.")
    @describe(days="How many days back to look (1-90).")
    async def stats_failures(self, ctx: Context, days: int = 30) -> None:
        """Failure-rate ranking: failed vs total invocations per command."""
        assert ctx.guild is not None
        days = max(1, min(days, 90))
        await ctx.typing()
        rows = await ctx.db.stats.get_guild_command_failures(ctx.guild.id, days=days)
        ranked = failure_ranking([(r["command"], r["total"], r["failed"]) for r in rows])
        total = sum(r["total"] for r in rows)
        failed = sum(r["failed"] for r in rows)
        view = stats_card(
            "Command Failure Rates",
            f"`{failed}` of `{total}` invocations failed in the last {days} days.",
            fields=[("Highest failure rate", _ranking(
                ((f"{name} — {rate:.0f}% ({bad}/{count})", bad) for name, count, bad, rate in ranked[:10]),
                unit="failed", empty="*No failed commands. Nice.*",
            ))],
            footer="Commands with fewer than 5 uses are left out.",
            accent=Accent.error if total and failed / total > 0.25 else Accent.brand,
            thumbnail=get_asset_url(ctx.guild),
        )
        await ctx.send(view=view)

    @stats.command(name="heatmap", description="Weekday x hour command activity of a member (UTC).")
    @describe(member="The member to show (defaults to you).", days="How many days back to look (1-365).")
    async def stats_heatmap(self, ctx: Context, member: discord.Member | None = None, days: int = 90) -> None:
        """Per-member activity heatmap."""
        assert ctx.guild is not None
        target = member or ctx.author
        days = max(1, min(days, 365))
        await ctx.typing()
        rows = await ctx.db.stats.get_activity_grid(ctx.guild.id, target.id, days=days)
        if not rows:
            await ctx.send_info(f"No commands recorded for **{target.display_name}** in the last {days} days.")
            return
        file = await self.bot.render.heatmap_chart(activity_grid(rows), f"{target.display_name} - activity (UTC)")
        view = chart_card(
            f"Activity Heatmap for {target.display_name}",
            f"`{sum(r['count'] for r in rows)}` commands over the last {days} days.",
            file.filename, footer="Times are UTC.",
            accent=target.colour if target.colour.value else Accent.brand,
        )
        await ctx.send(view=view, file=file)

    @stats.command(name="activity", description="Command activity of this server over time.")
    @describe(days="How many days back to look (2-90).")
    async def stats_activity(self, ctx: Context, days: int = 30) -> None:
        """Daily command volume chart for the server."""
        assert ctx.guild is not None
        days = max(2, min(days, 90))
        await ctx.typing()
        rows = await ctx.db.stats.get_command_series(ctx.guild.id, days=days, granularity="day")
        labels, values = daily_series(rows, days)
        if not any(values):
            await ctx.send_info(f"No commands recorded in the last {days} days.")
            return
        file = await self.bot.render.series_chart(labels=labels, values=values, title=f"Commands per day - {ctx.guild.name}")
        view = chart_card(
            "Server Activity", f"`{sum(values)}` commands in the last {days} days (peak `{max(values)}`/day).",
            file.filename, footer="Days are UTC.",
        )
        await ctx.send(view=view, file=file)

    @stats.command(name="hours", description="Command usage by hour of day (UTC).")
    @describe(days="How many days back to look (1-90).")
    async def stats_hours(self, ctx: Context, days: int = 30) -> None:
        """Command usage by hour of day."""
        assert ctx.guild is not None
        days = max(1, min(days, 90))
        await ctx.typing()
        rows = await ctx.db.stats.get_activity_grid(ctx.guild.id, None, days=days)
        if not rows:
            await ctx.send_info(f"No commands recorded in the last {days} days.")
            return
        values = hour_totals(rows)
        file = await self.bot.render.series_chart(
            labels=[f"{h:02d}" for h in range(24)], values=values, title="Commands by hour of day (UTC)", kind="bar",
            filename="hours.png",
        )
        view = chart_card(
            "Usage by Hour", f"Busiest hour: `{values.index(max(values)):02d}:00` UTC over the last {days} days.",
            file.filename,
        )
        await ctx.send(view=view, file=file)

    async def game_autocomplete(self, _: discord.Interaction, current: str) -> list[Choice[str | int | float]]:
        """Autocomplete over the tracked games."""
        return [
            Choice(name=f"{_icon(game)} {game.label}", value=game.value)
            for game in Game
            if current.lower() in game.label.lower()
        ][:25]

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

        rows, totals = await asyncio.gather(
            ctx.db.game_stats.get_member_games(ctx.guild.id, target.id),
            ctx.db.game_stats.get_member_totals(ctx.guild.id, target.id),
        )
        if not rows or totals is None:
            who = "You have" if target == ctx.author else f"**{target.display_name}** has"
            await ctx.send_info(f"{who} not played any tracked games yet.")
            return

        view = game_stats_card(
            str(target), target.display_name, totals, rows,
            cash=Emojis.Economy.cash, fire=Emojis.fire, snowflake=Emojis.snowflake,
        )
        await ctx.send(view=view)

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

        rankings = "\n".join(
            f"{medal_emoji(rank)} <@{record['user_id']}> — **{record['won']}**W / **{record['lost']}**L "
            f"({_winrate(record['won'], record['played']):.0f}%) • {Emojis.Economy.cash} **{fnumb(record['profit'])}**"
            for rank, record in enumerate(rows, start=1)
        )
        fields = [("Rankings", rankings)]
        if not selected:
            overview = await ctx.db.game_stats.get_guild_overview(ctx.guild.id)
            if overview:
                popular = "\n".join(
                    f"{_icon(Game(r['game']))} **{Game(r['game']).label}** — `{r['played']}` rounds"
                    for r in overview[:5]
                )
                fields.append(("Most Played", popular))

        view = stats_card(
            f"{_icon(selected)} {selected.label} Leaderboard" if selected else "🏆 Game Leaderboard",
            f"Top players in **{ctx.guild.name}**{'' if selected else ' across all games'}, ranked by wins.",
            fields=fields,
            footer="Use /stats games to see your own record.",
            thumbnail=get_asset_url(ctx.guild),
        )
        await ctx.send(view=view)

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

        await send_webhook(self.bot.stats_webhook, embed=embed)

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

    @command("names", alias="ns", description="Shows the username history of a user.", guild_only=True)
    @describe(member="The member to show the username history for.")
    async def names(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member | discord.User = member or ctx.author

        usernames: list[asyncpg.Record] = await self.get_item_history(user.id, "name")
        nicknames: list[asyncpg.Record] = await self.get_item_history(user.id, "nickname")

        if not usernames and not nicknames:
            await ctx.send_error("No name history found.")
            return

        def history(records: list[asyncpg.Record]) -> str:
            # Newest first; stats_card trims the tail if someone has renamed hundreds of times.
            return "\n".join(f"`{name}` {_since(changed_at, 'R')}" for name, changed_at in records) or "*None recorded.*"

        view = stats_card(
            f"Name History for {user}",
            fields=[("Usernames", history(usernames)), ("Nicknames", history(nicknames))],
            thumbnail=get_asset_url(user),
        )
        await ctx.send(view=view)

    @command("lastseen", alias="ls", description="Shows when a user was last seen.", guild_only=True)
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

    @command("avatarhistory", description="Shows the avatar history of a user.", alias="avhy", guild_only=True)
    @describe(member="The member to show the avatar history for.")
    async def avatar_history(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows the avatar history of a user."""
        user: discord.Member | discord.User = member or ctx.author

        async with ctx.progress("Fetching avatar history...") as progress:
            with Timer() as timer:
                history = await self.get_avatar_history(user)

                if not history:
                    await ctx.send_error("No avatar history found.")
                    return

                fetching_time = timer.reset()

                avatars = [x["avatar"] for x in history]
                if not avatars:
                    return

                await progress.update(f"Generating collage from {len(avatars)} avatars...")
                file = await self.bot.render.avatar_collage(avatars)

        view = chart_card(
            f"Avatar Collage for {user}",
            f"Showing `{len(history)}` of up to `100` changes.",
            file.filename,
            footer=(
                f"Last updated {_since(history[-1]['changed_at'], 'R')} • "
                f"fetched in {fetching_time:.3f}s, rendered in {timer.seconds:.3f}s"
            ),
        )
        await ctx.send(view=view, file=file)

    @command("presence", alias="ps", description="Shows the presence history of a user.", guild_only=True)
    @describe(member="The member to show the presence history for.")
    async def presence(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        user: discord.Member | discord.User = member or ctx.author
        query_days = 30

        async with ctx.progress("Fetching presence history...") as progress:
            with Timer() as timer:
                history: list[asyncpg.Record] = await self.get_presence_history(user.id, days=query_days)

                if not history:
                    await ctx.send_error("No presence history found.")
                    return

                fetching_time = timer.reset()

                await progress.update("Analyzing presence data...")
                breakdown = summarize_presence((record["changed_at"], record["status_before"]) for record in history)

                if not breakdown.has_data:
                    await ctx.send_error("Not enough data to generate a chart.")
                    return

                analyzing_time = timer.reset()

                await progress.update("Generating presence chart...")
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

        tracked = sum(breakdown.durations.values()) or 1
        shares = " • ".join(
            f"{label} **{breakdown.durations[key] / tracked:.0%}**"
            for label, key in (("Online", "Online"), ("Idle", "Idle"), ("DND", "Do Not Disturb"), ("Offline", "Offline"))
        )
        view = chart_card(
            f"Activity of {user} (last {query_days} days)",
            shares,
            canvas.filename,
            footer=(
                f"Watching since {_since(breakdown.earliest, 'D')} • fetched in {fetching_time:.3f}s, "
                f"analysed in {analyzing_time:.3f}s, rendered in {timer.seconds:.3f}s"
            ),
        )
        await ctx.send(view=view, file=canvas)

    @command(name="userinfo", alias="ui", description="Shows information about a user.", guild_only=True)
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
            guild_related.append(f"**Boosting Since:** {discord.utils.format_dt(user.premium_since, 'R')}")
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
        await send_webhook(self.bot.stats_webhook, embed=embed)

    def add_record(self, record: logging.LogRecord) -> None:
        self._logging_queue.put_nowait(record)

    async def send_log_record(self, record: logging.LogRecord) -> None:
        """Posts a gateway record as a short human line, collapsing bursts."""
        downtime = None
        if self._disconnected_at is not None and self._disconnected_at <= record.created:
            downtime = record.created - self._disconnected_at
        line = rewrite_gateway_line(record.getMessage(), downtime=downtime)
        if line is None:
            return
        if downtime is not None and line.startswith(("Reconnected", "New gateway session")):
            self._disconnected_at = None
        if line := self._gateway_burst.push(line, time.monotonic()):
            await self._send_gateway_line(line, record.created)

    async def _send_gateway_line(self, line: str, created: float) -> None:
        dt = datetime.datetime.fromtimestamp(created, datetime.UTC)
        await self._post(
            f"{Emojis.info} {discord.utils.format_dt(dt, style='T')} {line}",
            username="Gateway",
            avatar_url="https://klappstuhl.me/gallery/raw/xNZqq.png",
            silent=True,
        )

    async def _fetch_lavalink_metrics(self) -> LavalinkMetrics | None:
        """Scrape the connected Lavalink node's Prometheus ``/metrics`` endpoint.

        Returns ``None`` when no node is connected, the scrape fails, or the node has
        metrics disabled — the ``bothealth`` report degrades gracefully in that case.
        """
        try:
            node = wavelink.Pool.get_node()
        except (wavelink.InvalidNodeException, RuntimeError):
            return None

        url = node.uri.rstrip("/") + "/metrics"
        try:
            async with self.bot.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError):
            return None

        return parse_lavalink_metrics(text)

    async def send_bot_health(self, ctx: Context) -> None:
        """Sends the bot health report; invoked by ``dev bothealth``, which owns the owner gate."""
        level_accents = {
            HealthLevel.HEALTHY: Accent.success,
            HealthLevel.WARNING: Accent.warning,
            HealthLevel.UNHEALTHY: Accent.error,
        }

        # Gather the raw runtime observations; the analysis is delegated to the service.
        pool = self.bot.db.pool_stats()
        connections = [
            ConnectionState(generation=c.generation, in_use=c.in_use, is_closed=c.is_closed)
            for c in pool.connections
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
            current_generation=pool.generation,
            is_being_spammed=bool(being_spammed),
            command_waiters=command_waiters,
            has_failed_inner_tasks=bool(bad_inner_tasks),
            global_rate_limit=global_rate_limit,
        )

        description = (
            f"Uptime: **{human_timedelta(self.bot.startup_timestamp, suffix=False)}**\n"
            f"WebSocket Latency: `{self.bot.latency * 1000:.1f}ms`\n"
            f"Global Rate Limit: {'**YES**' if global_rate_limit else 'No'}"
        )

        connection_lines = [
            f"<Holder i={index} gen={c.generation} in_use={c.in_use} closed={c.is_closed}>"
            for index, c in enumerate(connections, start=1)
        ]
        pool_value = (
            f"Waiters: {pool.waiting} • Generation: {pool.generation} • In use: {pool.in_use}/{pool.size} • "
            f"Questionable: {report.questionable_connections}\n"
            f"```py\n{_fit(chr(10).join(connection_lines), 900) or 'No connections'}\n```"
        )

        tracker = self.bot.db.query_tracker
        db_lines = [
            f"Total Queries: {fnumb(tracker.total_queries)} • Avg: `{tracker.avg_duration_ms:.2f}ms` • "
            f"Slow (>{tracker._threshold_ms:.0f}ms): {len(tracker._slow_queries)}",
            *(f"`{sq['duration_ms']}ms` {discord.utils.escape_markdown(sq['query'][:60])}" for sq in tracker.slow_queries(3)),
        ]

        guild_size, guild_max = self.bot.db.get_guild_config.get_stats()
        user_size, user_max = self.bot.db.get_user_config.get_stats()
        sentinel_size, sentinel_max = self.bot.db.get_guild_sentinel.get_stats()
        memory_usage = self.process.memory_full_info().uss / 1024**2
        cpu_usage = self.process.cpu_percent() / (psutil.cpu_count() or 1)
        runtime = (
            f"Process: {memory_usage:.2f} MiB • {cpu_usage:.2f}% CPU\n"
            f"Extensions: {len(self.bot.extensions)} • Cogs: {len(self.bot.cogs)}\n"
            f"Inner tasks: {len(inner_tasks)} (failed: {bad_inner_tasks or 'none'}) • "
            f"Events waiting: {len(event_tasks)}\n"
            f"Spammers: {', '.join(str(s) for s in being_spammed) if being_spammed else 'none'} • "
            f"Commands waiting: {command_waiters} • Avatars waiting: {len(self._avatar_data_batch)}\n"
            f"Cache: guilds {guild_size}/{guild_max} • users {user_size}/{user_max} • "
            f"sentinels {sentinel_size}/{sentinel_max}"
        )

        # Lavalink node (scraped from its Prometheus /metrics endpoint)
        ll = await self._fetch_lavalink_metrics()
        if ll is not None:
            ll_uptime = human_timedelta(
                discord.utils.utcnow() - datetime.timedelta(seconds=ll.uptime_seconds), suffix=False
            )
            lavalink = (
                f"Players: {ll.playing_players} playing / {ll.players} total • Uptime: **{ll_uptime}**\n"
                f"Memory: {ll.memory_used_bytes / 1024**2:.0f} MiB used "
                f"({ll.memory_used_ratio * 100:.0f}% of {ll.memory_allocated_bytes / 1024**2:.0f} MiB)\n"
                f"CPU: {ll.lavalink_load * 100:.1f}% LL / {ll.system_load * 100:.1f}% system ({ll.cpu_cores} cores)"
            )
        else:
            lavalink = "No node connected or metrics unavailable."

        view = stats_card(
            "Bot Health Report",
            description,
            fields=[
                ("Runtime", runtime),
                ("Database", "\n".join(db_lines)),
                ("Lavalink", lavalink),
                ("Connection Pool", pool_value),
            ],
            footer=f"{report.warnings} warning(s)",
            accent=level_accents[report.level],
        )
        await ctx.send(view=view)

    @command(hidden=True, description="Shows the current gateway traffic interaction with the bot.")
    @commands.is_owner()
    async def gateway(self, ctx: Context) -> None:
        """Gateway related stats."""
        yesterday = discord.utils.utcnow() - datetime.timedelta(days=1)
        traffic = summarize_gateway_traffic(self.bot.identifies, self.bot.resumes, since=yesterday)
        view = stats_card(
            "Gateway",
            f"RESUMEs: `{traffic.total_resumes}`\nIDENTIFYs: `{traffic.total_identifies}`",
            footer="Last 24 hours",
            accent=Accent.neutral,
        )
        await ctx.send(view=view)

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
        if not counter:
            await ctx.send_info("No commands have been used since the last restart.")
            return

        total = sum(counter.values())
        slash_commands = self.bot.command_types_used[True]
        cpm = total / self._minutes_up()

        if limit >= 0:
            common = counter.most_common(limit or None)
            title = f"Top {limit} Commands" if limit else "All Commands"
        else:
            common = counter.most_common()[limit:]
            title = f"Bottom {abs(limit)} Commands"

        image = await self.bot.render.bar_chart(
            dict(sorted(dict(common).items(), key=lambda item: item[1], reverse=True)),
            f"{total} total commands used ({slash_commands} slash command uses) ({cpm:.2f}/minute)",
            merge=True,
        )
        assert isinstance(image, discord.File)
        view = chart_card(
            title,
            f"`{total}` commands since {_since(self.bot.startup_timestamp, 'R')} • `{cpm:.2f}`/minute • "
            f"`{slash_commands}` slash",
            image.filename,
        )
        await ctx.send(view=view, file=image)

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
            # Every command, not the default top 5: everything past the limit was reported as unused.
            records = await self.get_commands_stats(days=days, limit=len(all_commands) + 100) or []
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
            embed.add_field(name="Top 10", value=top_ten or "None")
            embed.add_field(name="Bottom 10", value=bottom_ten or "None")

            unused = ", ".join(name for name, uses in as_data if uses == 0)
            if len(unused) > 1024:
                unused = "Way too many..."

            embed.add_field(name="Unused", value=unused or "None", inline=False)

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

    # Beta has no log-channel worker; a handler there would only fill the queue.
    if not beta:
        bot.log_handler = handler = LoggingHandler(cog)
        logging.getLogger().addHandler(handler)


async def teardown(bot: Bot) -> None:
    if hasattr(bot, 'log_handler'):
        logging.getLogger().removeHandler(bot.log_handler)
        del bot.log_handler
