from __future__ import annotations

import asyncio
import datetime
import logging
import re
import sys
import time as _time
import traceback
from collections import Counter, defaultdict
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Final, TypeVar

import discord
import jishaku
import wavelink
from aiohttp import ClientSession
from discord.ext import commands
from discord.http import Route
from discord.utils import MISSING
from expiringdict import ExpiringDict

from app.cogs import EXTENSIONS
from app.core.command import Command, GroupCommand
from app.core.context import Context
from app.core.flags import FlagMeta
from app.core.help import PaginatedHelpCommand
from app.core.models import AppBadArgument
from app.core.pagination import TextSource
from app.core.permissions import PermissionSpec
from app.core.feature_flags import FeatureFlags
from app.core.spam import SpamControl
from app.i18n import I18n
from app.core.timer import Timer, TimerManager
from app.core.tree import CommandTree
from app.core.views import CommandSuggestionView
from app.database.base import Database
from app.internal_api import InternalAPI
from app.rendering import RenderingService
from app.utils.metrics import MetricsCollector
from app.utils import (
    GUILD_FEATURES,
    AnsiColor,
    AnsiStringBuilder,
    Config,
    cache,
    deep_to_with,
    fuzzy,
    helpers,
    humanize_duration,
)
from app.utils.lock import LockedResourceError
from app.utils.types import RPCAppInfo, RPCAppInfoPayload
from config import (
    Emojis,
    allowed_mentions,
    beta,
    default_prefix,
    description,
    lavalink_nodes,
    owners,
    resolved_token,
    stats_webhook,
    test_guild_id,
    version,
)
from config import (
    name as bot_name,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Generator, Iterable

GuildFeatureT = TypeVar('GuildFeatureT', bound=list[tuple[str, str]] | Any)

__all__ = (
    'LOG',
    'Bot',
)

LOG: Final[logging.Logger] = logging.getLogger(bot_name)


class Bot(commands.Bot):
    """Represents Percy as a bot.

    At its core, this handles and/or sends all events and payloads
    to and from Discord's API.
    """

    log: Final[logging.Logger] = LOG

    bypass_checks: bool
    bot_app_info: discord.AppInfo
    db: Database
    session: ClientSession
    startup_timestamp: datetime.datetime
    context: type[Context]
    timers: TimerManager
    render: RenderingService
    spam_control: SpamControl
    command_stats: Counter[str]
    socket_stats: Counter[str]
    command_types_used: Counter[bool]
    log_handler: logging.Handler
    internal_api: InternalAPI
    metrics: MetricsCollector
    feature_flags: FeatureFlags
    i18n: I18n

    #: Whether application-command IDs have been resolved onto command objects (for
    #: ``Command.mention``). Set by :meth:`resolve_app_command_ids`, cleared on re-sync.
    _app_command_ids_resolved: bool = False

    if TYPE_CHECKING:
        blacklist: Config[int, bool]
        temp_channels: Config[int, bool]
        doc_links: Config[str, dict[str, str | list[str]] | list[str]]

    # final due to no use being changed on runtime
    INTENTS: Final[discord.Intents] = discord.Intents(
        emojis_and_stickers=True,
        guilds=True,
        bans=True,
        members=True,
        presences=True,
        messages=True,
        message_content=True,
        reactions=True,
        voice_states=True,
    )

    def __init__(self) -> None:
        owner_kwargs: dict[str, Any]
        owner_kwargs = {'owner_id': owners} if isinstance(owners, int) else {'owner_ids': owners}

        super().__init__(
            command_prefix=self.__class__.resolve_command_prefix,
            help_command=PaginatedHelpCommand(),
            description=description,
            case_insensitive=True,
            allowed_mentions=allowed_mentions,
            tree_cls=CommandTree,
            intents=self.INTENTS,
            # status=discord.Status.dnd,
            max_messages=10,
            **owner_kwargs
        )

        def _make_command_cache_key(ctx: Context) -> str:
            return f'{ctx.now.timestamp()}:{ctx.author.id}:{ctx.command}'

        self.make_command_cache_key: Callable[[Context], str] = _make_command_cache_key
        self.command_error_cache: dict[str, str] = ExpiringDict(
            max_len=1000, max_age_seconds=60)

        self.resumes: defaultdict[int, list[datetime.datetime]] = defaultdict(list)
        self.identifies: defaultdict[int, list[datetime.datetime]] = defaultdict(list)

        self.context: type[Context] = Context
        self.spam_control: SpamControl = SpamControl(self)
        self.metrics: MetricsCollector = MetricsCollector()
        self.feature_flags: FeatureFlags = FeatureFlags()
        self.i18n: I18n = I18n()

        self.initial_extensions: list[str] = EXTENSIONS

    async def resolve_command_prefix(self, message: discord.Message) -> list[str]:
        """Resolves the command prefix for a message, respecting per-guild configuration."""
        if beta:
            return commands.when_mentioned_or('b.')(self, message)

        if not message.guild:
            return commands.when_mentioned_or(default_prefix)(self, message)

        config = await self.db.get_guild_config(guild_id=message.guild.id)
        if config is None:
            return commands.when_mentioned_or(default_prefix)(self, message)

        if not config.prefixes:
            return commands.when_mentioned(self, message)

        prefixes = sorted(config.prefixes, key=len, reverse=True)
        return commands.when_mentioned_or(*prefixes)(self, message)

    async def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        await self.load_extension('jishaku')

        DoNotLoadOnBeta = (
            'app.cogs.web_utils',
            'app.cogs.comic'
        )
        for extension in self.initial_extensions:
            if beta and extension in DoNotLoadOnBeta:
                continue
            try:
                await self.load_extension(extension)
            except Exception as exc:
                self.log.critical('Failed to load extension %s: %s', extension, exc, exc_info=True)
            else:
                self.log.debug('Loaded extension: %s', extension)

    async def reload_extension(self, name: str, *, package: str | None = None) -> None:
        """Reloads an extension."""
        await super().reload_extension(name, package=package)
        self.prepare_jishaku_flags()

    def add_command(self, command: Command, /) -> None:
        # Resolves custom flags to work with the command.
        if isinstance(command, Command):
            command.transform_flag_parameters()

        if isinstance(command, GroupCommand):
            for child in command.walk_commands():
                if isinstance(child, Command):
                    child.transform_flag_parameters()  # type: ignore

        super().add_command(command)

    async def setup_hook(self) -> None:
        """Prepares the bot for startup."""
        self.prepare_jishaku_flags()

        self.bot_app_info = await self.application_info()

        self.blacklist = Config('blacklist')
        self.temp_channels = Config('temp_channels.json')
        self.doc_links = Config('doc_links.json')

        self.bypass_checks = False
        self.db = await Database(self, loop=self.loop).wait()
        self.session = ClientSession()
        self.timers = TimerManager(self)
        self.render = RenderingService()

        self._setup_task = asyncio.ensure_future(self._setup_hook_task())

    @staticmethod
    def prepare_jishaku_flags() -> None:
        jishaku.Flags.HIDE = True
        jishaku.Flags.NO_UNDERSCORE = True
        jishaku.Flags.NO_DM_TRACEBACK = True

    async def _setup_hook_task(self) -> None:
        try:
            await wavelink.Pool.connect(
                nodes=[
                    wavelink.Node(uri=ns.uri, password=ns.password, retries=2) for ns in lavalink_nodes
                ],
                client=self,
                cache_capacity=100
            )
        except Exception as exc:
            self.log.error('Failed to connect to Lavalink:', exc_info=exc)

        try:
            self.internal_api = InternalAPI(self)
            await self.internal_api.start()
        except Exception as exc:
            self.log.error('Failed to start internal API:', exc_info=exc)

        await self._load_extensions()

        if test_guild_id is not None:
            self.tree.copy_global_to(guild=discord.Object(id=test_guild_id))

    async def get_context(
            self,
            origin: discord.Message | discord.Interaction,
            /,
            *,
            cls: type[Context] = Context,
    ) -> Context:  # type: ignore[override]
        return await super().get_context(origin, cls=cls)

    def get_slash_command_payloads(self, shortened: bool = False) -> list[dict]:
        """Return the application (slash) command payloads for all registered app commands.

        This iterates over the bot's commands and collects the app command representation
        (as produced by discord.app_commands Command.to_dict) for any hybrid/group commands
        that declare an .app_command attribute.
        """
        payloads: list[dict] = []
        for cmd in self.walk_commands():
            app_cmd = getattr(cmd, "app_command", None)
            if app_cmd is None:
                continue

            try:
                payload = app_cmd.to_dict(tree=self.tree)
            except Exception:
                app_cmd = getattr(app_cmd, "app_command", None)
                if app_cmd is None:
                    continue
                payload = app_cmd.to_dict(tree=self.tree)

            if shortened:
                payload = {
                    "name": app_cmd.qualified_name,
                    "description": payload["description"]
                }

            payloads.append(payload)
        return payloads

    async def resolve_app_command_ids(self, *, guild: discord.abc.Snowflake | None = None) -> None:
        """Tag command objects with their synced app-command ID so ``Command.mention`` works.

        Fetches the registered slash commands from Discord — global *and* the given guild,
        to cover both global- and guild-synced setups — and assigns each command (and its
        subcommands) the ID of its top-level application command. Idempotent: guarded by
        ``_app_command_ids_resolved`` and re-run after a sync clears that flag.
        """
        if self._app_command_ids_resolved:
            return

        fetched: list[discord.app_commands.AppCommand] = []
        with suppress(discord.HTTPException):
            fetched.extend(await self.tree.fetch_commands())
        if guild is not None:
            with suppress(discord.HTTPException):
                fetched.extend(await self.tree.fetch_commands(guild=guild))

        id_by_name = {cmd.name: cmd.id for cmd in fetched if cmd.type is discord.AppCommandType.chat_input}
        for command in self.walk_commands():
            if isinstance(command, Command):
                top_level = command.qualified_name.split(' ', 1)[0]
                command._app_command_id = id_by_name.get(top_level)

        self._app_command_ids_resolved = True

    async def process_commands(self, message: discord.Message) -> None:
        ctx = await self.get_context(message)

        if ctx.author.id in self.blacklist:
            return

        if ctx.guild is not None and ctx.guild.id in self.blacklist:
            return

        if ctx.command is None:
            # No command matched. discord.py only raises CommandNotFound from invoke(),
            # which we skip here — so handle the "did you mean?" suggestion directly. Only
            # act when the user actually used the prefix (ctx.invoked_with is set).
            if ctx.invoked_with:
                await self._maybe_suggest_command(ctx)
            return

        if await self.spam_control.is_spam(ctx, message):
            return

        cog_name = ctx.command.cog.qualified_name if ctx.command.cog else None
        if self.feature_flags.is_disabled(ctx.command.qualified_name, cog_name):
            await ctx.send_info('This command is temporarily disabled.', delete_after=10)
            return

        start = _time.perf_counter()
        await self.invoke(ctx)
        duration_ms = (_time.perf_counter() - start) * 1000

        self.metrics.record_command(
            ctx.command.qualified_name if ctx.command else "unknown",
            duration_ms,
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
            success=not ctx.command_failed,
        )

    async def on_shard_resumed(self, shard_id: int) -> None:
        self.log.info('Shard ID %s has resumed...', shard_id)
        self.resumes[shard_id].append(discord.utils.utcnow())

    async def on_ready(self) -> None:
        assert self.user is not None
        if not hasattr(self, 'startup_timestamp'):
            self.startup_timestamp = discord.utils.utcnow()

            text = f'Ready as {self.user} ({self.user.id})'
            center = f' {bot_name} v{version} '

            print(format(center, f'=^{len(text)}'))
            print(text)

            self.log.info('Gateway received READY @ %s', self.startup_timestamp)
        else:
            self.log.info('Ready as %s (ID: %s)', self.user, self.user.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id in self.blacklist:
            await guild.leave()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if await self.db.get_guild_config(guild_id=guild.id):
            await self.db.guilds.delete_config(guild.id)

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        (exc_type, exc, tb) = sys.exc_info()
        blacklist = (
            commands.CommandInvokeError, LockedResourceError, discord.Forbidden, discord.NotFound,
            commands.ConversionError
        )
        if isinstance(exc, blacklist):
            return

        trace = ''.join(traceback.format_exception(exc_type, exc, tb))
        embed = discord.Embed(
            title=f'{Emojis.warning} Event Error',
            description=f'```py\n{trace}\n```',
            timestamp=discord.utils.utcnow(),
            colour=helpers.Colour.burgundy()
        )
        embed.add_field(name='Event', value=event_method)
        embed.set_footer(text='Occurred at')

        args_str = TextSource(prefix='```py', max_size=1024)
        for index, arg in enumerate(args):
            args_str.add_line(f'[{index}]: {arg!r}')
        args_str.close_page()
        embed.add_field(name='Args', value=args_str.pages[0], inline=False)

        with suppress(discord.HTTPException, ValueError):
            ctx: Context | discord.Message | discord.Member = args[0]

            if isinstance(ctx, Context):
                author = ctx.author
                send = ctx.send
            elif isinstance(ctx, discord.Message):
                author = ctx.author
                send = ctx.channel.send
            elif isinstance(ctx, discord.Member):
                author = ctx
                member = ctx
                await member.create_dm()
                assert member.dm_channel is not None
                send = member.dm_channel.send
            else:
                raise ValueError

            if await self.is_owner(author):
                await send(embed=embed)
                return

            await self.stats_webhook.send(embed=embed)

    #: Minimum fuzzy ``ratio`` (0-100) for a mistyped command to earn a "did you mean?"
    #: suggestion. Tuned so a clear typo of a real command (``balanace`` -> ``balance``)
    #: matches while an unrelated word (``baldheu``) stays silent, as before.
    SUGGESTION_CUTOFF: Final[int] = 75

    async def _maybe_suggest_command(self, ctx: Context) -> None:
        """Reply with a single close command suggestion for a mistyped command.

        Stays silent unless the attempted name is a *very* close fuzzy match for a
        visible command, so unknown text behaves exactly as before (no reply).
        """
        attempted = (ctx.invoked_with or '').lower()
        # Only handle clean top-level misses; ignore 1-2 char noise to avoid false hits.
        if ctx.command is not None or len(attempted) < 3:
            return

        # Map every visible command name/alias to its command, then take the best match.
        choices: dict[str, Command] = {}
        for cmd in self.commands:
            if cmd.hidden:
                continue
            for name in (cmd.name, *cmd.aliases):
                choices.setdefault(name.lower(), cmd)  # type: ignore[arg-type]

        match = fuzzy.extract_one(attempted, choices, scorer=fuzzy.ratio, score_cutoff=self.SUGGESTION_CUTOFF)
        if match is None:
            return

        _, _, command = match
        suggestion = command.qualified_name
        prefix = ctx.prefix or ''
        rest = ctx.message.content[len(prefix) + len(ctx.invoked_with or ''):]
        new_content = f'{prefix}{suggestion}{rest}'

        view = CommandSuggestionView(ctx, suggestion, new_content)
        with suppress(discord.HTTPException):
            view.message = await ctx.send(
                view=view,
                reference=ctx.message,
                delete_after=15,
            )

    async def on_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        """|coro|

        The default command error handler provided by the bot.
        This is used for all command errors (including interaction command errors by redirecting them).

        This includes the ANSI back trace handler for commands.

        Parameters
        ----------
        ctx: Context
            The invocation context.
        error: Exception
            The error that was raised.
        """
        error = getattr(error, 'original', error)

        if ctx is MISSING:  # currently for user installed app commands cause ctx is not passed here
            self.log.critical('`ctx` is MISSING: Uncaught error when invoking a command: %s', error, exc_info=error)
            return

        self.command_error_cache[self.make_command_cache_key(ctx)] = f'{error.__class__.__name__}: {error}'
        self.metrics.record_error(type(error).__name__)

        # CommandNotFound is handled earlier in process_commands (an unmatched command never
        # reaches invoke(), so it is never raised here); CheckFailure/Forbidden stay silent.
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure, discord.Forbidden)):
            return

        if isinstance(error, commands.CommandOnCooldown):
            if not ctx.guild and ctx.bot_permissions.add_reactions:
                await ctx.message.add_reaction('\U000023f3')
                return

            retry_str = humanize_duration(error.retry_after)
            cooldown = error.cooldown
            msg = f'Slow down, you\'re on cooldown. Retry again in **{retry_str}**.'
            if cooldown.rate > 1:
                msg += f'\n-# This command allows {cooldown.rate} uses per {humanize_duration(cooldown.per)}.'
            await ctx.send_warning(msg)
            return
        if isinstance(error, commands.NSFWChannelRequired):
            await ctx.send(
                '\N{NO ENTRY SIGN} This command can only be run in channels that are marked **NSFW**.',
                reference=ctx.message, delete_after=15, ephemeral=True,
            )
            return

        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            if isinstance(error, commands.MissingPermissions):
                message = 'You are missing the following permissions required to run this command:'
            else:
                message = 'I am missing the following permissions required to execute this command:'

            missing = '\n'.join(f'- {PermissionSpec.permission_as_str(perm)}' for perm in error.missing_permissions)
            message += '\n' + missing

            permissions = ctx.bot_permissions
            if ctx.guild and (
                    permissions.administrator or (permissions.send_messages and permissions.read_message_history)
            ):
                await ctx.send(message, reference=ctx.message, ephemeral=True)
                return

            if permissions.administrator or permissions.add_reactions:
                await ctx.message.add_reaction('\U000026a0')

            with suppress(discord.HTTPException):
                await ctx.author.send(message)
            return

        # Service outage errors get a distinct, gentler tone.
        from app.clients.base import CircuitBreakerOpen, HTTPClientError
        from app.core.errors import ServiceUnavailableError

        unwrapped = getattr(error, 'original', error)
        if isinstance(unwrapped, (CircuitBreakerOpen, ServiceUnavailableError)):
            service = getattr(unwrapped, 'service_name', 'external service')
            retry = getattr(unwrapped, 'retry_after', None)
            msg = f'The **{service}** service is temporarily unavailable.'
            if retry:
                msg += f' Try again in **{humanize_duration(retry)}**.'
            else:
                msg += ' Please try again shortly.'
            await ctx.send_info(msg, delete_after=20)
            return

        if isinstance(unwrapped, HTTPClientError) and unwrapped.status >= 500:
            await ctx.send_info(
                'An external service returned an error. This is likely temporary — please try again shortly.',
                delete_after=20,
            )
            return

        # Look for errors we send directly into the channel.
        to_send_error_lookup = deep_to_with(error, '__cause__')
        to_send = (
            commands.MaxConcurrencyReached, LockedResourceError, commands.TooManyArguments,
            commands.FlagError, AssertionError
        )
        if isinstance(to_send_error_lookup, to_send):
            # We want to get the original error type and not some
            # wrapped error like CommandInvokeError etc.
            content = str(to_send_error_lookup)
            if not content.startswith('<:'):
                content = f'{Emojis.error} {content}'
            await ctx.send(content, reference=ctx.message, delete_after=15, ephemeral=True)
            return

        error = getattr(error, 'original', error)

        # Parameter-based errors.

        command: Command = ctx.command  # type: ignore

        if isinstance(error, (commands.BadArgument, AppBadArgument)):
            command.reset_cooldown(ctx)
            param = ctx.current_parameter
            # Search for a given "namespace" parameter in the :class:`.BadArgument`. -> See /app/core/models.py
            if hasattr(error, 'namespace'):
                _namespace = error.namespace
                if _namespace in command.clean_params:
                    param = command.clean_params[_namespace]  # type: ignore[arg-type]
        elif hasattr(error, 'param'):
            param = error.param
        else:
            if not await self.is_owner(ctx.author):
                self.log.critical('Uncaught error when invoking %s: %s', command.name, error, exc_info=error)

                builder = AnsiStringBuilder()
                builder.append(f'panic!({error})', color=AnsiColor.red, bold=True)
                ansi = builder.ensure_codeblock().dynamic(ctx)
                await ctx.send(ansi, reference=ctx.message)
            raise error

        builder = AnsiStringBuilder()
        builder.append('Attempted to parse command signature:').newline(2)
        builder.append((' ' * 4) + ctx.clean_prefix, color=AnsiColor.white, bold=True)

        if ctx.interaction:
            invoked_with = command.qualified_name + ' ' + (ctx.invoked_with or '')
        else:
            if ctx.invoked_parents and ctx.invoked_subcommand:
                invoked_with = ' '.join([*ctx.invoked_parents, ctx.invoked_with or ''])
            elif ctx.invoked_parents:
                invoked_with = ' '.join(ctx.invoked_parents)
            else:
                invoked_with = ctx.invoked_with or ''

        builder.append(invoked_with + ' ', color=AnsiColor.green, bold=True)

        signature = Command.ansi_signature_of(command)
        builder.extend(signature)
        signature_raw = signature.raw

        FLAG_PARAM_REGEX = re.compile(
            fr'[<\[](--)?{re.escape(param.name)}((=.*)?| [<\[]\w+(\.{{3}})?[>\]])(\.{{3}})?[>\]](\.{{3}})?')
        if match := FLAG_PARAM_REGEX.search(signature_raw):
            lower, upper = match.span()
        elif isinstance(param.annotation, FlagMeta):
            stored_params = command.params
            old_params = command.params.copy()

            # Remove the parameter from the signature that stores the custom flags
            # because we don't want to show it if we display all flags invidivually.
            flag_key = next(key for key, value in stored_params.items() if value.annotation is command.custom_flags)

            del stored_params[flag_key]
            lower = len(command.signature) + 1

            command.params = old_params
            del stored_params

            upper = len(command.ansi_signature.raw) - 1
        else:
            lower, upper = 0, len(command.ansi_signature.raw) - 1

        builder.newline()

        offset = len(ctx.clean_prefix) + len(str(invoked_with))
        content = f'{" " * (lower + offset + 5)}{"^" * (upper - lower)} Error occurred here'
        builder.append(content, color=AnsiColor.gray, bold=True).newline(2)

        # check if the missing argument is the flags builder
        if isinstance(error, commands.MissingRequiredArgument):
            if isinstance(param.annotation, FlagMeta):
                # we want to give a hint, that displays the flags that are required and display them
                flags = [flag for flag in param.annotation.walk_flags() if flag.required is True]
                builder.append('Missing required flags: ' + ', '.join(flag.name for flag in flags), color=AnsiColor.red, bold=True)
            else:
                # check if there is documentation (description) for the missing parameter, if yes, then add it!
                builder.append(f'Missing required argument: {param.name}', color=AnsiColor.red, bold=True)
                if param.description:
                    builder.newline(2).append(f'{" " * 4}Hint: {param.description}', color=AnsiColor.yellow, bold=True)
        else:
            builder.append(str(error), color=AnsiColor.red, bold=True)

        if invoked_with != command.qualified_name:
            builder.newline(2)
            builder.append('Hint: ', color=AnsiColor.white, bold=True)

            builder.append('command alias ')
            builder.append(repr(invoked_with), color=AnsiColor.cyan, bold=True)
            builder.append(' points to ')
            builder.append(command.qualified_name, color=AnsiColor.green, bold=True)
            builder.append(', is this correct?')

        ansi = builder.ensure_codeblock().dynamic(ctx)
        await ctx.send_error(f'Could not parse your command input properly:\n{ansi}', reference=ctx.message)

    async def on_blacklist_timer_complete(self, timer: Timer) -> None:
        """Called when a blacklist timer completed.

        .. versionadded:: 2.0.0

        Parameters
        ----------
        timer: Timer
            The timer that completed.
        """
        object_id = timer['object_id']

        if object_id:
            await self.remove_from_blacklist(discord.Object(id=int(object_id)))

    # UTILS

    @staticmethod
    def get_guild_features(
            features: list[GuildFeatureT], *, only_current: bool = False, emojize: bool = True
    ) -> Generator[tuple[str, Any] | tuple[GuildFeatureT, Any], Any, None]:
        """Returns a list of tuples containing all guild features if ``only_current`` is False or enabled features if True.

        Parameters
        ----------
        features: list[GuildFeatureT]
            The list of features to get.
        only_current: bool
            Whether to only get the current enabled features.
        emojize: bool
            Whether to emojize the feature names.

        Returns
        -------
        GuildFeatureA
            The list of tuples containing the features.
        """
        for feature in features:
            if only_current:
                if feature in GUILD_FEATURES:
                    fmt = GUILD_FEATURES[feature]
                    if emojize:
                        yield f'{fmt[0]} {feature}', fmt[1]
                    else:
                        yield feature, fmt[1]
            else:
                fmt = GUILD_FEATURES[feature]
                if emojize:
                    yield f'{fmt[0]} {feature}', fmt[1]
                else:
                    yield feature, fmt[1]

    @staticmethod
    async def get_or_fetch_member(guild: discord.Guild, member_id: int) -> discord.Member | None:
        """|coro|

        Look up a member in cache or fetches if not found.

        Parameters
        ----------
        guild: Guild
            The guild to look in.
        member_id: int
            The member ID to search for.

        Returns
        -------
        Member
            The member or None if not found.
        """
        member = guild.get_member(member_id)
        if member is not None:
            return member

        try:
            member = await guild.fetch_member(member_id)
        except discord.HTTPException:
            pass
        else:
            return member

        members = await guild.query_members(limit=1, user_ids=[member_id], cache=True)
        if not members:
            return None
        return members[0]

    @staticmethod
    async def resolve_member_ids(guild: discord.Guild, member_ids: Iterable[int]) -> AsyncIterator[discord.Member]:
        """|coro|

        Bulk resolves member IDs to member instances, if possible.
        Members that can't be resolved are discarded from the list.
        This is done lazily using an asynchronous iterator.
        Note that the order of the resolved members is not the same as the input.

        Parameters
        ----------
        guild: Guild
            The guild to resolve from.
        member_ids: Iterable[int]
            An iterable of member IDs.

        Yields
        -------
        Member
            The resolved members.
        """
        needs_resolution = []
        for member_id in member_ids:
            member = guild.get_member(member_id)
            if member is not None:
                yield member
            else:
                needs_resolution.append(member_id)

        total_need_resolution = len(needs_resolution)
        if total_need_resolution != 0:
            if total_need_resolution == 1:
                members = await guild.query_members(limit=1, user_ids=needs_resolution, cache=True)
                if members:
                    yield members[0]
            elif total_need_resolution <= 100:
                resolved = await guild.query_members(limit=100, user_ids=needs_resolution, cache=True)
                for member in resolved:
                    yield member
            else:
                for index in range(0, total_need_resolution, 100):
                    to_resolve = needs_resolution[index: index + 100]
                    members = await guild.query_members(limit=100, user_ids=to_resolve, cache=True)
                    for member in members:
                        yield member

    @cache.cache()
    def find_member_from_user(self, user: discord.abc.Snowflake) -> discord.Member | None:
        """Finds the first member object given a user/object.

        Note that the guild the returned member is associated, to will be a random guild.
        Returns ``None`` if the user is not in any mutual guilds.
        """
        if isinstance(user, discord.Member):
            return user

        for guild in self.guilds:
            if member := guild.get_member(user.id):
                return member

        return None

    def user_on_mobile(self, user: discord.abc.Snowflake) -> bool | None:
        """Whether this user object is on mobile.

        If there are no mutual guilds for this user, then this will return `None`.
        Because ``None`` is a falsy value, this will behave as if it defaults to ``False``.
        """
        member = self.find_member_from_user(user=user)
        if member is not None:
            return member.is_on_mobile()

        return None

    async def fetch_application(self, application_id: int) -> RPCAppInfo:
        """|coro|

        Retrieves the application information from the /rpc endpoint.

        Parameters
        ----------
        application_id: Snowflake
            The application ID to retrieve.

        Raises
        -------
        HTTPException
            Retrieving the information failed somehow.

        Returns
        --------
        :class:`.AppInfo`
            The application's information.
        """
        data: RPCAppInfoPayload = await self.http.request(
            Route(
                'GET',
                '/oauth2/applications/{application_id}/rpc',
                application_id=application_id
            )
        )
        return RPCAppInfo(state=self._connection, data=data)

    @discord.utils.cached_property
    def stats_webhook(self) -> discord.Webhook:
        """:class:`discord.Webhook`: The stats webhook for the bot."""
        wh_id, wh_token = stats_webhook
        hook = discord.Webhook.partial(id=wh_id, token=str(wh_token), session=self.session)
        return hook

    async def add_to_blacklist(self, obj: discord.abc.Snowflake, *, duration: int | None = None) -> None:
        """|coro|

        Adds an object to the bot's blacklist.
        This supports both users and guilds.

        .. versionchanged:: 2.0.0
            The duration parameter was added.

        Parameters
        ----------
        obj: Snowflake
            The object to add.
        duration: int
            The duration to add the object for.
        """
        if duration is not None:
            when = datetime.datetime.now() + datetime.timedelta(seconds=duration)
            await self.timers.create(when, 'blacklist', object_id=obj.id)

        await self.blacklist.put(obj.id, True)

    async def remove_from_blacklist(self, obj: discord.abc.Snowflake) -> None:
        """|coro|

        Removes an object from the bot's blacklist.

        Parameters
        ----------
        obj: Snowflake
            The object to remove.
        """
        with suppress(KeyError):
            await self.blacklist.remove(obj.id)

    async def close(self) -> None:
        """Closes this bot and it's aiohttp ClientSession."""
        if hasattr(self, 'session'):
            await self.session.close()

        await super().close()

        pending = asyncio.all_tasks()
        with suppress(RecursionError):
            # Wait for all tasks to complete. This usually allows for a graceful shutdown of the bot.
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=0.5)
            except TimeoutError:
                # If the tasks take too long to complete, cancel them.
                for task in pending:
                    task.cancel()
            except asyncio.CancelledError:
                pass

    async def start(self, token: str = resolved_token, *, reconnect: bool = True) -> None:  # type: ignore
        await super().start(token, reconnect=reconnect)
