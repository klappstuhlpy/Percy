from __future__ import annotations
import asyncio
import contextlib
import datetime
from operator import attrgetter
from typing import List, Optional, Union, Callable

import asyncpg
import discord
from discord import app_commands
from discord.ext import tasks

from cogs.comic._cache import ComicCache
from cogs.comic._client import Marvel
from cogs.comic._data import ComicFeed, Brand, GenericComic, Format, GenericComicMessage
from cogs.utils import cache, commands
from cogs.utils.helpers import AcquireProtocol
from cogs.utils.lock import lock_arg, lock_func, lock, LockedResourceError
from cogs.comic._parser import Parser
from launcher import get_logger
from bot import Percy


log = get_logger(__name__)

AnyComic = Union[GenericComic, GenericComicMessage]


class ComicPulls(commands.Cog, name='Comic Feeds'):
    """Subscribe to weekly comic releases from Marvel and DC!

    Publishes lists of new releases at `6 AM`, publish days are configurable.
    Manga releases are published in the first week of every month.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self.parser: Parser = Parser()
        self.comic_cache: ComicCache = ComicCache()

        self._task: Optional[asyncio.Task] = bot.loop.create_task(self.dispatch_feeds())
        self._current_feed: Optional[ComicFeed] = None
        self._have_data = asyncio.Event()

        self.marvel_client: Marvel = Marvel(self.bot)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='firestar', id=1109861219923402752)

    async def cog_load(self) -> None:
        self.auto_fetch_comics.start()

    async def cog_unload(self) -> None:
        self.auto_fetch_comics.cancel()

        if self._task:
            self._task.cancel()

    async def prev_schedule(self, brand: Brand) -> datetime.datetime:
        return max(i.date if i.date is not None else datetime.datetime.min for i in self.comic_cache.get(brand))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        with contextlib.suppress(discord.HTTPException):
            if isinstance(error.__cause__, LockedResourceError):
                await interaction.response.send_message(
                    '<:discord_info:1113421814132117545> The comic cache is currently being '
                    'updated. Please try again later.'
                )

    @tasks.loop(hours=6)
    async def auto_fetch_comics(self):
        await self.refresh_comics()

    @lock('ComicCache.refresh', 'comic refresh task', wait=True, raise_error=True)
    async def refresh_comics(self):
        log.debug('Refreshing comic cache...')
        if self._task:
            self._task.cancel()

        def sort_key(x):
            return x.date if x.date is not None else datetime.datetime.min

        log.debug('Fetching Marvel...')
        marvel_comics = await self.parser.fetch_marvel_lookup_table(self.marvel_client)
        await self.comic_cache.set(Brand.MARVEL, sorted(marvel_comics, key=sort_key))

        log.debug('Fetching DC...')
        dc_comics = await self.parser.bs4_dc()
        await self.comic_cache.set(Brand.DC, sorted(dc_comics, key=sort_key))

        log.debug('Fetching Manga...')
        viz_comics = await self.parser.bs4_viz()
        await self.comic_cache.set(Brand.MANGA, sorted(viz_comics, key=sort_key))

        self._task = self.bot.loop.create_task(self.dispatch_feeds())
        log.debug('Comic cache refreshed.')

    async def call_feed(self, comic: ComicFeed) -> None:
        query = "UPDATE comic_config SET next_pull = $1 WHERE guild_id = $2 AND brand = $3;"
        await self.bot.pool.execute(query, comic.next_scheduled(), comic.guild_id, comic.brand.name)

        self.bot.dispatch(f'comic_schedule', comic)

    async def wait_for_next_feed(self, *, connection: Optional[asyncpg.Connection] = None, days: int = 7) -> ComicFeed:
        async with AcquireProtocol(connection=connection, pool=self.bot.pool) as con:
            feed = await self.get_earliest_feed(connection=con, days=days)
            if feed is not None:
                self._have_data.set()
                return feed

            self._have_data.clear()
            self._current_feed = None
            await self._have_data.wait()

            return await self.get_earliest_feed(connection=con, days=days)

    async def dispatch_feeds(self) -> None:
        try:
            while not self.bot.is_closed():
                feed = self._current_feed = await self.wait_for_next_feed()

                now = discord.utils.utcnow()
                if feed.next_pull >= now:
                    to_sleep = (feed.next_pull - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                await self.call_feed(feed)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_feeds())

    async def get_earliest_feed(
            self, *, connection: Optional[asyncpg.Connection] = None, days: int = 7
    ) -> Optional[ComicFeed]:
        query = """
            SELECT *
            FROM comic_config
            WHERE next_pull IS NOT NULL
            AND (next_pull AT TIME ZONE 'UTC') < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY next_pull
            LIMIT 1;
        """
        con = connection or self.bot.pool

        record = await con.fetchrow(query, datetime.timedelta(days=days))
        return ComicFeed(self, record=record) if record else None

    def MaybeSkipTask(self, key: Union[Callable, bool]) -> bool:
        if not key:
            return False

        self._task.cancel()
        self._task = self.bot.loop.create_task(self.dispatch_feeds())

    async def pin(self, msg: discord.Message):
        try:
            pins = list(reversed(await msg.channel.pins()))
            if len(pins) >= 50:
                try:
                    p = next(i for i in pins if i.author.id == self.bot.user.id)
                    await p.unpin()
                except StopIteration:
                    return None
            await msg.pin()

            async for m in msg.channel.history(limit=1):
                await m.delete()
        except discord.Forbidden:
            pass

    @lock_arg('Comic.publish', 'config', attrgetter('channel_id'), raise_error=True)
    async def publish_to_feed(self, config: ComicFeed):
        channel = self.bot.get_channel(config.channel_id)

        comics = self.comic_cache.get(config.brand)

        if comics:
            if config.brand == Brand.MANGA:
                now = datetime.datetime.now()
                formatted_date = now.strftime('%B, %Y')
                lead_text = f'## {config.brand.value} • {formatted_date}'
            else:
                lead_text = f'## {config.brand.value} Comics • {discord.utils.format_dt(await self.prev_schedule(config.brand), 'd')}'

            lead_msg = await channel.send(lead_text)
            if config.pin:
                await self.pin(lead_msg)

            if config.ping:
                await channel.send(f'<@&{config.ping}>')

            if config.format in [Format.FULL, Format.COMPACT]:
                embeds = {comic.id: comic.to_embed(config.format == Format.FULL) for comic in comics}

                instances = {}
                for entry in comics:
                    msg = await channel.send(embed=embeds[entry.id])
                    instances[entry.id] = entry.to_instance(msg)

            summary_embeds = await self.summary_embed(comics, config.brand, lead_msg)
            summ_msg = await channel.send(
                embeds=summary_embeds, allowed_mentions=discord.AllowedMentions(roles=True))
            if config.pin and config.format == Format.SUMMARY:
                await self.pin(summ_msg)
        else:
            await channel.send(
                embed=discord.Embed(
                    description=f'*<:discord_info:1113421814132117545> There are no new **{config.brand.name}** comics for this week.*',
                    timestamp=discord.utils.utcnow(),
                    colour=config.brand.colour
                ).set_thumbnail(url=config.brand.icon_url)
            )

    async def summary_embed(self, comics: List[AnyComic], brand: Brand, start: discord.Message = None):
        embed, embeds = discord.Embed(colour=brand.colour), []

        for fi, cid in enumerate(self.comic_cache.get(brand)):
            if not fi % 25 and fi != 0:
                embeds.append(embed)
                embed = discord.Embed(colour=brand.colour)

            if cid in comics:
                cs_cm = discord.utils.get(comics, id=cid.id)

                info = [f'{cs_cm.writer}'] if cs_cm.writer else []
                if cs_cm.url:
                    info.append(f'[Read More]({cs_cm.url})')

                embed.add_field(name=cs_cm.title, value=' • '.join(info) if info else '…')
        embeds.append(embed)

        if brand == Brand.MANGA:
            embeds[0].title = f'{brand.value} • Summary'
        else:
            embeds[0].title = f'{brand.value} Comics • Summary'

        if brand.copyright:
            embeds[-1].set_footer(text=brand.copyright, icon_url=brand.icon_url)

        if start:
            embed = discord.Embed(colour=brand.colour)
            embed.description = f'*Jump to the Top {start.jump_url}.*'
            embeds.append(embed)

        return embeds

    @cache.cache()
    async def get_comic_config(self, guild_id: int, brand: Brand) -> Optional[ComicFeed]:
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
        Optional[:class:`ComicFeed`]
            The comic feed config, if found.
        """
        query = "SELECT * FROM comic_config WHERE guild_id = $1 AND brand = $2;"
        record = await self.bot.pool.fetchrow(query, guild_id, str(brand))
        return ComicFeed(self, record=record) if record else None

    comics = app_commands.Group(name='comics', description='Comic feed commands.', guild_only=True,
                                extras=dict(bypass_error=app_commands.errors.CheckFailure))

    @commands.command(
        comics.command,
        name='current',
        description='Lists this week\'s/month\'s comics!',
        cooldown=commands.CooldownMap(rate=2, per=15.0, key=attrgetter('guild_id'))
    )
    @app_commands.describe(brand='The comic brand to receive a feed from.')
    @commands.permissions(user=['manage_channels'])
    @lock_func(refresh_comics, raise_error=True)
    async def comics_current(self, interaction: discord.Interaction, brand: Brand):
        """Lists this week's/month's comics!"""
        await interaction.response.defer(
            ephemeral=not interaction.channel.permissions_for(interaction.user).embed_links)

        embeds = await self.summary_embed(self.comic_cache.get(brand), brand)
        await interaction.followup.send(embeds=embeds)

    @commands.command(
        comics.command,
        name='push',
        description='Pushes the latest comic feed to a channel.',
        cooldown=commands.CooldownMap(rate=2, per=15.0, key=attrgetter('guild_id'))
    )
    @app_commands.describe(brand='The comic brand to receive a feed from.')
    @commands.permissions(user=['manage_channels'])
    @lock_arg('Comic.push', 'interaction', attrgetter('guild.id'), raise_error=True)
    @lock_func(refresh_comics, raise_error=True)
    async def comics_push(self, interaction: discord.Interaction, brand: Brand):
        """Triggers your current feed configuration."""
        await interaction.response.defer()

        config: ComicFeed = await self.get_comic_config(interaction.guild_id, brand)
        if not config:
            return await interaction.followup.send(
                f'<:redTick:1079249771975413910> You have not set up a **{brand.name}** feed yet in this server!')

        if not config:
            return await interaction.followup.send(
                f'<:redTick:1079249771975413910> You have not set up a **{brand.name}** feed yet in this server!')

        await self.call_feed(config)
        self.MaybeSkipTask(True)

        await interaction.followup.send(
            f'<:greenTick:1079249732364406854> Feed successfully triggered for **{brand.name}** in <#{config.channel_id}>')

    @commands.command(
        comics.command,
        name='subscribe',
        description='Subscribes to a comic brand feed.'
    )
    @app_commands.rename(_format='format')
    @app_commands.describe(
        brand='The comic brand to receive a feed from.',
        channel='Channel to set up the feed. Leave empty to set up in THIS channel.',
        _format='Feed format. Use /formats to view options. Summary is default.'
    )
    @commands.permissions(user=['manage_channels'])
    @lock_arg('Comic.subscribe', 'interaction', attrgetter('guild.id'), raise_error=True)
    @lock_func(refresh_comics, raise_error=True)
    async def comic_subscribe(
            self,
            interaction: discord.Interaction,
            brand: Brand,
            channel: discord.TextChannel = None,
            _format: Format = Format.SUMMARY
    ):
        """Sets up a comic pulls feed."""
        await interaction.response.defer()
        config = await self.get_comic_config(interaction.guild.id, brand)

        if config:
            return await interaction.followup.send(
                '<:redTick:1079249771975413910> You have already set up a feed for this brand in this server.')

        if channel is None:
            channel = interaction.channel

        new_config = ComicFeed.temporary(
            self,
            guild_id=interaction.guild.id,
            brand=brand,
            channel_id=channel.id,
            format=_format,
            day=brand.default_day,
            ping=None,
            pin=False
        )

        await new_config.create()
        self.get_comic_config.invalidate_containing(str(interaction.guild.id))
        self.MaybeSkipTask(True)

        await interaction.followup.send(
            f'<:greenTick:1079249732364406854> Set up **{brand.name}** feed in Channel {channel.mention}.',
            embed=new_config.to_embed())

    @commands.command(
        comics.command,
        name='config',
        description='Show/Edit the current configuration for comic feeds.',
    )
    @app_commands.rename(_format='format')
    @app_commands.describe(
        brand='The comic brand to receive a feed from.',
        channel='Channel to set up the feed.',
        _format='Feed format. Use /formats to view options.',
        day='Day of the week to send the feed.',
        ping='Role to ping when the feed is sent.',
        pin='Whether to pin the feed message.',
        reset='Reset the configuration.'
    )
    @commands.permissions(user=['manage_channels'])
    async def comic_config(
            self,
            interaction: discord.Interaction,
            brand: Brand,
            channel: discord.TextChannel = None,
            day: app_commands.Range[int, 1, 7] = None,
            ping: discord.Role = None,
            pin: bool = None,
            _format: Format = None,
            reset: bool = False
    ):
        await interaction.response.defer(ephemeral=True)
        config: ComicFeed = await self.get_comic_config(interaction.guild_id, brand)
        if not config:
            return await interaction.followup.send(
                f'<:redTick:1079249771975413910> You have not set up a feed for **{brand.name}** yet in this server!')

        if reset:
            await config.delete()
            self.MaybeSkipTask(config is not None and self._current_feed and self._current_feed.id == config.id)
            self.get_comic_config.invalidate_containing(str(interaction.guild_id))
            return await interaction.followup.send(
                f'<:greenTick:1079249732364406854> Reset the **{brand.name}** feed configuration.', ephemeral=True)

        if not any([channel, _format, ping, day, pin]):
            return await interaction.followup.send(embed=config.to_embed())
        else:
            kwargs: dict = dict(config.__iter__())

            if channel:
                kwargs['channel_id'] = channel.id
            if day:
                kwargs['day'] = day
                kwargs['next_pull'] = config.next_scheduled(day)
            if ping:
                kwargs['ping'] = ping.id
            if pin:
                kwargs['pin'] = pin
            if _format:
                kwargs['format'] = _format.name

            await config.edit(kwargs)
            self.get_comic_config.invalidate_containing(str(interaction.guild_id))
            self.MaybeSkipTask(True)

            await interaction.followup.send(
                f'<:greenTick:1079249732364406854> Successfully modified **{brand.name}** feed configuration.')

    @commands.Cog.listener()
    @lock_func(publish_to_feed, wait=True)
    async def on_comic_schedule(self, feed: ComicFeed):
        if feed:
            await self.publish_to_feed(feed)
