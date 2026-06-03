import asyncio
import copy
import importlib
import io
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import discord
from aiohttp import FormData
from discord.ext import commands

from app.core import Bot, Cog, Context
from app.core.converter import CodeblockConverter
from app.core.models import command, group
from app.core.pagination import TextSourcePaginator
from app.utils import TabularData, pluralize, tail
from config import images_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from asyncpg import Record


class Admin(Cog):
    """Admin commands for the bot owner."""

    __hidden__ = True
    emoji = '<:originally_known_as:1322355070578327692>'

    async def run_process(self, command: str) -> list[str]:
        try:
            process = await asyncio.create_subprocess_shell(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await process.communicate()
        except NotImplementedError:
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            result = await self.bot.loop.run_in_executor(None, process.communicate)

        return [output.decode() for output in result]

    async def cog_check(self, ctx: Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    @group(invoke_without_command=True, guild_only=True)
    async def sync(self, ctx: Context, guild_id: int | None, copy: bool = False) -> None:
        """Syncs the slash commands with the given guild"""

        guild = discord.Object(id=guild_id) if guild_id else ctx.guild

        if copy:
            self.bot.tree.copy_global_to(guild=guild)

        commands = await self.bot.tree.sync(guild=guild)
        await ctx.send(f'Successfully synced {len(commands)} commands')

    @sync.command(name='global', guild_only=False)
    async def sync_global(self, ctx: Context) -> None:
        """Syncs the commands globally"""

        commands = await self.bot.tree.sync(guild=None)
        await ctx.send(f'Successfully synced {len(commands)} commands')

    @group(invoke_without_command=True, alias='rl')
    async def reload(self, ctx: Context) -> None:
        """Command group for module reloading purposes."""
        pass

    @reload.command(name='module', alias='m')
    async def reload_module(self, ctx: Context, name: str) -> None:
        """Reloads a non-cog module using importlib. (unsafe)"""
        try:
            _file = sys.modules[name]
        except KeyError:
            await ctx.send_error('Module not found.')
            return

        try:
            importlib.reload(_file)
        except Exception as e:
            await ctx.send_error(f'Failed to reload module: {e}')
            return

        await ctx.send_success(f'`{_file}` reloaded.')

    @command()
    async def pm(self, ctx: Context, user_id: int, *, content: str) -> None:
        """Sends a DM to a user by ID."""
        user = self.bot.get_user(user_id) or (await self.bot.fetch_user(user_id))

        message = (
            f'{content}\n\n'
            f'*This is a DM sent because you had previously requested feedback or I found a bug '
            'in a command you used, I do not monitor this DM.*'
        )
        try:
            await user.send(message)
        except discord.HTTPException:
            await ctx.send_error(f'Could not send a DM to {user}.')
        else:
            await ctx.send_success('PM successfully sent.')

    @group()
    async def images(self, ctx: Context) -> None:
        """Commands for image managing for https://klappstuhl.me."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @images.command(name='upload')
    async def images_upload(self, ctx: Context, file: discord.Attachment) -> None:
        """Uploads a file to https://klappstuhl.me."""
        if not file.content_type.startswith('image'):
            await ctx.send_error('Only images are allowed.')
            return

        async with ctx.typing():
            headers = {
                'Content-Type': 'multipart/form-data',
                'Authorization': images_key
            }
            data = FormData()
            data.add_field('file', await file.read())
            async with self.bot.session.post(
                    'https://klappstuhl.me/api/images/upload',
                    headers=headers,
                    data=data
            ) as resp:
                if resp.status == 200:
                    await ctx.send_success(f'Uploaded to <{(await resp.json())}>')
                else:
                    await ctx.send_error(f'Response: **{resp.status}**\n```json\n{await resp.json()}```')

    @images.command(name='delete')
    async def images_delete(self, ctx: Context, _id: str) -> None:
        """Deletes a file from https://klappstuhl.me."""
        async with ctx.typing():
            headers = {
                'Authorization': images_key
            }
            async with self.bot.session.delete(
                    f'https://klappstuhl.me/api/images/{_id}',
                    headers=headers,
            ) as resp:
                if resp.status == 200:
                    await ctx.send_success(f'Deleted [**{_id}**]')
                else:
                    await ctx.send_error(f'Failed to delete: **{resp.status}**\n```json\n{await resp.json()}```')

    @images.command(name='get')
    async def images_get(self, ctx: Context, _id: str) -> None:
        """Gets a file from https://klappstuhl.me."""
        async with ctx.typing():
            headers = {
                'Content-Type': 'multipart/form-data',
            }
            async with self.bot.session.get(
                    f'https://klappstuhl.me/gallery/raw/{_id}',
                    headers=headers
            ) as resp:
                if resp.status == 200:
                    file = discord.File(fp=io.BytesIO(await resp.read()), filename=resp.url.name)
                    await ctx.send_success(f'Image [**{resp.url.name}**]', file=file)
                else:
                    await ctx.send_error(f'Failed to get: **{resp.status}**\n```json\n{await resp.json()}```')

    @command(hidden=True, description='Lists current running tasks in the asyncio event loop.')
    @commands.is_owner()
    async def list_tasks(self, ctx: Context) -> None:
        """List all tasks."""
        _tasks = asyncio.all_tasks(loop=self.bot.loop)
        table = TabularData()
        table.set_columns(['Memory ID', 'Name', 'Object'])

        def strip_memory_id(s: str) -> str:
            return (s.split(' ')[-1])[:-1]

        table.add_rows(
            (strip_memory_id(str(task.get_coro())), task.get_name(), str(task.get_coro()).split(' ')[2]) for task in
            _tasks)
        rendered = table.render()
        rendered = re.sub(r'```\w?.*', '', rendered, re.RegexFlag.M)

        paginator = TextSourcePaginator(ctx, prefix='```ansi')
        for line in rendered.splitlines():
            paginator.add_line(line)
        await paginator.start()

    @staticmethod
    async def send_sql_results(ctx: Context, records: list[Any]) -> None:
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

    @group(
        hidden=True,
        description='Run some SQL queries.',
        iwc=True
    )
    async def sql(self, ctx: Context, *, query: Annotated[list[str], CodeblockConverter]) -> None:
        """Run some SQL."""
        query = '\n'.join(query)

        is_multistatement = query.count(';') > 1
        strategy: Callable[[str], Awaitable[list[Record]] | Awaitable[str]]
        strategy = ctx.db.execute if is_multistatement else ctx.db.fetch

        try:
            start = time.perf_counter()
            results = await strategy(query)
            dt = (time.perf_counter() - start) * 1000.0
        except Exception:
            await ctx.send(f'```py\n{traceback.format_exc()}\n```')
            return

        rows = len(results)
        if isinstance(results, str) or rows == 0:
            await ctx.send(f'`{dt:.2f}ms: {results}`')
            return

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
            fmt = f'```sql\n{render}\n```\n*Returned {pluralize(rows):row} in {dt:.2f}ms*'
            await ctx.send(fmt)

    @sql.command(name='schema')
    async def sql_schema(self, ctx: Context, *, table_name: str) -> None:
        """Runs a query describing the table schema."""
        results = await ctx.db.admin.get_table_schema(table_name)

        if len(results) == 0:
            raise commands.BadArgument(f'Table `{table_name}` not found')

        await self.send_sql_results(ctx, results)

    @sql.command(name='tables')
    async def sql_tables(self, ctx: Context) -> None:
        """Lists all SQL tables in the database."""
        results = await ctx.db.admin.list_tables()

        if len(results) == 0:
            raise commands.BadArgument('Could not find any tables')

        await self.send_sql_results(ctx, results)

    @sql.command(name='sizes')
    async def sql_sizes(self, ctx: Context) -> None:
        """Display how much space the database is taking up."""
        results = await ctx.db.admin.get_table_sizes()

        if len(results) == 0:
            await ctx.send_error('Could not find any tables')
            return

        await self.send_sql_results(ctx, results)

    @sql.command(name='explain', aliases=['analyze'])
    async def sql_explain(self, ctx: Context, *, query: Annotated[list[str], CodeblockConverter]) -> None:
        """Explain an SQL query."""
        query = '\n'.join(query)

        analyze = ctx.invoked_with == 'analyze'
        json = await ctx.db.admin.explain_query(query, analyze=analyze)
        if json is None:
            await ctx.send_error('No results.')
            return

        file = discord.File(io.BytesIO(json[0].encode('utf-8')), filename='explain.json')
        await ctx.send(file=file)

    @command()
    async def sudo(
            self,
            ctx: Context,
            channel: discord.TextChannel | None,
            who: discord.Member | discord.User,
            *,
            command: str,
    ) -> None:
        """Run a command as another user optionally in another channel."""
        msg = copy.copy(ctx.message)
        new_channel = channel or ctx.channel
        msg.channel = new_channel
        msg.author = who
        msg.content = ctx.prefix + command
        new_ctx = await self.bot.get_context(msg, cls=type(ctx))
        await self.bot.invoke(new_ctx)

    @command(name='showlog')
    async def showlog(self, ctx: Context, log: str = 'percy', last_lines: int = 600) -> None:
        """Shows the x last lines of a log file."""
        f_file = f'{log}.log'
        with Path(f_file).open('rb') as f:
            lines = tail(f, last_lines)
            buf = io.BytesIO()
            for line in lines:
                buf.write(line)
            buf.seek(0)
            await ctx.send(file=discord.File(buf, f_file))

    @command(name='guilds')
    async def guilds(self, ctx: Context) -> None:
        """Shows all guilds the bot is in."""
        guilds = len(self.bot.guilds)
        await ctx.send(f'`{guilds}`')


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
