from __future__ import annotations
import asyncio
import contextlib
import datetime
import fnmatch
import json
from contextlib import suppress
from enum import Enum
from operator import attrgetter
from typing import Dict, List, Optional, Union, Self, TypeVar, Callable, Generic

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import Percy
from cogs import command, command_permissions
from cogs.utils import cache
from cogs.utils.formats import MaybeAcquire
from cogs.utils.helpers import PostgresItem
from cogs.utils.lock import lock, lock_arg
from cogs.utils.comic._parser import Parser  # noqa
from launcher import get_logger

log = get_logger(__name__)

MARVEL_ICON_URL = 'https://cdn.discordapp.com/attachments/1066703171243745377/1107651622978469888/free-marvel-282124.png'
DC_ICON_URL = 'https://cdn.discordapp.com/attachments/1066703171243745377/1107657136013586543/Screenshot_2023-05-15_151251.png'
VIZ_ICON_URL = 'https://cdn.discordapp.com/attachments/1066703171243745377/1113786444369104978/unnamed.png'

MANGA_POSITIONS = ["Story", "Art", "Story and Art", "Original Conecept", "Written", "Drawn"]


def serialize_resource_id_from_brand(bound_args: dict) -> str:
    """Return the cache key of the Brand `item` from the bound args of ComicCache.set."""
    item: Brand = bound_args["item"]
    return f"comic:{item}"


class ComicCache:
    """Cache for the Comicpulls cog."""

    def __init__(self, namespace: str = "comic"):
        self.namespace: str = namespace
        self.cache: dict[str, list[GenericComic]] = {}

    @lock("ComicCache.set", serialize_resource_id_from_brand, wait=True)
    async def set(self, item: Brand, value: list[GenericComic]) -> None:
        """Set the Comics `value` for the brand `item`."""
        cache_key = f"{self.namespace}:{item}"

        self.cache.setdefault(cache_key, [])
        self.cache[cache_key] = value

    async def get(self, item: Brand) -> list[GenericComic] | None:
        """Return the Markdown content of the symbol `item` if it exists."""
        cache_key = f"{self.namespace}:{item}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        return None

    async def delete(self, package: str) -> bool:
        """Remove all values for `package`; return True if at least one key was deleted, False otherwise."""
        pattern = f"{self.namespace}:{package}:*"

        package_keys = [
            key for key in self.cache.keys() if fnmatch.fnmatchcase(key, pattern)
        ]
        if package_keys:
            for key in package_keys:
                del self.cache[key]
            log.info(f"Deleted keys from cache: {package_keys}.")
            return True
        return False


class Brand(Enum):
    MARVEL = "Marvel"
    DC = "DC"
    MANGA = "Manga"

    def __str__(self):
        return self.name

    @property
    def icon_url(self) -> str:
        if self == self.MARVEL:
            return MARVEL_ICON_URL
        elif self == self.DC:
            return DC_ICON_URL
        elif self == self.MANGA:
            return VIZ_ICON_URL

    @property
    def link(self) -> str:
        if self == self.MARVEL:
            return "Marvel.com"
        elif self == self.DC:
            return "DC.com"
        elif self == self.MANGA:
            return "Viz.com"

    @property
    def colour(self) -> int:
        if self == self.MARVEL:
            return 0xEC1D24
        elif self == self.DC:
            return 0x0074E8
        elif self == self.MANGA:
            return 0xFFFFFF

    @property
    def default_day(self):
        if self == self.DC:
            return 3
        else:
            return 1  # Marvel and Manga

    @property
    def copyright(self):
        year = datetime.datetime.now().year
        if self == self.DC:
            return "© & ™ DC. ALL RIGHTS RESERVED"
        elif self == self.MARVEL:
            return f"Data provided by Marvel. © {year} MARVEL"
        elif self == self.MANGA:
            return f"© {year} VIZ Media, LLC. All rights reserved."


class Format(Enum):
    FULL = "Full"
    COMPACT = "Compact"
    SUMMARY = "Summary"

    def __str__(self):
        return self.name


def alpha_surnames(names: list[str]) -> list[str]:
    return sorted(names, key=lambda x: x.split(' ')[-1])


class ComicJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.name
        elif isinstance(obj, ComicPulls):
            return f"<class '{obj.__module__}'>"
        elif isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)


class GenericComic:
    """A wrapped Comic Object that Supports a Marvel and DC Comic or Manga Source

    Parameters
    ----------
    brand: Brand
        The Brand of the Comic
    id: int | str
        The ID of the Comic
    title: str
        The Title of the Comic
    description: str
        The Description of the Comic
    creators: Dict[str, List[str]]
        The Creators of the Comic
    image_url: str
        The Image URL of the Comic
    url: str
        The URL of the Comic
    page_count: int
        The Page Count of the Comic
    price: float
        The Price of the Comic
    date: datetime
        The ReleaseDate of the Comic
    **kwargs
        Any other Keyword Arguments
    """

    def __init__(
            self,
            *,
            brand: Brand = None,
            id: int | str = None,
            title: str = None,
            description: str = None,
            creators: Dict[str, List[str]] = None,
            image_url: str = None,
            url: str = None,
            page_count: int = None,
            price: float = None,
            copyright: str = None,
            date: datetime.datetime = None,
            **kwargs
    ):
        self.brand: Brand = brand
        self.id: int = id
        self.title: str = title
        self.description: str = description
        self.creators: Dict[str, List[str]] = creators

        self.image_url: str = image_url
        self.url: str = url or ""

        self.date: datetime.datetime = date
        self.page_count: int = page_count
        self.price: float = price

        self.copyright: str = copyright
        self.kwargs = kwargs

    def __str__(self):
        return self.title

    def __repr__(self):
        return f"<GenericComic id={self.id} title={self.title} brand={self.brand.name}>"

    @property
    def writer(self):
        next_key = next((a for a in ["Writer", "Creator", *MANGA_POSITIONS] if a in self.creators), None)
        return ', '.join(alpha_surnames(self.creators[next_key] if next_key else []))

    @property
    def price_format(self):
        return f"${self.price:.2f} USD" if self.price else 'Unknown'

    def format_creators(self, *, cover: bool = False, compact: bool = False):
        priority = ["Writer", "Artist", "Penciler", "Inker", "Colorist", "Letterer", "Editor", *MANGA_POSITIONS]

        def sorting_key(person: str) -> int:
            try:
                return priority.index(person)
            except ValueError:
                return len(priority)

        compact_positions = {"Writer", "Penciler", "Artist", *MANGA_POSITIONS}
        keys = sorted(self.creators.keys(), key=lambda k: (sorting_key(k), k))
        return "\n".join(
            f"**{k}**: {', '.join(alpha_surnames(self.creators[k]))}"
            for k in keys
            if (not compact or k in compact_positions) and (cover or not k.endswith("(Cover)"))
        )

    def to_embed(self, full_img: bool = True):
        embed = discord.Embed(
            title=self.title,
            colour=self.brand.colour,
            description=self.description,
            url=self.url,
        )

        if self.brand == Brand.MANGA:
            embed.add_field(name="General Info",
                            value=f"Price: {self.price_format}\n"
                                  f"Pages: {self.page_count}\n"
                                  f"Release Date: {discord.utils.format_dt(self.date, 'D')}\n"
                                  f"Category: {self.kwargs.get('category')}\n"
                                  f"Age Rating: {self.kwargs.get('age_rating')}")

            if self.creators:
                embed.add_field(name="Creators", value=self.format_creators())
        else:
            if self.creators:
                embed.add_field(name="Creators", value=self.format_creators())

            embed.add_field(name="General Info",
                            value=f"Price: {self.price_format}\n"
                                  f"Pages: {self.page_count}")

        embed.set_footer(text=f"{self.title} • {self.copyright}", icon_url=self.brand.icon_url)

        if full_img:
            embed.set_image(url=self.image_url)
        else:
            embed.set_thumbnail(url=self.image_url)

        return embed

    def to_instance(self, message: discord.Message):
        return GenericComicMessage(self, message)


class GenericComicMessage(GenericComic):
    def __init__(self, comic: GenericComic, message: discord.Message):
        super().__init__(**comic.__dict__)
        self.message = message

    def more(self):
        return self.message.jump_url


B = TypeVar('B', bound=Brand)


class ComicFeed(PostgresItem, Generic[B]):
    id: int
    guild_id: int
    channel_id: int
    format: Format
    brand: Brand
    day: int
    ping: bool
    pin: bool
    next_pull: datetime.datetime

    __slots__ = ('cog', 'id', 'guild_id', 'channel_id', 'format', 'brand', 'day', 'ping', 'pin', 'next_pull')

    def __init__(self, cog: ComicPulls, **kwargs):
        super().__init__(**kwargs)
        self.cog: ComicPulls = cog

        self.brand: B = Brand[str(self.brand)]
        self.format = Format[str(self.format)]

    def to_embed(self):
        embed = discord.Embed(
            title=f"{self.brand.value} Feed Configuration",
            description='Mangas are only published once in the first week of a month.' if self.brand == Brand.MANGA else None,
            color=self.brand.colour
        )
        embed.add_field(name="Publish Channel", value=f"<#{self.channel_id}>")
        embed.add_field(name="Format", value=f"{self.format.value}")
        embed.add_field(name="Next Scheduled", value=discord.utils.format_dt(self.next_pull, 'D'))
        embed.add_field(name="Ping Role", value=f"<@&{self.ping}>" if self.ping else None)
        embed.add_field(name="Message Pin", value="Enabled" if self.pin else "Disabled")
        embed.set_footer(text=f"[{self.guild_id}] • {self.brand.name}")
        embed.set_thumbnail(url=self.brand.icon_url)
        return embed

    async def create(self) -> Self:
        self.next_pull = self.next_scheduled()

        query = """
            INSERT INTO feed_config (guild_id, channel_id, brand, format, day, ping, pin, next_pull)
            SELECT x.guild_id, x.channel_id, x.brand, x.format, x.day, x.ping, x.pin, x.next_pull
            FROM jsonb_populate_record(null::feed_config, $1::TEXT::jsonb) AS x
        """

        await self.cog.bot.pool.execute(query, json.dumps(self.__dict__, cls=ComicJSONEncoder))
        return self

    async def edit(self, kwargs: dict) -> Self:
        query = """
            UPDATE feed_config SET (channel_id, format, day, ping, pin, next_pull) = (x.channel_id, x.format, x.day, x.ping, x.pin, x.next_pull)
            FROM jsonb_populate_record(null::feed_config, $1::TEXT::jsonb) AS x
            WHERE feed_config.guild_id = x.guild_id
            AND feed_config.brand = x.brand::TEXT;
        """

        await self.cog.bot.pool.execute(query, json.dumps(kwargs, cls=ComicJSONEncoder))
        self.cog.get_comic_config.invalidate_containing(str(self.guild_id))
        return self

    async def delete(self):
        query = "DELETE FROM feed_config WHERE guild_id = $1 AND brand = $2;"
        await self.cog.bot.pool.execute(query, self.guild_id, self.brand.name)
        self.cog.get_comic_config.invalidate_containing(str(self.guild_id))

    def next_scheduled(self, day: int = None):
        day = day or self.day
        now = discord.utils.utcnow().date()
        soon = now + datetime.timedelta(days=(day - now.isoweekday()) % 7)
        combined = datetime.datetime.combine(soon, datetime.time(0), tzinfo=datetime.timezone.utc) \
            .astimezone(datetime.timezone.utc)

        if combined < discord.utils.utcnow():
            if self.brand == Brand.MANGA:
                combined = combined.replace(month=combined.month + 1, day=day)
            else:
                combined += datetime.timedelta(days=7)

        return combined.replace(tzinfo=None)

    @property
    def prev_scheduled(self):
        return self.next_scheduled() - datetime.timedelta(days=7)


class MultiLock:
    def __init__(self):
        self.locks = {}

    def is_locked(self, lock_id: int):
        lock = self.locks.get(lock_id)
        return lock and (lock.locked() if lock else False)

    @contextlib.asynccontextmanager
    async def acquire(self, lock_id: int):
        try:
            lock = self.locks.get(lock_id)
            if not lock:
                self.locks[lock_id] = asyncio.Lock()
                lock = self.locks[lock_id]

            yield await lock.acquire()
        finally:
            self.release(lock_id)

    def release(self, lock_id: int):
        lock = self.locks.get(lock_id)
        if lock and lock.locked():
            lock.release()
            if not lock.locked():
                del self.locks[lock_id]


class ComicPulls(commands.Cog, name="Comic Feeds"):
    """Subscribe to weekly comic releases from Marvel and DC!

    Publishes lists of new releases at `6 AM`, publish days are configurable.
    Manga releases are published in the first week of every month.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self.parser: Parser = Parser  # type: ignore
        self.comic_cache: ComicCache = ComicCache()

        self._send_lock: MultiLock = MultiLock()
        self._batch_lock: asyncio.Lock = asyncio.Lock()

        self._task: Optional[asyncio.Task] = bot.loop.create_task(self.dispatch_feeds())
        self._current_feed: Optional[ComicFeed] = None
        self._have_data = asyncio.Event()

        comic: app_commands.Group = self.comics
        comic.interaction_check = self.comic_cache_check

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='firestar', id=1109861219923402752)

    async def cog_load(self) -> None:
        self.auto_fetch_comics.start()

    async def cog_unload(self) -> None:
        self.auto_fetch_comics.cancel()

        if self._task:
            self._task.cancel()

    async def cog_app_command_error(
            self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.errors.CheckFailure):
            return

    async def prev_schedule(self, brand: Brand) -> datetime.datetime:
        return max(i.date if i.date is not None else datetime.datetime.min for i in await self.comic_cache.get(brand))

    async def comic_cache_check(self, interaction: discord.Interaction) -> bool:
        if self._batch_lock.locked():
            with suppress(discord.NotFound):
                await interaction.response.send_message(
                    "<:discord_info:1113421814132117545> The comic cache is currently being "
                    "updated. Please try again later.")
            return False
        return True

    @tasks.loop(hours=6)
    async def auto_fetch_comics(self):
        await self.fetch_comics()

    async def fetch_comics(self):
        async with self._batch_lock:
            def sort_key(x):
                return x.date if x.date is not None else datetime.datetime.min

            log.debug("Fetching Marvel...")
            marvel_comics = await self.parser.fetch_marvel_lookup_table(self.bot.marvel_client)
            await self.comic_cache.set(Brand.MARVEL, sorted(marvel_comics, key=sort_key))

            log.debug("Fetching DC...")
            dc_comics = await self.parser.bs4_dc()
            await self.comic_cache.set(Brand.DC, sorted(dc_comics, key=sort_key))

            log.debug("Fetching Manga...")
            viz_comics = await self.parser.bs4_viz()
            await self.comic_cache.set(Brand.MANGA, sorted(viz_comics, key=sort_key))

    async def call_feed(self, comic: ComicFeed) -> None:
        query = "UPDATE feed_config SET next_pull = $1 WHERE guild_id = $2 AND brand = $3;"
        await self.bot.pool.execute(query, comic.next_scheduled(), comic.guild_id, comic.brand.name)

        self.bot.dispatch(f'comic_schedule', comic)

    async def wait_for_next_feeds(self, *, connection: Optional[asyncpg.Connection] = None, days: int = 7) -> ComicFeed:
        async with MaybeAcquire(connection=connection, pool=self.bot.pool) as con:
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
                feed = self._current_feed = await self.wait_for_next_feeds()

                now = datetime.datetime.utcnow()
                if feed.next_pull >= now:
                    to_sleep = (feed.next_pull - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                if self._batch_lock.locked():  # If we're already updating the cache, wait for it to finish
                    try:
                        await self._batch_lock.acquire()
                    finally:
                        self._batch_lock.release()

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
            FROM feed_config
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

    async def publish_to_feed(self, config: ComicFeed):
        async with self._send_lock.acquire(config.channel_id):
            channel = self.bot.get_channel(config.channel_id)

            comics = await self.comic_cache.get(config.brand)

            if comics:
                if config.brand == Brand.MANGA:
                    now = datetime.datetime.now()
                    formatted_date = now.strftime("%B, %Y")
                    lead_text = f"## {config.brand.value} • {formatted_date}"
                else:
                    lead_text = f"## {config.brand.value} Comics • {discord.utils.format_dt(await self.prev_schedule(config.brand), 'd')}"

                lead_msg = await channel.send(lead_text)
                if config.pin:
                    await self.pin(lead_msg)

                if config.ping:
                    await channel.send(f"<@&{config.ping}>")

                if config.format in [Format.FULL, Format.COMPACT]:
                    embeds = {comic.id: comic.to_embed(config.format == Format.FULL) for comic in comics}

                    instances = {}
                    for entry in await self.comic_cache.get(config.brand):
                        if entry in comics:
                            msg = await channel.send(embed=embeds[entry.id])
                            instances[entry.id] = entry.to_instance(msg)

                summary_embeds = await self.summary_embed(comics, config.brand, lead_msg)
                summ_msg = await channel.send(embeds=summary_embeds,
                                              allowed_mentions=discord.AllowedMentions(roles=True))
                if config.pin and config.format == Format.SUMMARY:
                    await self.pin(summ_msg)
            else:
                await channel.send(
                    embed=discord.Embed(
                        description=f"*<:discord_info:1113421814132117545> There are no new **{config.brand.name}** comics for this week.*",
                        timestamp=discord.utils.utcnow(),
                        colour=config.brand.colour
                    ).set_thumbnail(url=config.brand.icon_url)
                )

    async def summary_embed(
            self, comics: List[Union[GenericComic, GenericComicMessage]], brand: Brand, start: discord.Message = None
    ):
        embed = discord.Embed(colour=brand.colour)
        embeds = []
        for fi, cid in enumerate(await self.comic_cache.get(brand)):
            if not fi % 25 and fi != 0:
                embeds.append(embed)
                embed = discord.Embed(colour=brand.colour)

            if cid in comics:
                cs_cm = discord.utils.get(comics, id=cid.id)

                info = [f"{cs_cm.writer}"] if cs_cm.writer else []
                if cs_cm.url:
                    info.append(f"[Read More]({cs_cm.url})")

                embed.add_field(name=cs_cm.title, value=" • ".join(info) if info else "…")
        embeds.append(embed)

        if brand == Brand.MANGA:
            embeds[0].title = f"{brand.value} • Summary"
        else:
            embeds[0].title = f"{brand.value} Comics • Summary"

        if brand.copyright:
            embeds[-1].set_footer(text=brand.copyright, icon_url=brand.icon_url)

        if start:
            embed = discord.Embed(colour=brand.colour)
            embed.description = f"*Jump to the Top {start.jump_url}.*"
            embeds.append(embed)

        return embeds

    comics = app_commands.Group(name='comics', description='Comic feed commands.', guild_only=True)

    @cache.cache()
    async def get_comic_config(self, guild_id: int, brand: str) -> Optional[ComicFeed]:
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
        record = await self.bot.pool.fetchrow(
            'SELECT * FROM feed_config WHERE guild_id = $1 AND brand = $2', guild_id, brand
        )
        return ComicFeed(self, record=record) if record else None

    @comics.command(name="current")
    @app_commands.describe(brand="The comic brand to receive a feed from.")
    @app_commands.checks.cooldown(2, 15.0, key=lambda i: i.guild_id)
    @command_permissions(1, user=["manage_channels"])
    async def comics_current(self, interaction: discord.Interaction, brand: Brand):
        """Lists this week's/month's comics!"""
        await interaction.response.defer(
            ephemeral=not interaction.channel.permissions_for(interaction.user).embed_links)

        embeds = await self.summary_embed(await self.comic_cache.get(brand), brand)
        await interaction.followup.send(embeds=embeds)

    @comics.command(name="push", description="Pushes the latest comic feed to a channel.")
    @app_commands.describe(brand="The comic brand to receive a feed from.")
    @app_commands.checks.cooldown(3, 15.0, key=lambda i: i.guild_id)
    @command_permissions(1, user=["manage_channels"])
    @lock_arg("cogs.comic_push", "interaction", attrgetter("guild.id"), raise_error=True)
    async def comic_push(self, interaction: discord.Interaction, brand: Brand):
        """Triggers your current feed configuration."""
        await interaction.response.defer()

        config: ComicFeed = await self.get_comic_config(interaction.guild_id, brand)
        if not config:
            return await interaction.followup.send(
                f"<:redTick:1079249771975413910> You have not set up a **{brand.name}** feed yet in this server!")

        if not config:
            return await interaction.followup.send(
                f"<:redTick:1079249771975413910> You have not set up a **{brand.name}** feed yet in this server!")

        await self.call_feed(config)
        self.MaybeSkipTask(True)

        await interaction.followup.send(
            f"<:greenTick:1079249732364406854> Feed successfully triggered for **{brand.name}** in <#{config.channel_id}>")

    @comics.command(name="subscribe", description="Subscribes to a comic brand feed.")
    @app_commands.rename(_format='format')
    @app_commands.describe(
        brand="The comic brand to receive a feed from.",
        channel="Channel to set up the feed. Leave empty to set up in THIS channel.",
        _format="Feed format. Use /formats to view options. Summary is default."
    )
    @command_permissions(1, user=["manage_channels"])
    @lock_arg("comicpulls.comic_subscribe", "interaction", attrgetter("guild.id"), raise_error=True)
    async def comic_subscribe(
            self,
            interaction: discord.Interaction,
            brand: Brand,
            channel: discord.TextChannel = None,
            _format: Format = "SUMMARY"
    ):
        """Sets up a comic pulls feed."""
        await interaction.response.defer()
        config = await self.get_comic_config(interaction.guild.id, brand)

        if config:
            return await interaction.followup.send(
                "<:redTick:1079249771975413910> You have already set up a feed for this brand in this server.")

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
            f"<:greenTick:1079249732364406854> Set up **{brand.name}** feed in Channel {channel.mention}.",
            embed=new_config.to_embed())

    @command(
        comics.command,
        name="config",
        description="Show/Edit the current configuration for comic feeds.",
    )
    @app_commands.rename(_format='format')
    @app_commands.describe(
        brand="The comic brand to receive a feed from.",
        channel="Channel to set up the feed.",
        _format="Feed format. Use /formats to view options.",
        day="Day of the week to send the feed.",
        ping="Role to ping when the feed is sent.",
        pin="Whether to pin the feed message.",
        reset="Reset the configuration."
    )
    @command_permissions(1, user=["manage_channels"])
    async def comic_config(
            self, interaction: discord.Interaction,
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
                f"<:redTick:1079249771975413910> You have not set up a feed for **{brand.name}** yet in this server!")

        if reset:
            await config.delete()
            self.MaybeSkipTask(config is not None and self._current_feed and self._current_feed.id == config.id)
            self.get_comic_config.invalidate_containing(str(interaction.guild_id))
            return await interaction.followup.send(
                f"<:greenTick:1079249732364406854> Reset the **{brand.name}** feed configuration.", ephemeral=True)

        if not any([channel, _format, ping, day, pin]):
            return await interaction.followup.send(embed=config.to_embed())
        else:
            kwargs: dict = dict(config.__iter__())

            if channel:
                kwargs["channel_id"] = channel.id
            if day:
                kwargs["day"] = day
                kwargs["next_pull"] = config.next_scheduled(day)
            if ping:
                kwargs["ping"] = ping.id
            if pin:
                kwargs["pin"] = pin
            if _format:
                kwargs["format"] = _format.name

            await config.edit(kwargs)
            self.get_comic_config.invalidate_containing(str(interaction.guild_id))
            self.MaybeSkipTask(True)

            await interaction.followup.send(
                f'<:greenTick:1079249732364406854> Successfully modified **{brand.name}** feed configuration.')

    @commands.Cog.listener()
    async def on_comic_schedule(self, feed: ComicFeed):
        if feed:
            await self.publish_to_feed(feed)


async def setup(bot):
    await bot.add_cog(ComicPulls(bot))
