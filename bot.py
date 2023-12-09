from __future__ import annotations

import datetime
import logging
import ssl
from collections import defaultdict
from typing import Optional, TYPE_CHECKING, Union, Dict, Iterable, AsyncIterator, Any, Counter, Callable, Coroutine, Type

import aiohttp
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from expiringdict import ExpiringDict
from sqlalchemy.ext.asyncio import AsyncEngine

from cogs import EXTENSIONS
from cogs.giveaway import GiveawayEntryView, GiveawayItem
from cogs.polls import PollView, PollItem
from cogs.user import UserSettings
from cogs.utils import helpers
from cogs.utils.comic._client import Marvel  # noqa
from cogs.utils.config import Config
from cogs.utils.context import Context
from cogs.utils.helpers import BasicJSONEncoder
from cogs.utils.constants import GUILD_FEATURES

if TYPE_CHECKING:
    from cogs.reminder import Reminder
    from cogs.mod import Mod as ModCog
    from cogs.config import Config as ConfigCog
    from discord.types.guild import GuildFeature
    from launcher import get_logger

    log = get_logger(__name__)
    GuildFeatureA = tuple[GuildFeature, str]
else:
    GuildFeatureA = tuple[str, str]
    log = logging.getLogger(__name__)


def _prefix_callable(bot: Percy, msg: discord.Message) -> Iterable[str]:
    user_id = bot.user.id
    base = [f'<@!{user_id}> ', f'<@{user_id}> ']
    if msg.guild is None:
        base.extend(['#', '?'])
    else:
        base.extend(bot.prefixes.get(msg.guild.id, ['?', '#']))
    return base


class ProxyObject(discord.Object):
    def __init__(self, guild: Optional[discord.abc.Snowflake]):
        super().__init__(id=0)
        self.guild: Optional[discord.abc.Snowflake] = guild


class Percy(commands.Bot):
    user: discord.ClientUser
    logging_handler: Any
    command_stats: Counter[str]
    socket_stats: Counter[str]
    command_types_used: Counter[bool]
    bot_app_info: discord.AppInfo
    pool: asyncpg.Pool
    alchemy_engine: AsyncEngine
    session: aiohttp.ClientSession
    config: Config
    old_tree_error = Callable[[discord.Interaction, discord.app_commands.AppCommandError], Coroutine[Any, Any, None]]

    def __init__(self) -> None:
        allowed_mentions = discord.AllowedMentions(roles=False, everyone=False, users=True)
        intents = discord.Intents(
            guilds=True,
            members=True,
            bans=True,
            presences=True,
            emojis=True,
            voice_states=True,
            messages=True,
            reactions=True,
            message_content=True,
            # AutoModeration
            auto_moderation_execution=True,
            auto_moderation_configuration=True,
        )
        super().__init__(
            command_prefix=_prefix_callable,  # type: ignore
            pm_help=None,
            help_attrs=dict(hidden=True),
            chunk_guilds_at_startup=False,
            heartbeat_timeout=200.0,
            allowed_mentions=allowed_mentions,
            intents=intents,
            enable_debug_events=True,
        )
        self.command_cache: Dict[int, list[discord.Message]] = ExpiringDict(
            max_len=1000, max_age_seconds=60
        )

        self.resumes: defaultdict[int, list[datetime]] = defaultdict(list)
        self.identifies: defaultdict[int, list[datetime]] = defaultdict(list)

        self.spam_control: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            10, 12.0, commands.BucketType.user
        )
        self._auto_spam_count: Counter[int] = Counter()  # type: ignore # user_id: count

        self.context: Type[Context] = Context
        self.colour: Type[helpers.Colour] = helpers.Colour
        self.marvel_client: Marvel = Marvel(self)
        self._error_message_log: list[int] = []

        self.initial_extensions: list[str] = EXTENSIONS

    def __repr__(self) -> str:
        return (
            f"<Bot id={self.user.id} name={self.user.name!r} "
            f"discriminator={self.user.discriminator!r} bot={self.user.bot}>"
        )

    @property
    def owner(self) -> discord.User:
        return self.bot_app_info.owner

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()

        self.blacklist: Config[bool] = Config('blacklist.json')
        self.prefixes: Config[list[str]] = Config('prefixes.json')

        self.media_config: Config[Dict[str, Any]] = Config('media_config.json', encoder=BasicJSONEncoder, load_later=True)
        self.data_storage: Config[Dict[dict, Any]] = Config('data_storage.json', encoder=BasicJSONEncoder)

        self.bot_app_info = await self.application_info()
        self.owner_id = self.bot_app_info.owner.id

        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
            except Exception as e:
                log.error(f"Failed to load extension `{extension}`", exc_info=e)

        await self.reattach_views()

    async def on_shard_resumed(self, shard_id: int):
        log.info('Shard ID %s has resumed...', shard_id)
        self.resumes[shard_id].append(discord.utils.utcnow())

    async def on_ready(self) -> None:
        if not hasattr(self, "launched_at"):
            self.launched_at = discord.utils.utcnow()  # noqa

        log.info(f"Ready as {self.user} (ID: {self.user.id})")

    def _clear_gateway_data(self) -> None:
        one_week_ago = discord.utils.utcnow() - datetime.timedelta(days=7)
        for shard_id, dates in self.identifies.items():
            to_remove = [index for index, dt in enumerate(dates) if dt < one_week_ago]
            for index in reversed(to_remove):
                del dates[index]

        for shard_id, dates in self.resumes.items():
            to_remove = [index for index, dt in enumerate(dates) if dt < one_week_ago]
            for index in reversed(to_remove):
                del dates[index]

    async def on_command_error(self, ctx: Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.author.send(f'{ctx.tick(False)} This command cannot be used in private messages.')
        elif isinstance(error, commands.DisabledCommand):
            await ctx.author.send(f'{ctx.tick(False)} Sorry. This command is disabled and cannot be used.')
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if isinstance(original, discord.Forbidden):
                if original.code == 50013:
                    return
                await ctx.send_tick(False, 'I do not have permission to execute this action.')
            elif isinstance(original, discord.HTTPException):
                await ctx.send('<:warning:1113421726861238363> Somehow, an unexpected error occurred. Try again later?')
            elif issubclass(type(original), RuntimeError):  # Caused by locking  -> lock.py
                await ctx.send(f"{original} Please wait for it to finish and try again later.")
        elif isinstance(error, (commands.ArgumentParsingError, commands.FlagError, commands.BadArgument)):
            await ctx.send(str(error))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send_tick(False, f"Missing required argument: `{error.param.name}`")
        elif isinstance(error, commands.BotMissingPermissions):
            missing = [perm.replace('_', ' ').replace('guild', 'server').title() for perm in error.missing_permissions]
            await ctx.send(f"I don't have the permissions to perform this action.\n"
                           f"Missing: `{', '.join(missing)}`")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"<:warning:1113421726861238363> Slow down, you're on cooldown. Retry again in **{error.retry_after:.2f}s**.")
        elif isinstance(error, commands.TooManyArguments):
            await ctx.send_tick(False, f'You called {ctx.command.name!r} command with too many arguments.')

    async def reattach_views(self) -> None:
        records = await self.pool.fetch("SELECT * FROM polls")
        cog = self.get_cog('Polls')
        for record in records:
            item = PollItem(cog, record=record)  # type: ignore
            self.add_view(PollView(self, item, archived=not item.kwargs.get('running', False)))

        records = await self.pool.fetch("SELECT * FROM giveaways")
        for record in records:
            self.add_view(GiveawayEntryView(self, GiveawayItem(record=record)))

    def get_guild_prefixes(self, guild: Optional[discord.abc.Snowflake], *, local_inject=_prefix_callable) -> list[str]:
        proxy_msg = ProxyObject(guild)
        return local_inject(self, proxy_msg)  # type: ignore  # lying

    def get_raw_guild_prefixes(self, guild_id: int) -> list[str]:
        return self.prefixes.get(guild_id, ['?', '#'])

    async def set_guild_prefixes(self, guild: discord.abc.Snowflake, prefixes: list[str]) -> None:
        if len(prefixes) == 0:
            await self.prefixes.put(guild.id, [])
        elif len(prefixes) > 10:
            raise RuntimeError('Cannot have more than 10 custom prefixes.')
        else:
            await self.prefixes.put(guild.id, sorted(set(prefixes), reverse=True))

    async def add_to_blacklist(self, object_id: int):
        await self.blacklist.put(object_id, True)

    async def remove_from_blacklist(self, object_id: int):
        try:
            await self.blacklist.remove(object_id)
        except KeyError:
            pass

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if guild.id in self.blacklist:
            await guild.leave()

    async def log_spammer(self, ctx: Context, message: discord.Message, retry_after: float, *, autoblock: bool = False):
        guild_name = getattr(ctx.guild, 'name', 'No Guild (DMs)')
        guild_id = getattr(ctx.guild, 'id', None)
        fmt = 'User %s (ID %s) in guild %r (ID %s) spamming, retry_after: %.2fs'
        log.warning(fmt, message.author, message.author.id, guild_name, guild_id, retry_after)
        if not autoblock:
            return

        embed = discord.Embed(title='Auto-blocked Member', colour=0xDDA453)
        embed.add_field(name='Member', value=f'{message.author} (ID: {message.author.id})', inline=False)
        embed.add_field(name='Guild Info', value=f'{guild_name} (ID: {guild_id})', inline=False)
        embed.add_field(name='Channel Info', value=f'{message.channel} (ID: {message.channel.id}', inline=False)
        embed.timestamp = discord.utils.utcnow()
        return await self.stats_webhook.send(embed=embed, username='Percy Spam Control')

    def resolve_command(self, command: str) -> Optional[Union[commands.Command, app_commands.commands.Command, Any]]:
        resolved = self.get_command(command)
        if not resolved:  # No message Command?
            resolved = self.tree.get_command(command)
            if not resolved:  # No root Command?
                app_cmds = self.tree.walk_commands()
                resolved = discord.utils.find(lambda c: c.qualified_name == command, app_cmds)  # find it by full name

        if resolved:
            return resolved
        return None

    async def get_context(self, origin: Union[discord.Interaction, discord.Message], /, *, cls=Context) -> Context:
        return await super().get_context(origin, cls=cls)

    async def process_commands(self, message: discord.Message):
        ctx = await self.get_context(message)

        if ctx.command is None:
            return

        if ctx.author.id in self.blacklist:
            return

        if ctx.guild is not None and ctx.guild.id in self.blacklist:
            return

        bucket = self.spam_control.get_bucket(message)
        current = message.created_at.timestamp()
        retry_after = bucket and bucket.update_rate_limit(current)
        author_id = message.author.id
        if retry_after and author_id != self.owner_id:
            self._auto_spam_count[author_id] += 1
            if self._auto_spam_count[author_id] >= 5:
                await self.add_to_blacklist(author_id)
                del self._auto_spam_count[author_id]
                await self.log_spammer(ctx, message, retry_after, autoblock=True)
            else:
                await self.log_spammer(ctx, message, retry_after)
            return
        else:
            self._auto_spam_count.pop(author_id, None)  # type: ignore

        await self.invoke(ctx)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        await self.process_commands(message)

    @staticmethod
    def get_guild_features(
            features: list[GuildFeature], *, only_current: bool = False, emojize: bool = True
    ) -> GuildFeatureA:
        """Returns a list of tuples containing all guild features if ``only_current`` is False or enabled features if True.

        Parameters
        ------------
        features: list[GuildFeature]
            The list of features to get.
        only_current: bool
            Whether to only get the current enabled features.
        emojize: bool
            Whether to emojize the feature names.

        Returns
        -----------
        GuildFeatureA
            The list of tuples containing the features.
        """
        for feature in features:
            if only_current:
                if feature in GUILD_FEATURES:
                    fmt = GUILD_FEATURES[feature]
                    if emojize:
                        yield f"{fmt[0]} {feature}", fmt[1]
                    else:
                        yield feature, fmt[1]
            else:
                fmt = GUILD_FEATURES[feature]
                if emojize:
                    yield f"{fmt[0]} {feature}", fmt[1]
                else:
                    yield feature, fmt[1]

    @staticmethod
    async def query_member_named(
            guild: discord.Guild, argument: str, *, cache: bool = False
    ) -> Optional[discord.Member]:
        """Queries a member by their name, name + discrim, or nickname.

        Parameters
        ------------
        guild: Guild
            The guild to query the member in.
        argument: str
            The name, nickname, or name + discrim combo to check.
        cache: bool
            Whether to cache the results of the query.

        Returns
        ---------
        Optional[Member]
            The member matching the query or None if not found.
        """
        if len(argument) > 5 and argument[-5] == '#':
            username, _, discriminator = argument.rpartition('#')
            members = await guild.query_members(username, limit=100, cache=cache)
            return discord.utils.get(members, name=username, discriminator=discriminator)
        else:
            members = await guild.query_members(argument, limit=100, cache=cache)
            return discord.utils.find(lambda m: m.name == argument or m.nick == argument, members)

    @staticmethod
    async def get_or_fetch_member(guild: discord.Guild, member_id: int) -> Optional[discord.Member]:
        """|coro|

        Looks up a member in cache or fetches if not found.

        Parameters
        -----------
        guild: Guild
            The guild to look in.
        member_id: int
            The member ID to search for.

        Returns
        ---------
        Optional[Member]
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
        -----------
        guild: Guild
            The guild to resolve from.
        member_ids: Iterable[int]
            An iterable of member IDs.
        Yields
        --------
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

    @discord.utils.cached_property
    def stats_webhook(self) -> discord.Webhook:
        wh_id, wh_token = self.config.stat_webhook
        hook = discord.Webhook.partial(id=wh_id, token=wh_token, session=self.session)
        return hook

    async def close(self) -> None:
        await super().close()
        if hasattr(self, 'session'):
            await self.session.close()

    async def start(self, *args, **kwargs) -> None:
        await super().start(self.config.token, reconnect=True)

    @property
    def config(self):
        return __import__('config')

    @property
    def config_cog(self) -> Optional[ConfigCog]:
        return self.get_cog('Config')

    @property
    def reminder(self) -> Optional[Reminder]:
        return self.get_cog('Reminder')

    @property
    def moderation(self) -> Optional[ModCog]:
        return self.get_cog('Mod')

    @property
    def user_settings(self) -> Optional[UserSettings]:
        return self.get_cog('User Settings')
