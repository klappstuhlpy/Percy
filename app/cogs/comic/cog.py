from __future__ import annotations

import asyncio
import datetime
import logging
from operator import attrgetter
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import utcnow

from app.core import Bot, Cog, Context, Flags, NoticeView, cooldown, describe, flag, group, make_notice, store_true
from app.utils import cache, truncate
from app.utils.lock import lock, lock_arg, lock_from
from app.utils.tasks import Scheduler, scheduled_coroutine
from config import Emojis, comic_api_url, default_prefix

from .client import ComicCache, LOCGClient, Parser
from .models import Brand, ComicFeed, Format, GenericComic, GenericComicMessage
from .ui import JumpToTopButton

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from app.database.base import GuildConfig

log = logging.getLogger(__name__)

type AnyComic = GenericComic | GenericComicMessage
comic_cache_refresh_task_id = 'ComicCache.refresh'


class ComicsEditFlags(Flags):
    channel: discord.TextChannel = flag(description='Channel to set up the feed. Leave empty to set up in THIS channel.')
    day: commands.Range[int, 1, 7] = flag(description='Day of the week to send the feed.')
    ping: discord.Role = flag(description='Role to ping when the feed is sent.')
    format: Format = flag(description='Feed format. Use /formats to view options.')
    pin: bool = store_true(description='Whether to pin the feed message.')
    reset: bool = store_true(description='Reset the configuration.', short='r')


class Comics(Cog):
    """Subscribe to weekly comic releases from Marvel, DC and more.

    Publishes lists of new releases at `12 p.m.`, publish days are configurable.
    Manga releases are published in the first week of every month.
    """

    emoji = '<:firestar:1322354632529543218>'

    if TYPE_CHECKING:
        bot: Bot
        parser: Parser
        comic_cache: ComicCache
        inventory_scheduler: Scheduler
        locg_client: LOCGClient
        _current_feed: ComicFeed | None
        __event: asyncio.Event
        __dispatching_task: asyncio.Task | None

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.locg_client: LOCGClient = LOCGClient(self.bot.session, base_url=comic_api_url)

        self.parser: Parser = Parser()
        self.comic_cache: ComicCache = ComicCache()
        self.inventory_scheduler: Scheduler = Scheduler(self)

        self._current_feed: ComicFeed | None = None

        self.__dispatching_task: asyncio.Task | None = None
        self.__event = asyncio.Event()

    def reset_cache(self) -> None:
        self.inventory_scheduler.cancel_all()
        self.comic_cache.reset()

    async def cog_load(self) -> None:
        """Refresh documentation inventory on cog initialization."""
        self.inventory_scheduler.schedule(
            comic_cache_refresh_task_id,
            self.refresh_inventories(),
        )

    async def cog_unload(self) -> None:
        """Clear scheduled inventories, queued symbols and cleanup task on cog unload."""
        self.reset_cache()

    def prev_schedule(self, brand: Brand) -> datetime.datetime:
        comics = self.comic_cache.get(brand) or []
        return max(i.date if i.date is not None else datetime.datetime.min for i in comics)

    @scheduled_coroutine
    @lock(comic_cache_refresh_task_id, 'comic refresh task', wait=True, raise_error=True)
    async def refresh_inventories(self) -> None:
        if self.__dispatching_task:
            self.__dispatching_task.cancel()
            self.__dispatching_task = None

        log.debug('Refreshing comic cache...')

        coroutines: list[tuple[Coroutine, Brand]] = [
            (self.locg_client.fetch_comics('marvel'), Brand.MARVEL),
            (self.locg_client.fetch_comics('dc'), Brand.DC),
            (self.parser.bs4_viz(), Brand.MANGA)
        ]

        def sort_key(x: GenericComic) -> datetime.datetime:
            return x.date if x.date is not None else datetime.datetime.min

        for coro, brand in coroutines:
            log.debug('Fetching %s inventory...', brand.name)
            try:
                data = await coro
                if data:
                    data.sort(key=sort_key, reverse=True)
                    await self.comic_cache.set(brand, data)
            except Exception as e:
                log.warning('Error refreshing comic cache for %s: %s', brand.name, e)
            else:
                log.debug('Fetched %s inventory.', brand.name)

    @refresh_inventories.after_task  # type: ignore[arg-type]
    async def after_refresh_inventories(self) -> None:
        if not self.__dispatching_task:
            self.__dispatching_task = self.bot.loop.create_task(self.dispatch_feeds())

        self.inventory_scheduler.schedule_at(
            discord.utils.utcnow() + datetime.timedelta(hours=6),
            comic_cache_refresh_task_id,
            self.refresh_inventories()
        )

    async def call_feed(self, comic: ComicFeed) -> None:
        """|coro|

        Calls the feed for the given comic.

        Parameters
        ----------
        comic: :class:`ComicFeed`
            The comic feed to call.
        """
        await self.bot.db.comics.set_next_pull(comic.next_scheduled(), comic.guild_id, comic.brand.name)

        self.bot.dispatch('comic_schedule', comic)

    async def wait_for_next_feed(self, *, days: int = 7) -> ComicFeed | None:
        """|coro|

        Waits for the next feed to be ready.

        Parameters
        ----------
        days: :class:`int`
            The number of days to wait for the next feed. Default is `7`.

        Returns
        -------
        :class:`ComicFeed`
            The next feed to be dispatched.
        """
        async with (self.bot.db.acquire(timeout=500.0)) as con:
            feed = await self.get_earliest_feed(connection=con, days=days)
            if feed is not None:
                log.debug('Loaded next feed %r to fire at %s.', feed.id, feed.next_pull)
                self.__event.set()
                return feed

            self.__event.clear()
            self._current_feed = None
            log.debug('No feed ready, waiting for next feed...')
            await self.__event.wait()

            return await self.get_earliest_feed(connection=con, days=days)

    async def dispatch_feeds(self) -> None:
        """|coro|

        Dispatches comic feeds to their respective channels.

        This task is responsible for sending comic feeds to their respective channels.
        It waits for the next feed to be ready and sends it to the configured channels.
        """
        try:
            while not self.bot.is_closed():
                self._current_feed = feed = await self.wait_for_next_feed()
                now = utcnow()

                assert feed is not None
                if feed.next_pull.replace(tzinfo=datetime.UTC) >= now:
                    to_sleep = (feed.next_pull - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                log.debug('Dispatching feed: %s', feed)
                await self.call_feed(feed)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self.reset_task()

    async def get_earliest_feed(
            self, *, connection: asyncpg.Connection | None = None, days: int = 7
    ) -> ComicFeed | None:
        """|coro|

        Gets the earliest feed that is ready to be dispatched.

        Parameters
        ----------
        connection: :class:`asyncpg.Connection`
            The connection to use for the query.
        days: :class:`int`
            The number of days to wait for the next feed. Default is `7`.

        Returns
        -------
        :class:`ComicFeed`
            The next feed to be dispatched.
        """
        record = await self.bot.db.comics.get_next_scheduled(days, connection=connection)
        return ComicFeed(cog=self, record=record) if record else None

    def reset_task(self) -> None:
        """Maybe skip the dispatching task."""
        if self.__dispatching_task:
            self.__dispatching_task.cancel()
            self.__dispatching_task = self.bot.loop.create_task(self.dispatch_feeds())

    async def pin(self, msg: discord.Message) -> None:
        """|coro|

        Pins a message to the channel.

        Parameters
        ----------
        msg: :class:`discord.Message`
            The message to pin.
        """
        try:
            pins = list(reversed(await msg.channel.pins()))
            if len(pins) >= 50:
                try:
                    p = next(i for i in pins if self.bot.user and i.author.id == self.bot.user.id)
                    await p.unpin()
                except StopIteration:
                    return
            await msg.pin()

            async for m in msg.channel.history(limit=1):
                await m.delete()
        except discord.Forbidden:
            pass

    @lock_arg('Comic.publish', 'config', attrgetter('channel_id'), raise_error=True, wait=True)
    async def publish_to_feed(self, config: ComicFeed) -> None:
        """|coro|

        Publishes the comic feed to the configured channel.

        Parameters
        ----------
        config: :class:`ComicFeed`
            The comic feed configuration to publish.
        """
        try:
            channel = self.bot.get_channel(config.channel_id)
            assert isinstance(channel, discord.TextChannel)
            comics = self.comic_cache.get(config.brand)

            if comics:
                if config.brand == Brand.MANGA:
                    now = datetime.datetime.now()
                    formatted_date = now.strftime('%B, %Y')
                    lead_text = f'## {config.brand.value} • {formatted_date}'
                else:
                    lead_text = f'## {config.brand.value} Comics • {discord.utils.format_dt(self.prev_schedule(config.brand), 'd')}'

                lead_msg = await channel.send(lead_text)
                if config.pin:
                    await self.pin(lead_msg)

                if config.ping:
                    await channel.send(f'<@&{config.ping}>')

                if config.format in [Format.FULL, Format.COMPACT]:
                    full = config.format == Format.FULL

                    instances: dict[int, GenericComicMessage] = {}
                    for entry in comics:
                        try:
                            msg = await channel.send(view=NoticeView(entry.to_container(full)))
                        except discord.DiscordServerError as exc:
                            if exc.code == 503:
                                # Service Unavailable, we try again in after some time
                                await asyncio.sleep(3)
                                msg = await channel.send(view=NoticeView(entry.to_container(full)))
                            else:
                                continue
                        instances[entry.id] = entry.to_instance(msg)  # type: ignore

                summary = self.build_summary_container(comics, config.brand, jump_button=JumpToTopButton(lead_msg))
                summ_msg = await channel.send(
                    view=NoticeView(summary), allowed_mentions=discord.AllowedMentions(roles=True),
                )
                if config.pin and config.format == Format.SUMMARY:
                    await self.pin(summ_msg)
            else:
                await channel.send(
                    view=make_notice(
                        f'{config.brand.value} Comics',
                        f'{Emojis.info} There are no new **{config.brand.name}** comics for this week. :/\n'
                        f'-# {config.brand.copyright}',
                        accent=config.brand.colour,
                        thumbnail=config.brand.icon_url,
                    )
                )
        except discord.Forbidden:
            guild_config: GuildConfig = await self.bot.db.get_guild_config(config.guild_id)  # type: ignore[misc]
            await guild_config.send_alert(
                f'I don\'t have permission to send messages in the configured channel for the **{config.brand.name}** feed.\n'
                f'Please adjust the permissions and try by using `{default_prefix}comics push {config.brand.name}`.',
                force=True
            )

    def build_summary_container(
        self, comics: list[AnyComic], brand: Brand, *, jump_button: discord.ui.Button | None = None
    ) -> discord.ui.Container:
        """Build the Components V2 summary card listing every comic for a brand.

        Parameters
        ----------
        comics: :class:`list`
            The list of comics to summarise.
        brand: :class:`Brand`
            The brand to build the summary for.
        jump_button: :class:`discord.ui.Button` | None
            Optional link button (e.g. "jump to top") appended to the card.

        Returns
        -------
        :class:`discord.ui.Container`
            The summary card.
        """
        container = discord.ui.Container(accent_colour=brand.colour)
        container.add_item(discord.ui.TextDisplay(f'## {brand.value} Comics • Summary'))
        container.add_item(discord.ui.Separator())

        lines: list[str] = []
        for cid in self.comic_cache.get(brand) or []:
            cs_cm = discord.utils.get(comics, id=cid.id)
            if cs_cm is None:
                continue
            info = [cs_cm.writer] if cs_cm.writer else []
            if cs_cm.url:
                info.append(f'[Read More]({cs_cm.url})')
            lines.append(f'**{cs_cm.title}** — {" **×** ".join(info) if info else "…"}')

        container.add_item(discord.ui.TextDisplay(truncate('\n'.join(lines), 3900) or '…'))

        if jump_button is not None:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(jump_button))

        if brand.copyright:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f'-# {brand.copyright}'))

        return container

    @cache.cache()
    async def get_comic_config(self, guild_id: int, brand: Brand) -> ComicFeed | None:
        """|coro| @cached

        Gets the comic feed config for a guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the configs for.
        brand: :class:`str`
            The brand to get the config for.

        Returns
        -------
        :class:`ComicFeed`
            The comic feed config, if found.
        """
        record = await self.bot.db.comics.get_config(guild_id, str(brand))
        return ComicFeed(cog=self, record=record) if record else None

    @group(
        'comics',
        alias='comic',
        description='Group command for managing comic feeds.',
        guild_only=True,
        hybrid=True
    )
    async def _comics(self, ctx: Context) -> None:
        """Group command for managing polls."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_comics.command(
        'current',
        description='Shows you this week\'s/month\'s comics!',
    )
    @cooldown(2, 30.0, commands.BucketType.guild)
    @describe(brand='The comic brand to receive the newest feed from.')
    @lock_from(refresh_inventories, raise_error=True)
    async def comics(self, ctx: Context, brand: Brand) -> None:
        """Lists this week's/month's comics!"""
        await ctx.defer(ephemeral=True)
        container = self.build_summary_container(self.comic_cache.get(brand) or [], brand)
        await ctx.send(view=NoticeView(container), ephemeral=True)

    @_comics.command(
        'push',
        description='Pushes the latest comic feed to a channel.',
        user_permissions=['manage_channels']
    )
    @cooldown(3, 30.0, commands.BucketType.guild)
    @describe(brand='The comic brand to receive a feed from.')
    @lock_arg('Comic.push', 'ctx', attrgetter('guild.id'), raise_error=True)
    @lock_from(refresh_inventories, raise_error=True)
    async def comics_push(self, ctx: Context, brand: Brand) -> None:
        """Triggers your current feed configuration."""
        await ctx.defer()

        config: ComicFeed = await self.get_comic_config(ctx.guild_id, brand)  # type: ignore[misc]
        if config is None:
            await ctx.send_error(f'You have not set up a **{brand.name}** feed yet in this server!')
            return

        await self.call_feed(config)
        self.reset_task()

        await ctx.send_success(f'Feed successfully triggered for **{brand.name}** in <#{config.channel_id}>')

    @_comics.command(
        'subscribe',
        description='Subscribes to a comic brand feed.',
        user_permissions=['manage_channels']
    )
    @app_commands.rename(_format='format')
    @describe(
        brand='The comic brand to receive a feed from.',
        channel='Channel to set up the feed. Leave empty to set up in THIS channel.',
        _format='The format of how the feed is being displayed. Available options are: `summary`, `compact`, `full`.'
    )
    @lock_arg('Comic.subscribe', 'ctx', attrgetter('guild.id'), raise_error=True)
    @lock_from(refresh_inventories, raise_error=True)
    async def comic_subscribe(
            self,
            ctx: Context,
            brand: Brand,
            channel: discord.TextChannel | None = None,
            _format: Format = Format.SUMMARY
    ) -> None:
        """Sets up a comic pulls feed."""
        await ctx.defer()
        assert ctx.guild is not None

        config = await self.get_comic_config(ctx.guild.id, brand)  # type: ignore
        if config is not None:
            await ctx.send_error('You have already set up a feed for this brand in this server.')
            return

        if channel is None:
            channel = ctx.channel

        new_config = ComicFeed.temporary(
            guild_id=ctx.guild.id,
            brand=brand,
            channel_id=channel.id,
            format=_format,
            day=brand.default_day,
            ping=None,
            pin=False
        )
        new_config.next_pull = new_config.next_scheduled()

        await self.bot.db.comics.create_config(new_config.to_dict())

        self.get_comic_config.invalidate_containing(str(ctx.guild.id))
        self.reset_task()

        await ctx.send(
            view=NoticeView(
                new_config.to_container(header=f'{Emojis.success} Set **{brand.name}** feed in Channel {channel.mention}.')
            )
        )

    @_comics.command(
        'config',
        description='Show/Edit the current configuration for comic feeds.',
        user_permissions=['manage_channels']
    )
    @describe(brand='The comic brand to receive the feed from.')
    async def comic_config(
            self,
            ctx: Context,
            brand: Brand,
            *,
            flags: ComicsEditFlags,
    ) -> None:
        """Show/Edit the current configuration for comic feeds."""
        await ctx.defer(ephemeral=True)

        config: ComicFeed = await self.get_comic_config(ctx.guild_id, brand)  # type: ignore
        if config is None:
            await ctx.send_error(f'You have not set up a feed for **{brand.name}** yet in this server!')
            return

        if flags.reset:
            await config.delete()
            if config is not None and self._current_feed and self._current_feed.id == config.id:
                self.reset_task()

            self.get_comic_config.invalidate_containing(str(ctx.guild_id))
            await ctx.send_success(f'Reset the **{brand.name}** feed configuration.', ephemeral=True)
            return

        if not any([flags.channel, flags.format, flags.ping, flags.day, flags.pin]):
            await ctx.send(view=NoticeView(config.to_container()))
            return

        form: dict = {}

        if flags.channel:
            form['channel_id'] = flags.channel.id
        if flags.day:
            form['day'] = flags.day
            form['next_pull'] = config.next_scheduled(flags.day)
        if flags.ping:
            form['ping'] = flags.ping.id
        if flags.pin:
            form['pin'] = flags.pin
        if flags.format:
            form['format'] = flags.format.name

        await config.update(**form)
        self.get_comic_config.invalidate_containing(str(ctx.guild_id))
        self.reset_task()

        await ctx.send_success(f'Successfully modified **{brand.name}** feed configuration.')

    @Cog.listener()
    @lock_from(publish_to_feed, wait=True)
    async def on_comic_schedule(self, feed: ComicFeed) -> None:
        if feed:
            await self.publish_to_feed(feed)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Comics(bot))
