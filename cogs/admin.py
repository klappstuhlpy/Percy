import asyncio
import copy
import io
import subprocess
import sys
import textwrap
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Union, Awaitable, Optional

import discord
from asyncpg import Record
from discord import app_commands
from discord.app_commands import default_permissions
from discord.ext import commands

from bot import Percy
from cogs.utils.paginator import TextSource, TextPaginator
from . import command
from .utils import converters
from .utils.tasks import PerformanceMocker
from .utils.context import Context
from .utils.constants import PLAYGROUND_GUILD_ID, PH_GUILD_ID


class Admin(commands.Cog):
    """Admin commands for the bot owner."""

    def __init__(self, bot):
        self.bot: Percy = bot
        self._last_result: Optional[Any] = None

        self.compile_ctx_menu = app_commands.ContextMenu(
            name='Compile Code',
            callback=self.compile_callback,
            guild_ids=[PH_GUILD_ID, PLAYGROUND_GUILD_ID],
        )
        self.bot.tree.add_command(self.compile_ctx_menu)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="originally_known_as", id=1113011941921792050)

    async def run_process(self, command: str) -> list[str]:  # noqa
        try:
            process = await asyncio.create_subprocess_shell(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await process.communicate()
        except NotImplementedError:
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await self.bot.loop.run_in_executor(None, process.communicate)

        return [output.decode() for output in result]

    @staticmethod
    def cleanup_code(content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')

    async def cog_check(self, ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @staticmethod
    def build_eval_embed(
            user: discord.Member, time_taken: float, result: Optional[str] = None, trc: Optional[str] = None
    ) -> discord.Embed:
        py_ver = ".".join(sys.version_info[:3])

        if trc:
            if result:
                description = f'```py\n{result}{trc}\n```'
            else:
                description = f'```py\n{trc}\n```'
            embed = discord.Embed(title="Compiler Output", description=description, color=discord.Color.red())
        else:
            description = f'```py\n{result or "[No output]"}\n```'
            embed = discord.Embed(title="Program Output", description=description, color=discord.Color.green())
        embed.set_footer(text=f"{user} • {time_taken}ms • python{py_ver}")
        return embed

    @default_permissions(administrator=True)
    async def compile_callback(self, interaction: discord.Interaction, message: discord.Message):
        """Compiles a code from a message."""
        await interaction.response.defer(ephemeral=True)
        content = str(message.system_content)

        if not content.startswith('```py') or not content.endswith('```'):
            return await interaction.followup.send(f'<:redTick:1079249771975413910> **Critical:** Can not find a '
                                                   f'*Python* Code block to compile in {message.jump_url}.',
                                                   suppress_embeds=True, ephemeral=True)

        interaction.message = await interaction.followup.send(
            embed=discord.Embed(description="*Processing request...*",
                                color=discord.Color.orange()),
            ephemeral=True)

        env = {
            'bot': self.bot,
            'self': self,  # type: Admin
            'ctx': await self.bot.get_context(message),
            'channel': interaction.channel,
            'author': interaction.user,
            'guild': interaction.guild,
            'message': message,
            '_': self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(content)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        t_1 = time.time()
        try:
            exec(to_compile, env)
        except:  # noqa
            t_2 = time.time()
            return await interaction.message.edit(embed=self.build_eval_embed(
                interaction.user, round((t_2 - t_1) * 1000, 2), trc=traceback.format_exc())
            )

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except:  # noqa
            t_2 = time.time()
            return await interaction.message.edit(embed=self.build_eval_embed(
                interaction.user, round((t_2 - t_1) * 1000, 2), result=stdout.getvalue(), trc=traceback.format_exc())
            )
        else:
            value = stdout.getvalue()
            try:
                await message.add_reaction(discord.PartialEmoji(name="yes", id=1066772402270371850, animated=True))
            except:  # noqa
                pass

            if ret:
                value = self._last_result = ret

            if value:
                t_2 = time.time()
                return await interaction.message.edit(embed=self.build_eval_embed(
                    interaction.user, round((t_2 - t_1) * 1000, 2), result=self.truncate_to_code(value))
                )

    @staticmethod
    def truncate_to_code(text: str) -> str:
        text = '```py\n' + text + '```'
        if len(text) <= 4096:
            return text
        return text[:4090] + '…```'

    @command(
        commands.command,
        hidden=True,
        name='aeval'
    )
    async def _aeval(self, ctx: Context, *, body: str):
        """Evaluates a code"""

        message = await ctx.send(
            embed=discord.Embed(description="*Processing request...*",
                                color=discord.Color.orange())
        )

        env = {
            'bot': self.bot,
            'self': self,  # type: Admin
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': message,
            '_': self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        t_1 = time.time()
        try:
            exec(to_compile, env)
        except:  # noqa
            t_2 = time.time()
            return await message.edit(embed=self.build_eval_embed(
                ctx.author, round((t_2 - t_1) * 1000, 2), trc=traceback.format_exc())
            )

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except:  # noqa
            t_2 = time.time()
            return await message.edit(embed=self.build_eval_embed(
                ctx.author, round((t_2 - t_1) * 1000, 2), trc=stdout.getvalue())
            )
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction(discord.PartialEmoji(name="yes", id=1066772402270371850, animated=True))
            except:  # noqa
                pass

            if ret:
                value = self._last_result = ret

            if value:
                t_2 = time.time()
                return await message.edit(embed=self.build_eval_embed(
                    ctx.author, round((t_2 - t_1) * 1000, 2), result=self.truncate_to_code(value))
                )

    @command(
        commands.command,
        hidden=True,
        description="Checks the timing of a command, attempting to suppress HTTP and DB calls."
    )
    async def perf(self, ctx: Context, *, command: str):  # noqa
        """Checks the timing of a command, attempting to suppress HTTP and DB calls."""

        try:
            msg = copy.copy(ctx.message)
            msg.content = ctx.prefix + command

            new_ctx = await self.bot.get_context(msg, cls=type(ctx))

            new_ctx._state = PerformanceMocker()
            new_ctx.channel = PerformanceMocker()

            if new_ctx.command is None:
                return await ctx.send('No command found')

            start = time.perf_counter()
            try:
                await new_ctx.command.invoke(new_ctx)
            except commands.CommandError:
                end = time.perf_counter()
                success = False
                try:
                    await ctx.send(f'```py\n{traceback.format_exc()}\n```')
                except discord.HTTPException:
                    pass
            else:
                end = time.perf_counter()
                success = True

            await ctx.send(
                embed=discord.Embed(description=f"Status: {ctx.tick(success)} Time: `{(end - start) * 1000:.2f}ms`",
                                    color=discord.Colour.blurple()))
        except:  # noqa
            traceback.print_exc()

    @staticmethod
    async def send_sql_results(ctx: Context, records: list[Any]):
        from .utils.formats import TabularData

        headers = list(records[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in records)
        render = table.render()

        if len(render) > 2000:
            fp = io.BytesIO(render.encode('utf-8'))
            await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))
        else:
            fmt = f'```sql\n{render}\n```'
            await ctx.send(fmt)

    @command(
        commands.group,
        hidden=True,
        invoke_without_command=True,
        description="Run some SQL."
    )
    async def sql(self, ctx: Context, *, query: str):
        """Run some SQL."""
        from .utils.formats import TabularData, plural
        import time

        query = self.cleanup_code(query)

        is_multistatement = query.count(';') > 1
        strategy: Callable[[str], Union[Awaitable[list[Record]], Awaitable[str]]]
        if is_multistatement:
            # fetch does not support multiple statements
            strategy = ctx.db.execute
        else:
            strategy = ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query)
            dt = (time.perf_counter() - start) * 1000.0
        except:  # noqa
            return await ctx.send(f'```py\n{traceback.format_exc()}\n```')

        rows = len(results)
        if isinstance(results, str) or rows == 0:
            return await ctx.send(f'`{dt:.2f}ms: {results}`')

        headers = list(results[0].keys())
        table = TabularData()
        table.set_columns(headers)
        table.add_rows(list(r.values()) for r in results)
        render = table.render()

        fmt = render
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode('utf-8'))
            await ctx.send('Too many results...', file=discord.File(fp, 'results.sql'))
        else:
            fmt = f'```sql\n{render}\n```\n*Returned {plural(rows):row} in {dt:.2f}ms*'
            await ctx.send(fmt)

    @command(
        sql.command,
        name='schema',
        hidden=True
    )
    async def sql_schema(self, ctx: Context, *, table_name: str):
        """Runs a query describing the table schema."""
        query = """
            SELECT column_name, data_type, column_default, is_nullable
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE table_name = $1
            ORDER BY ordinal_position
        """

        results: list[Record] = await ctx.db.fetch(query, table_name)

        if len(results) == 0:
            await ctx.send('Could not find a table with that name')
            return

        await self.send_sql_results(ctx, results)

    @command(
        sql.command,
        name='tables',
        hidden=True
    )
    async def sql_tables(self, ctx: Context):
        """Lists all SQL tables in the database."""

        query = """
            SELECT table_name
            FROM INFORMATION_SCHEMA.TABLES
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name;
        """

        results: list[Record] = await ctx.db.fetch(query)

        if len(results) == 0:
            await ctx.send('Could not find any tables')
            return

        await self.send_sql_results(ctx, results)

    @command(
        sql.command,
        name='sizes',
        hidden=True
    )
    async def sql_sizes(self, ctx: Context):
        """Display how much space the database is taking up."""

        query = """
            SELECT nspname || '.' || relname AS "relation",
                pg_size_pretty(pg_relation_size(C.oid)) AS "size"
            FROM pg_class C
            LEFT JOIN pg_namespace N ON (N.oid = C.relnamespace)
            WHERE nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY pg_relation_size(C.oid) DESC
            LIMIT 20;
        """

        results: list[Record] = await ctx.db.fetch(query)

        if len(results) == 0:
            await ctx.send('Could not find any tables')
            return

        await self.send_sql_results(ctx, results)

    @command(
        commands.command,
        hidden=True,
    )
    async def sudo(
            self,
            ctx: Context,
            channel: Optional[discord.TextChannel],
            who: Union[discord.Member, discord.User],
            *,
            command: str,  # noqa
    ):
        """Run a command as another user optionally in another channel."""
        msg = copy.copy(ctx.message)
        new_channel = channel or ctx.channel
        msg.channel = new_channel
        msg.author = who
        msg.content = ctx.prefix + command
        new_ctx = await self.bot.get_context(msg, cls=type(ctx))
        await self.bot.invoke(new_ctx)

    @command(
        commands.command,
        hidden=True,
    )
    async def do(self, ctx: Context, times: int, *, command: str):  # noqa
        """Repeats a command a specified number of times."""
        msg = copy.copy(ctx.message)
        msg.content = ctx.prefix + command

        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        for i in range(times):
            await new_ctx.reinvoke()

    @command(
        commands.command,
        hidden=True,
        description="Run git Commands in bots Directory in shell. (Shortcut to sh Command)"
    )
    async def git(self, ctx: Context, *, command: str):
        """Runs a shell command."""
        await ctx.invoke(self.sh, command=f"cd {Path(__file__).parent.parent.absolute()}\ngit {command}")  # noqa

    @command(
        commands.command,
        hidden=True,
    )
    async def sh(self, ctx: Context, *, command: str):  # noqa
        """Runs a shell command."""

        async with ctx.typing():
            stdout, stderr = await self.run_process(command)

        if stderr:
            text = f'stdout:\n{stdout}\nstderr:\n{stderr}'
        else:
            text = stdout

        source = TextSource(prefix="```sh")
        for line in text.split('\n'):
            source.add_line(line)

        await TextPaginator.start(ctx, entries=source.pages, timeout=60, per_page=1)

    @command(
        commands.command,
        name="showlog",
        hidden=True
    )
    async def showlog(self, ctx: Context, log: str = 'percy', last_lines: int = 600):
        """Shows the x last lines of a log file."""
        f_file = f'{log}.log'
        path = Path(f_file)
        with open(path, 'rb') as f:
            lines = converters.tail(f, last_lines)
            buf = io.BytesIO()
            for line in lines:
                buf.write(line)
            buf.seek(0)
            await ctx.send(file=discord.File(buf, f_file))

    @command(
        commands.command,
        hidden=True,
    )
    async def perf(self, ctx: Context, *, command: str):  # noqa
        """Checks the timing of a command, attempting to suppress HTTP and DB calls."""

        msg = copy.copy(ctx.message)
        msg.content = ctx.prefix + command

        new_ctx = await self.bot.get_context(msg, cls=type(ctx))

        new_ctx._state = PerformanceMocker()
        new_ctx.channel = PerformanceMocker()

        if new_ctx.command is None:
            return await ctx.send(f'{ctx.tick(False)} No command found')

        start = time.perf_counter()
        try:
            await new_ctx.command.invoke(new_ctx)
        except commands.CommandError:
            end = time.perf_counter()
            success = False
            try:
                await ctx.send(f'```py\n{traceback.format_exc()}\n```')
            except discord.HTTPException:
                pass
        else:
            end = time.perf_counter()
            success = True

        await ctx.send(f'Status: {ctx.tick(success)} Time: `{(end - start) * 1000:.2f}ms`')


async def setup(bot):
    await bot.add_cog(Admin(bot))
