"""Owner-only developer tools for Percy's own internals.

Jishaku already covers the generic side (``jsk sudo``/``su``/``in``, ``jsk load``/``reload``, ``jsk py``,
``jsk sh``, ``jsk sync``, ``jsk debug``); everything here is something only Percy knows about: its
memoized getters, feature flags, guild config records, timers, AI engine and Lavalink nodes.
"""

from __future__ import annotations

import ast
import asyncio
import io
import re
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import discord
import wavelink
from discord.ext import commands

from app.core import Cog, Context
from app.core.converter import CodeblockConverter
from app.core.models import group
from app.core.pagination import TextSourcePaginator
from app.core.timer import Timer
from app.utils import TabularData, pluralize, tail

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from asyncpg import Record

    from app.utils.cache import CacheProtocol


def _iter_caches() -> Iterator[tuple[str, CacheProtocol]]:
    """Every ``@cache.cache()`` function in a loaded ``app.*`` module, module-level or on a class."""
    seen: set[int] = set()
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("app.") or module is None:
            continue
        for name, value in list(vars(module).items()):
            candidates = [(name, value)]
            if isinstance(value, type) and value.__module__ == module_name:
                candidates = [(f"{name}.{attr}", v) for attr, v in vars(value).items()]
            for qualname, obj in candidates:
                if hasattr(obj, "invalidate") and hasattr(obj, "get_key") and id(obj) not in seen:
                    seen.add(id(obj))
                    yield f"{module_name}.{qualname}", obj


def _parse_value(raw: str) -> Any:
    """``null``/``none`` → None, Python literals as themselves, anything else as a string."""
    if raw.lower() in ("null", "none"):
        return None
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


class Dev(Cog):
    """Developer tools for the bot owner."""

    __hidden__ = True
    emoji = "<:originally_known_as:1322355070578327692>"

    async def cog_check(self, ctx: commands.Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @staticmethod
    async def send_table(ctx: Context, records: list[Any], *, footer: str = "") -> None:
        table = TabularData()
        table.set_columns(list(records[0].keys()))
        table.add_rows(list(r.values()) for r in records)
        render = table.render()

        if len(render) > 1900:
            await ctx.send("Too many results...", file=discord.File(io.BytesIO(render.encode("utf-8")), "results.txt"))
        else:
            await ctx.send(f"```\n{render}\n```{footer}")

    # -- overview -----------------------------------------------------------

    @group(name="dev", hidden=True, description="Developer tools. Lists every owner-only command.")
    async def dev(self, ctx: Context) -> None:
        """Lists all owner-only commands grouped by cog."""
        entries: dict[str, list[str]] = {}

        for cog_name, cog in sorted(self.bot.cogs.items()):
            cog_is_owner_only = getattr(cog, "__hidden__", False) or cog.qualified_name == "Jishaku"
            owner_cmds: list[str] = []
            for cmd in cog.walk_commands():
                if cog_is_owner_only or self._has_owner_check(cmd):
                    sig = f"`{ctx.prefix}{cmd.qualified_name}`"
                    doc = cmd.short_doc or cmd.description or cmd.help
                    if doc:
                        sig += f" — {doc}"
                    owner_cmds.append(sig)
            if owner_cmds:
                entries[cog_name] = owner_cmds

        paginator = TextSourcePaginator(ctx, prefix="", suffix="", max_size=1900)
        for cog_name, cmds in entries.items():
            paginator.add_line(f"**{cog_name}**")
            for line in cmds:
                paginator.add_line(f"  {line}")
            paginator.add_line("")
        await paginator.start()

    @staticmethod
    def _has_owner_check(cmd: commands.Command) -> bool:
        """Whether a command or any of its parents has an ``is_owner()`` check."""
        current: commands.Command | commands.Group | None = cmd
        while current is not None:
            for check in current.checks:
                if getattr(check, "__qualname__", "").startswith("is_owner"):
                    return True
            current = current.parent
        return False

    @dev.command(name="bothealth", description="Shows the bot health report: gateway, DB pool, caches, Lavalink.")
    async def dev_bothealth(self, ctx: Context) -> None:
        """Shows the bot health report (built by the Stats cog, which holds the state it reads)."""
        stats = self.bot.get_cog("Stats")
        if stats is None or not hasattr(stats, "send_bot_health"):
            await ctx.send_error("The Stats cog is not loaded.")
            return
        await stats.send_bot_health(ctx)

    # -- owner bypass -----------------------------------------------------------

    @dev.command(name="bypass", description="Toggles whether owners skip permission checks and cooldowns.")
    async def dev_bypass(self, ctx: Context, state: bool | None = None) -> None:
        """Turns the owner bypass on or off; with no argument, shows it.

        Off means owners run commands like a normal member: permission checks, guild command
        overrides and cooldowns all apply. Owner-only commands (this one included) keep working.
        Resets to on at restart.
        """
        if state is not None:
            self.bot.owner_bypass = state
        if self.bot.owner_bypass:
            await ctx.send_info("Owner bypass is **on**: you skip permission checks and cooldowns.")
        else:
            await ctx.send_info("Owner bypass is **off**: checks and cooldowns apply to you like any member.")

    # -- cache ----------------------------------------------------------------

    @dev.group(name="cache", description="Lists every memoized getter with size and hit rate.")
    async def dev_cache(self, ctx: Context, *, name: str | None = None) -> None:
        """Lists the memoized getters, optionally only those whose name contains ``name``."""
        rows = [
            {"cache": qualname.removeprefix("app."), **fn.cache_info()}
            for qualname, fn in sorted(_iter_caches())
            if name is None or name in qualname
        ]
        if not rows:
            await ctx.send_error("No matching caches.")
            return
        await self.send_table(ctx, rows)

    @dev_cache.command(name="clear", description="Invalidates a cache, or only its keys containing a substring.")
    async def dev_cache_clear(self, ctx: Context, name: str, *, contains: str = "") -> None:
        """Invalidates every cache whose name ends with ``name`` (``all`` for every cache).

        With ``contains``, only keys containing that substring are dropped, e.g.
        ``dev cache clear get_guild_config guild_id=123``.
        """
        matches = [(q, fn) for q, fn in _iter_caches() if name == "all" or q.endswith(name)]
        if not matches:
            await ctx.send_error(f"No cache named `{name}`. See `{ctx.prefix}dev cache`.")
            return

        dropped = 0
        for _, fn in matches:
            before = len(fn.cache)
            fn.invalidate_containing(contains)
            dropped += before - len(fn.cache)
        await ctx.send_success(f"Dropped {pluralize(dropped):entry|entries} from {pluralize(len(matches)):cache}.")

    # -- feature flags ----------------------------------------------------------

    @dev.group(name="flags", description="Shows runtime-disabled commands and cogs.")
    async def dev_flags(self, ctx: Context) -> None:
        """Shows the commands and cogs currently disabled at runtime."""
        status = self.bot.feature_flags.status()
        cogs = ", ".join(f"`{c}`" for c in status["disabled_cogs"]) or "None"
        cmds = ", ".join(f"`{c}`" for c in status["disabled_commands"]) or "None"
        await ctx.send(f"**Disabled cogs:** {cogs}\n**Disabled commands:** {cmds}\n-# In-memory, reset on restart.")

    async def _toggle_flag(self, ctx: Context, target: str, *, enable: bool) -> None:
        flags = self.bot.feature_flags
        if (cog := self.bot.get_cog(target)) is not None:
            (flags.enable_cog if enable else flags.disable_cog)(cog.qualified_name)
            label = f"cog `{cog.qualified_name}`"
        elif (cmd := self.bot.get_command(target)) is not None:
            (flags.enable_command if enable else flags.disable_command)(cmd.qualified_name)
            label = f"command `{cmd.qualified_name}`"
        else:
            await ctx.send_error(f"No cog or command named `{target}`.")
            return
        await ctx.send_success(f"{'Enabled' if enable else 'Disabled'} {label}.")

    @dev_flags.command(name="disable", description="Disables a command or cog until restart.")
    async def dev_flags_disable(self, ctx: Context, *, target: str) -> None:
        """Disables a command (qualified name) or cog (name) until restart."""
        await self._toggle_flag(ctx, target, enable=False)

    @dev_flags.command(name="enable", description="Re-enables a command or cog.")
    async def dev_flags_enable(self, ctx: Context, *, target: str) -> None:
        """Re-enables a previously disabled command or cog."""
        await self._toggle_flag(ctx, target, enable=True)

    # -- guild config -------------------------------------------------------------

    @dev.command(name="config", description="Dumps or sets a guild_config column, bypassing the dashboard.")
    async def dev_config(
        self, ctx: Context, guild_id: int | None = None, field: str | None = None, *, value: str | None = None
    ) -> None:
        """Dumps a guild's config, one column, or sets a column (``null`` clears it).

        Defaults to the current guild. Writes go through ``GuildConfig.update`` so the column is
        validated and the cached config is invalidated, exactly as a dashboard save would.
        """
        guild_id = guild_id or (ctx.guild.id if ctx.guild else None)
        if guild_id is None:
            await ctx.send_error("Pass a guild id outside of a server.")
            return

        config = await self.bot.db.get_guild_config(guild_id)
        if field is not None and value is not None:
            try:
                await config.update(**{field: _parse_value(value)})
            except Exception as exc:
                await ctx.send_error(f"Update failed: `{exc}`")
                return
            config = await self.bot.db.get_guild_config(guild_id)

        record = await self.bot.db.guilds.get_config_record(guild_id)
        data = dict(record) if record else {}
        if field is not None:
            if field not in data:
                await ctx.send_error(f"Unknown column `{field}`.")
                return
            data = {field: data[field]}

        rendered = "\n".join(f"{k} = {v!r}" for k, v in data.items()) or "No row stored for this guild."
        paginator = TextSourcePaginator(ctx, prefix="```py", max_size=1900)
        for line in rendered.splitlines():
            paginator.add_line(line)
        await paginator.start()

    # -- timers -----------------------------------------------------------------------

    @dev.group(name="timers", description="Lists the soonest pending timers.")
    async def dev_timers(self, ctx: Context, event: str | None = None) -> None:
        """Lists the 25 soonest pending timers, optionally of one ``event``."""
        records = await self.bot.db.timers.list_pending(event=event)
        if not records:
            await ctx.send_info("No pending timers.")
            return
        rows = [
            {"id": r["id"], "event": r["event"], "expires": f"{r['expires']:%Y-%m-%d %H:%M}", "tz": r["timezone"]}
            for r in records
        ]
        total = await self.bot.db.timers.count_pending()
        await self.send_table(ctx, rows, footer=f"\n*{pluralize(total):timer} pending in total.*")

    @dev_timers.command(name="fire", description="Fires a timer now.")
    async def dev_timers_fire(self, ctx: Context, timer_id: int) -> None:
        """Dispatches a stored timer immediately, as if it had expired."""
        record = await self.bot.db.timers.fetch_timer(timer_id)
        if record is None:
            await ctx.send_error(f"No timer with id `{timer_id}`.")
            return
        await self.bot.timers.call(Timer(bot=self.bot, record=record))
        # The dispatcher may be sleeping on exactly this timer; restart it so it reloads the next one.
        self.bot.timers.reset_task()
        await ctx.send_success(f"Fired timer `{timer_id}` (`{record['event']}`).")

    @dev_timers.command(name="cancel", description="Deletes a timer without firing it.")
    async def dev_timers_cancel(self, ctx: Context, timer_id: int) -> None:
        """Deletes a stored timer without dispatching it."""
        if await self.bot.db.timers.fetch_timer(timer_id) is None:
            await ctx.send_error(f"No timer with id `{timer_id}`.")
            return
        await self.bot.db.timers.delete_timer(timer_id)
        self.bot.timers.reset_task()
        await ctx.send_success(f"Cancelled timer `{timer_id}`.")

    # -- AI -----------------------------------------------------------------------------

    @dev.group(name="ai", description="Probes the AI engine, bypassing the cached health report.")
    async def dev_ai(self, ctx: Context) -> None:
        """Runs a fresh AI health probe."""
        report = await self.bot.ai.health(max_age=0)
        lines = [
            f"Enabled: **{report.enabled}** | Reachable: **{report.reachable}** | Degraded: **{report.degraded}**",
            f"Latency: `{report.latency_ms or 0:.0f}ms` | Ollama: `{report.version or 'unknown'}`",
            f"Models: {', '.join(f'{k}=`{v}`' for k, v in report.models.items()) or 'None'}",
            f"Calls: {report.calls} | Failures: {report.failures} | Cache: {report.cache_hits}/{report.cache_misses}",
        ]
        if report.error:
            lines.append(f"Error: `{report.error}`")
        await ctx.send("\n".join(lines))

    @dev_ai.command(name="ask", description="Sends a raw prompt to the model, no persona, no flags.")
    async def dev_ai_ask(self, ctx: Context, *, prompt: str) -> None:
        """Sends a raw user prompt to the SMART tier and shows the reply and timing."""
        async with ctx.typing():
            start = time.perf_counter()
            reply = await self.bot.ai.complete([{"role": "user", "content": prompt}])
            dt = (time.perf_counter() - start) * 1000.0
        if reply is None:
            await ctx.send_error(f"No reply after `{dt:.0f}ms` (engine unavailable or failed).")
            return
        await ctx.send(f"{reply[:1900]}\n-# {dt:.0f}ms")

    # -- music --------------------------------------------------------------------------

    @dev.group(name="music", description="Shows the Lavalink nodes and their players.")
    async def dev_music(self, ctx: Context) -> None:
        """Shows each Lavalink node's status, session and players."""
        nodes = list(wavelink.Pool.nodes.values())
        if not nodes:
            await ctx.send_warning(f"No Lavalink nodes in the pool. Try `{ctx.prefix}dev music reconnect hard`.")
            return

        lines = []
        for node in nodes:
            websocket = node._websocket
            keep_alive = websocket.keep_alive_task if websocket else None
            alive = keep_alive is not None and not keep_alive.done()
            playing = sum(1 for p in node.players.values() if p.playing)
            lines.append(
                f"**{node.identifier}** `{node.uri}`\n"
                f"Status: **{node.status.name}** | Socket alive: **{alive}** | Session: `{node.session_id}`\n"
                f"Players: {playing} playing / {len(node.players)} total"
            )
        await ctx.send("\n\n".join(lines))

    @dev_music.command(name="reconnect", description="Reconnects stuck Lavalink nodes (soft resumes, hard restarts).")
    async def dev_music_reconnect(self, ctx: Context, mode: str = "soft") -> None:
        """Reconnects every Lavalink node.

        ``soft`` (default) opens a new websocket on the existing session, so Lavalink resumes
        every player inside the node's resume window, the same as the music watchdog does.
        ``hard`` closes each node (disconnecting its players), connects it from scratch and
        restores players from the persisted sessions. Use it when soft does not help, e.g. a
        node stuck in CONNECTING. With no node in the pool at all, both modes run the
        startup connect.
        """
        if mode not in ("soft", "hard"):
            await ctx.send_error("Mode must be `soft` or `hard`.")
            return

        nodes = list(wavelink.Pool.nodes.values())
        async with ctx.typing():
            if not nodes:
                await self.bot._connect_lavalink()
            for node in nodes:
                try:
                    if mode == "hard":
                        await node.close()
                        await node._connect(client=self.bot)
                    elif node._websocket is None:
                        await node._connect(client=self.bot)
                    else:
                        await node._websocket.connect()
                except Exception as exc:
                    await ctx.send_error(f"`{node.identifier}` failed to reconnect: `{exc}`")
                    return

        music = self.bot.get_cog("Music")
        if mode == "hard" and music is not None and hasattr(music, "_restore_sessions"):
            self.bot.loop.create_task(music._restore_sessions())

        statuses = ", ".join(f"`{n.identifier}`: **{n.status.name}**" for n in wavelink.Pool.nodes.values())
        await ctx.send_success(f"{mode.title()} reconnect done. {statuses or 'No nodes in the pool.'}")

    # -- command tree -------------------------------------------------------------------

    @dev.group(name="tree", description="Diffs local slash commands against the ones synced to Discord.")
    async def dev_tree(self, ctx: Context, guild_id: int | None = None) -> None:
        """Diffs local and synced top-level slash commands, globally or for one guild."""
        guild = discord.Object(id=guild_id) if guild_id else None
        local = {c.name for c in self.bot.tree.get_commands(guild=guild)}
        remote = {c.name for c in await self.bot.tree.fetch_commands(guild=guild)}

        scope = f"guild `{guild_id}`" if guild_id else "global"
        missing = ", ".join(f"`{n}`" for n in sorted(local - remote)) or "None"
        stale = ", ".join(f"`{n}`" for n in sorted(remote - local)) or "None"
        await ctx.send(
            f"**Scope:** {scope} | local {len(local)}, synced {len(remote)}\n"
            f"**Not synced yet:** {missing}\n**Synced but gone locally:** {stale}"
        )

    @dev_tree.command(name="sync", description="Syncs slash commands and re-resolves command mentions.")
    async def dev_tree_sync(self, ctx: Context, guild_id: int | None = None) -> None:
        """Syncs the tree globally or to one guild, then refreshes the IDs behind ``Command.mention``."""
        guild = discord.Object(id=guild_id) if guild_id else None
        async with ctx.typing():
            synced = await self.bot.tree.sync(guild=guild)
            self.bot._app_command_ids_resolved = False
            await self.bot.resolve_app_command_ids(guild=guild)
        await ctx.send_success(f"Synced {pluralize(len(synced)):command} {'to the guild' if guild else 'globally'}.")

    # -- misc (formerly the Admin cog) -----------------------------------------------------

    @dev.command(name="pm", description="Sends a DM to a user by ID.")
    async def dev_pm(self, ctx: Context, user_id: int, *, content: str) -> None:
        """Sends a DM to a user by ID."""
        user = self.bot.get_user(user_id) or (await self.bot.fetch_user(user_id))

        message = (
            f"{content}\n\n"
            "*This is a DM sent because you had previously requested feedback or I found a bug "
            "in a command you used, I do not monitor this DM.*"
        )
        try:
            await user.send(message)
        except discord.HTTPException:
            await ctx.send_error(f"Could not send a DM to {user}.")
        else:
            await ctx.send_success("PM successfully sent.")

    @dev.command(name="tasks", description="Lists every task running in the asyncio event loop.")
    async def dev_tasks(self, ctx: Context) -> None:
        """Lists every task in the event loop (``jsk tasks`` only shows jishaku's own)."""
        table = TabularData()
        table.set_columns(["Memory ID", "Name", "Object"])

        def strip_memory_id(s: str) -> str:
            return (s.split(" ")[-1])[:-1]

        table.add_rows(
            (strip_memory_id(str(task.get_coro())), task.get_name(), str(task.get_coro()).split(" ")[2])
            for task in asyncio.all_tasks(loop=self.bot.loop)
        )
        rendered = re.sub(r"```\w?.*", "", table.render(), flags=re.MULTILINE)

        paginator = TextSourcePaginator(ctx, prefix="```ansi")
        for line in rendered.splitlines():
            paginator.add_line(line)
        await paginator.start()

    @dev.command(name="log", description="Uploads the last lines of a log file.")
    async def dev_log(self, ctx: Context, log: str = "percy", last_lines: int = 600) -> None:
        """Uploads the last ``last_lines`` lines of ``<log>.log``."""
        f_file = f"{Path(log).name}.log"
        try:
            with Path(f_file).open("rb") as f:
                lines = tail(f, last_lines)
        except FileNotFoundError:
            await ctx.send_error(f"`{f_file}` does not exist.")
            return
        assert isinstance(lines, list)
        await ctx.send(file=discord.File(io.BytesIO(b"".join(lines)), f_file))

    # -- SQL --------------------------------------------------------------------------------

    @dev.group(name="sql", description="Runs SQL against the bot's database.")
    async def dev_sql(self, ctx: Context, *, query: Annotated[list[str], CodeblockConverter]) -> None:
        """Runs SQL. More than one statement is executed without returning rows."""
        query_text = "\n".join(query)

        strategy: Callable[[str], Awaitable[list[Record]] | Awaitable[str]]
        strategy = ctx.db.execute if query_text.count(";") > 1 else ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query_text)
            dt = (time.perf_counter() - start) * 1000.0
        except Exception:
            await ctx.send(f"```py\n{traceback.format_exc()[-1900:]}\n```")
            return

        if isinstance(results, str) or len(results) == 0:
            await ctx.send(f"`{dt:.2f}ms: {results}`")
            return
        await self.send_table(ctx, results, footer=f"\n*Returned {pluralize(len(results)):row} in {dt:.2f}ms*")

    @dev_sql.command(name="schema", description="Describes a table's schema.")
    async def dev_sql_schema(self, ctx: Context, *, table_name: str) -> None:
        """Runs a query describing the table schema."""
        results = await ctx.db.admin.get_table_schema(table_name)
        if not results:
            raise commands.BadArgument(f"Table `{table_name}` not found")
        await self.send_table(ctx, results)

    @dev_sql.command(name="tables", description="Lists every table.")
    async def dev_sql_tables(self, ctx: Context) -> None:
        """Lists all SQL tables in the database."""
        results = await ctx.db.admin.list_tables()
        if not results:
            raise commands.BadArgument("Could not find any tables")
        await self.send_table(ctx, results)

    @dev_sql.command(name="sizes", description="Shows how much space each table takes up.")
    async def dev_sql_sizes(self, ctx: Context) -> None:
        """Display how much space the database is taking up."""
        results = await ctx.db.admin.get_table_sizes()
        if not results:
            await ctx.send_error("Could not find any tables")
            return
        await self.send_table(ctx, results)

    @dev_sql.command(name="explain", aliases=["analyze"], description="EXPLAINs (or ANALYZEs) a query.")
    async def dev_sql_explain(self, ctx: Context, *, query: Annotated[list[str], CodeblockConverter]) -> None:
        """Explain an SQL query; invoked as ``analyze`` it also runs it."""
        json = await ctx.db.admin.explain_query("\n".join(query), analyze=ctx.invoked_with == "analyze")
        if json is None:
            await ctx.send_error("No results.")
            return
        await ctx.send(file=discord.File(io.BytesIO(json[0].encode("utf-8")), filename="explain.json"))


async def setup(bot) -> None:
    await bot.add_cog(Dev(bot))
