import importlib
import sys
import traceback
from importlib.metadata import distribution, packages_distributions
from types import ModuleType, TracebackType
from typing import Optional, List, Any, Type, Union

import discord
import jishaku
import psutil
from discord.ext import commands
from jishaku.cog import OPTIONAL_FEATURES, STANDARD_FEATURES
from jishaku.features.baseclass import Feature
from jishaku.math import natural_size
from jishaku.modules import package_version

from cogs.utils import errors
from cogs.utils.context import Context
from cogs.utils.converters import ModuleConverter, get_asset_url
from cogs.utils.formats import plural
from cogs.utils.paginator import TextSource, EmbedPaginator

jishaku.Flags.NO_DM_TRACEBACK = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.HIDE = True


async def send_traceback(
        destination: Union[discord.abc.Messageable, discord.Message],
        verbosity: int,
        etype: Type[BaseException],
        value: BaseException,
        trace: TracebackType
):
    """Sends a traceback of an exception to a destination.

    Parameters
    ----------
    destination: Union[discord.abc.Messageable, discord.Message]
        The destination to send the traceback to.
    verbosity: int
        The amount of lines to send.
    etype: Type[BaseException]
        The type of exception.
    value: BaseException
        The exception itself.
    trace: TracebackType
        The traceback.
    """

    traceback_content = "".join(traceback.format_exception(etype, value, trace, verbosity)).replace("``",
                                                                                                    "`\u200b`")

    paginator = TextSource(prefix='```py')
    for line in traceback_content.split('\n'):
        paginator.add_line(line)

    message = None

    for page in paginator.pages:
        if isinstance(destination, discord.Message):
            message = await destination.reply(page)
        else:
            message = await destination.send(page)

    return message


class Jishaku(*OPTIONAL_FEATURES, *STANDARD_FEATURES):

    # noinspection PyProtectedMember
    @Feature.Command(parent='jsk', name='sync')
    async def jsk_sync(self, ctx: Context, *targets: str):
        """
        Sync global or guild application commands to Discord.
        """

        if not self.bot.application_id:
            raise errors.CommandError('Bot does not have an application ID.')

        guilds_set: set[Optional[int]] = {None}
        for target in targets:
            if target == '$':  # Sync commands to global
                guilds_set.add(None)
            elif target == '*':  # Sync commands to all guilds
                guilds_set |= set(self.bot.tree._guild_commands.keys())
            elif target == '.':  # Sync commands to the current guild
                if ctx.guild:
                    guilds_set.add(ctx.guild.id)
                else:
                    await ctx.stick(False, 'Can\'t sync guild commands without guild information')
                    return
            else:  # Sync commands to a specific guild
                try:
                    guilds_set.add(int(target))
                except ValueError as error:
                    raise errors.BadArgument(f'{target} is not a valid guild ID') from error

        if not targets:
            guilds_set.add(None)

        guilds: List[Optional[int]] = list(guilds_set)
        guilds.sort(key=lambda g: (g is not None, g))

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        embeds: List[discord.Embed] = [
            discord.Embed(title=f'\N{SATELLITE ANTENNA} Command Tree Guild Sync', description=""),
            discord.Embed(title=f'\N{GLOBE WITH MERIDIANS} Command Tree Global Sync',)
        ]

        for guild in guilds:
            slash_commands = self.bot.tree._get_all_commands(
                guild=discord.Object(guild) if guild else None
            )
            translator = getattr(self.bot.tree, 'translator', None)
            if translator:
                payload = [await command.get_translated_payload(translator) for command in slash_commands]
            else:
                payload = [command.to_dict() for command in slash_commands]

            try:
                if guild is None:
                    data = await self.bot.http.bulk_upsert_global_commands(self.bot.application_id, payload=payload)
                else:
                    data = await self.bot.http.bulk_upsert_guild_commands(self.bot.application_id, guild,
                                                                          payload=payload)

                synced = [discord.app_commands.AppCommand(data=d, state=ctx._state) for d in data]
            except discord.HTTPException as error:
                error_lines: List[str] = []
                for line in str(error).split('\n'):
                    error_lines.append(line)
                    try:
                        match = self.SLASH_COMMAND_ERROR.match(line)
                        if not match:
                            continue

                        pool = slash_commands
                        selected_command = None
                        name = ""
                        parts = match.group(1).split('.')
                        assert len(parts) % 2 == 0

                        for part_index in range(0, len(parts), 2):
                            index = int(parts[part_index])

                            if pool:
                                selected_command = pool[index]
                                name += selected_command.name + ' '

                                if hasattr(selected_command, '_children'):
                                    pool = list(selected_command._children.values())
                                else:
                                    pool = None
                            else:
                                param = list(selected_command._params.keys())[index]
                                name += f'(parameter: {param}) '

                        if selected_command:
                            to_inspect: Any = None

                            if hasattr(selected_command, 'callback'):  # type: ignore
                                to_inspect = selected_command.callback  # type: ignore
                            elif isinstance(selected_command, commands.Cog):
                                to_inspect = type(selected_command)

                            try:
                                error_lines.append(''.join([
                                    '\N{MAGNET} This is likely caused by: `',
                                    name,
                                    '` at ',
                                    str(inspections.file_loc_inspection(to_inspect)),  # type: ignore
                                    ':',
                                    str(inspections.line_span_inspection(to_inspect)),  # type: ignore
                                ]))
                            except Exception:
                                error_lines.append(f'\N{MAGNET} This is likely caused by: `{name}`')
                    except Exception as diag_error:
                        error_lines.append(
                            f'\N{MAGNET} Couldn\'t determine cause: {type(diag_error).__name__}: {diag_error}')

                error_text = '\n'.join(error_lines)

                if guild:
                    source.add_line(f'\N{WARNING SIGN} `{guild}`: {error_text}', empty=True)
                else:
                    source.add_line(f'\N{WARNING SIGN} Global: {error_text}', empty=True)

                embed = discord.Embed(title='Slash Command Sync Failed')
                for page in source.pages:
                    embed.description = page
                    embeds.append(embed)
            else:
                if guild:
                    embeds[0].description += f'\n- `{guild}` (*{len(synced)} commands*)'
                else:
                    embeds[1].description = f'Synced total global {plural(len(synced)):command}'

        await EmbedPaginator.start(ctx, entries=[
            embed for embed in embeds if embed.description or embed.description != ""
        ])

    @Feature.Command(
        parent='jsk',
        name='mrl',
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def reload_module(self, ctx: commands.Context, *, module: ModuleConverter):
        """Reloads a module."""

        assert isinstance(module, ModuleType)
        icon = '\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}'

        try:
            importlib.reload(module)
        except Exception as exc:
            await ctx.send(f'{icon}\N{WARNING SIGN} `{module.__name__}` was not reloaded.\n')
            return await send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)
        await ctx.send(f'{icon} `{module.__name__}` was reloaded successfully.')

    @Feature.Command(
        parent='jsk',
        name='ml',
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def load_module(self, ctx: commands.Context, *, module: str):
        """Reloads a module."""

        icon = '\N{INBOX TRAY}'
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            return await ctx.send(f'{icon}\N{WARNING SIGN} `{module!r}` is not a valid module.')
        except Exception as exc:
            await ctx.send(f'{icon}\N{WARNING SIGN} `{module}` was not loaded.')
            return await send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)
        await ctx.send(f'{icon} `{module}` was loaded successfully.')

    @Feature.Command(
        parent='jsk',
        name='mul',
        invoke_without_commad=True,
        ignore_extra=False
    )
    async def unload_module(self, ctx: commands.Context, *, module: ModuleConverter):
        """Reloads a module."""

        assert isinstance(module, ModuleType)
        icon = '\N{OUTBOX TRAY}'

        try:
            del sys.modules[module.__name__]
        except KeyError:
            return await ctx.send(f'{icon}\N{WARNING SIGN} `{module.__name__}` was not found.')
        except Exception as exc:
            return await send_traceback(ctx.channel, 8, type(exc), exc, exc.__traceback__)

        await ctx.send(f'{icon} `{module.__name__}` was unloaded successfully.')

    @Feature.Command(
        name='jishaku',
        aliases=['jsk'],
        invoke_without_command=True,
        ignore_extra=False
    )
    async def jsk(self, ctx: commands.Context):
        """
        The Jishaku debug and diagnostic commands.
        This command on its own gives a status brief.
        All other functionality is within its subcommands.
        """

        distributions: List[str] = [
            dist
            for dist in packages_distributions()['discord']
            if any(
                file.parts == ('discord', '__init__.py')
                for file in distribution(dist).files
            )
        ]

        if distributions:
            dist_version = f'{distributions[0]} `{package_version(distributions[0])}`'
        else:
            dist_version = f'unknown `{discord.__version__}`'

        summary = [
            f'Jishaku `v{package_version('jishaku')}`, {dist_version}, '
            f'Python `{sys.version}` on `{sys.platform}`'.replace('\n', ""),
            f'Module was loaded <t:{self.load_time.timestamp():.0f}:R>, '
            f'cog was loaded <t:{self.start_time.timestamp():.0f}:R>.',
            "",
        ]

        if psutil:
            try:
                proc = psutil.Process()

                with proc.oneshot():
                    try:
                        mem = proc.memory_full_info()
                        summary.append(
                            f'Using `{natural_size(mem.rss)}` physical memory and '
                            f'`{natural_size(mem.vms)}` virtual memory, '
                            f'`{natural_size(mem.uss)}` of which unique to this process.'
                        )
                    except psutil.AccessDenied:
                        pass

                    try:
                        name = proc.name()
                        pid = proc.pid
                        thread_count = proc.num_threads()

                        summary.append(
                            f'Running on PID `{pid}` (`{name}`) with `{thread_count}` thread(s).'
                        )
                    except psutil.AccessDenied:
                        pass

                    summary.append("")
            except psutil.AccessDenied:
                summary.append(
                    'psutil is installed, but this process does not have high enough access rights '
                    'to query process information.'
                )
                summary.append("")  # blank line
        s_for_guilds = "" if len(self.bot.guilds) == 1 else 's'
        s_for_users = "" if len(self.bot.users) == 1 else 's'
        cache_summary = f'`{len(self.bot.guilds)}` guild{s_for_guilds} and `{len(self.bot.users)}` user{s_for_users}'

        if isinstance(self.bot, discord.AutoShardedClient):
            if len(self.bot.shards) > 20:
                summary.append(
                    f'This bot is automatically sharded (`{len(self.bot.shards)}` shards of `{self.bot.shard_count}`)'
                    f' and can see {cache_summary}.'
                )
            else:
                shard_ids = ', '.join(str(i) for i in self.bot.shards.keys())
                summary.append(
                    f'This bot is automatically sharded (Shards `{shard_ids}` of `{self.bot.shard_count}`)'
                    f' and can see {cache_summary}.'
                )
        elif self.bot.shard_count:
            summary.append(
                f'This bot is manually sharded (Shard `{self.bot.shard_id}` of `{self.bot.shard_count}`)'
                f' and can see {cache_summary}.'
            )
        else:
            summary.append(f'This bot is not sharded and can see {cache_summary}.')

        if self.bot._connection.max_messages:  # type: ignore
            message_cache = f'Message cache capped at `{self.bot._connection.max_messages}`'
        else:
            message_cache = 'Message cache is disabled'

        remarks = {True: 'enabled', False: 'disabled', None: 'unknown'}

        *group, last = (
            f'{intent.replace('_', ' ')} intent is {remarks.get(getattr(self.bot.intents, intent, None))}'
            for intent in ('presences', 'members', 'message_content')
        )

        summary.append(f'{message_cache}, {', '.join(group)}, and {last}.')

        summary.append(
            f'Average websocket latency: `{round(self.bot.latency * 1000, 2)} ms`'
        )

        embed = discord.Embed(description='\n'.join(summary), color=0x2b2d31)
        embed.set_author(name=ctx.bot.user.name, icon_url=get_asset_url(ctx.bot.user))

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Jishaku(bot=bot))
