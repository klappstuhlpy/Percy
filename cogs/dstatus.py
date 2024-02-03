from __future__ import annotations

import contextlib
import dataclasses
import datetime
import enum
from dataclasses import dataclass
from typing import Optional, Any

import asyncpg
import discord
from discord.ext import tasks
from typing import TypeVar

from bot import Percy
from .utils.commands import PermissionTemplate
from .utils import commands, cache, converters
from .utils.context import GuildContext
from .utils.helpers import PostgresItem

DS_ENDPOINT = 'https://discordstatus.com/api/v2/incidents.json'
DISCORD_ICON_URL = 'https://images-ext-2.discordapp.net/external/6jW0q_egONj8FelyNsUt_ighZ6obXn0TTFuxLNJf1v4/https/discord.com/assets/f9bb9c4af2b9c32a2c5ee0014661546d.png'


class Status(enum.Enum):
    RESOLVED = 'resolved'
    INVESTIGATING = 'investigating'
    MONITORING = 'monitoring'
    IDENTIFIED = 'identified'
    UPDATE = 'update'

    @property
    def emoji(self) -> str:
        return {
            'resolved': '<:online:1101531229188272279>',
            'investigating': '<:idle:1101530975151849522>',
            'monitoring': '<:idle:1101530975151849522>',
            'identified': '<:dnd:1101531066600259685>',
            'update': '<:offline:1105801866312417331>'
        }.get(self.value)

    @property
    def color(self) -> int:
        return {
            'resolved': 0x7BCBA7,
            'investigating': 0xFCC25E,
            'monitoring': 0xFCC25E,
            'identified': 0xF57E7E,
            'update': 0xFCC25E
        }.get(self.value)


T = TypeVar('T')


class IncidentItem(PostgresItem):
    id: str
    name: str
    status: str
    started_at: datetime
    guild_id: int
    channel_id: int
    message_id: Optional[int]

    __slots__ = ('bot', 'id', 'name', 'status', 'started_at', 'guild_id', 'channel_id', 'message_id')

    def __init__(self, bot: Percy, **kwargs):
        self.bot: Percy = bot
        super().__init__(**kwargs)

    def get_channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(self.guild_id)
        if self.channel_id:
            return guild.get_channel(self.channel_id)
        return None

    async def get_message(self) -> Optional[discord.Message]:
        if self.message_id:
            channel = self.get_channel()
            if channel:
                return await channel.fetch_message(self.message_id)
        return None


@dataclass
class ShortComponent:
    code: str
    name: str
    old_status: str
    new_status: str


@dataclass
class Component:
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    position: int
    description: str
    showcase: bool
    start_date: str
    group_id: str
    page_id: str
    group: bool
    only_show_if_degraded: bool

    def __post_init__(self):
        self.created_at = converters.utcparse(self.created_at)
        self.updated_at = converters.utcparse(self.updated_at)


@dataclass
class Update:
    id: str
    status: str
    body: str
    incident_id: str
    created_at: datetime
    updated_at: datetime
    display_at: datetime
    affected_components: list[ShortComponent]
    deliver_notifications: bool
    custom_tweet: str
    tweet_id: str

    def __post_init__(self):
        self.created_at = converters.utcparse(self.created_at)
        self.updated_at = converters.utcparse(self.updated_at)
        self.display_at = converters.utcparse(self.display_at)

        if self.affected_components:
            self.affected_components = [
                ShortComponent(**component_data) for component_data in self.affected_components]  # type: ignore


@dataclass
class Incident:
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    monitoring_at: datetime
    resolved_at: datetime
    impact: str
    shortlink: str
    started_at: datetime
    page_id: str
    incident_updates: list[Update]
    components: list[Component]
    reminder_intervals: Any

    def __post_init__(self):
        self.created_at = converters.utcparse(self.created_at)
        self.updated_at = converters.utcparse(self.updated_at)
        self.monitoring_at = converters.utcparse(self.monitoring_at)
        self.resolved_at = converters.utcparse(self.resolved_at)
        self.started_at = converters.utcparse(self.started_at)

        self.components = [Component(**component_data) for component_data in self.components]  # type: ignore

        if self.incident_updates:
            self.incident_updates = [
                Update(**update_data) for update_data in self.incident_updates]  # type: ignore

    def build_embed(self) -> discord.Embed:
        updates = self.incident_updates.copy()
        updates.reverse()

        embed = discord.Embed(
            title=self.name,
            timestamp=self.started_at,
            url=self.shortlink,
            colour=Status(updates[-1].status).color)
        embed.set_author(name='Discord Status', url='https://discordstatus.com/', icon_url=DISCORD_ICON_URL)
        embed.set_footer(text='Started at')

        for update in updates:
            embed.add_field(
                name=f'{Status(update.status).emoji} {update.status.title()} '
                     f'({discord.utils.format_dt(update.created_at, 'R')})',
                value=update.body,
                inline=False)

        return embed

    def as_dict(self):
        return dataclasses.asdict(self)


class DiscordStatus(commands.Cog):
    """Discord Status Feed related commands.

    Visit: https://discordstatus.com/ for more information.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='connections', id=1118604869104840744)

    async def cog_load(self) -> None:
        self.check_new_incident.start()

    async def cog_unload(self) -> None:
        self.check_new_incident.stop()

    @cache.cache()
    async def get_subscribers(self) -> Optional[list[IncidentItem]]:
        """|coro|

        Gets the open incidents from the database.

        Returns
        -------
        Optional[IncidentItem]
            The open incident item.
        """

        query = "SELECT * FROM discord_incidents;"
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                records = await conn.fetch(query)

        if not records:
            return None

        return [IncidentItem(self.bot, record=record) for record in records]

    async def fetch_unresolved_incidents(self, bypass: bool = False) -> Optional[list[Incident]]:
        """|coro|

        Fetches the latest incident from the Discord Status Feed.

        Returns
        -------
        Optional[list[Incident]]
            The latest not 'resolved' incidents.
        """

        async with self.bot.session.get(DS_ENDPOINT) as resp:
            if resp.status != 200:
                return None

            data = await resp.json()

        if not data:
            return None

        # We are looking here now for the incidents that got updated in the last 10 minutes because if we
        # checked for incidents with the "resolved" status,
        # we would miss the "resolved" state update to add it to the embeds.

        # 10 minutes should be alright because we are checking every 3 minutes.

        # x[0] is the newest incident
        if bypass:
            return [Incident(**data) for data in data['incidents']]

        return [Incident(**data) for data in data['incidents']
                if converters.utcparse(data['updated_at']) >
                discord.utils.utcnow() - datetime.timedelta(minutes=10)]

    async def _compare_changes_and_update(self, incident: Incident, saved: IncidentItem) -> Optional[discord.Message]:
        """|coro|

        Compares the changes of the incident with the latest incident in the database.
        If there are changes, it will update the database and send a message to the channel.

        Parameters
        ----------
        incident: Incident
            The incident to compare with.
        saved: IncidentItem
            The latest incident in the database.
        """

        if saved.id is None or not saved.id:
            query = "UPDATE discord_incidents SET id = $1 WHERE guild_id = $2 RETURNING *;"
            saved = IncidentItem(
                self.bot, record=await self.bot.pool.fetchrow(query, incident.id, saved.guild_id))

        if incident.id == saved.id:
            if incident.status == saved.status:
                return

            query = "UPDATE discord_incidents SET status = $3 WHERE id = $1 AND guild_id = $2;"
            await self.bot.pool.execute(query, saved.id, saved.guild_id, incident.status)
        else:
            query = "UPDATE discord_incidents SET id = $1, status = $3 WHERE id = $2 AND guild_id = $4;"
            await self.bot.pool.execute(query, incident.id, saved.id, incident.status, saved.guild_id)

        channel = saved.get_channel()
        message = await saved.get_message()
        if not message:
            with contextlib.suppress(discord.HTTPException):
                message = await channel.send(embed=incident.build_embed())

            if message:
                query = "UPDATE discord_incidents SET message_id = $1 WHERE id = $2 AND guild_id = $3;"
                await self.bot.pool.execute(query, message.id, saved.id, saved.guild_id)
        else:
            await message.edit(embed=incident.build_embed())

        self.get_subscribers.invalidate(self)

    @cache.cache()
    async def get_subscriber(self, guild_id: int) -> Optional[IncidentItem]:
        """|coro|

        Checks if the guild is subscribed to the Discord Status Feed.

        Parameters
        ----------
        guild_id: int
            The guild to check.

        Returns
        -------
        bool
            Whether the guild is subscribed or not.
        """

        query = "SELECT * FROM discord_incidents WHERE guild_id = $1;"
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                record = await conn.fetchrow(query, guild_id)

        if not record:
            return None
        return IncidentItem(self.bot, record=record)

    @commands.command(
        commands.hybrid_group,
        name='discord-status',
        aliases=['dstatus'],
        fallback='show',
        description='Shows the current Discord Status.'
    )
    @commands.guild_only()
    async def dstatus(self, ctx: GuildContext):
        """Shows the current Discord Status."""
        latest = await self.fetch_unresolved_incidents()
        if not latest:
            raise commands.CommandError('No incidents found. *There should be though? Contact the developer!*')

        embeds = [incident.build_embed() for incident in latest]
        await ctx.send(content=(
            f'Displaying the **10** last incidents, ***{abs(10 - len(embeds))}** more incidents...*' if len(embeds) > 10 else None),
            embeds=embeds[:10], ephemeral=True)

    @commands.command(
        dstatus.command,
        name='release',
        description='Releases the last incident if not posted.',
        with_app_command=False
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @commands.guild_only()
    async def dstatus_release(self, ctx: GuildContext):
        """Releases the last incident again."""

        latest = (await self.fetch_unresolved_incidents(bypass=True))[0]
        if not latest:
            raise commands.CommandError('No incidents found. *There should be though?* Contact the developer!')

        subscriber = await self.get_subscriber(ctx.guild.id)
        if not subscriber:
            raise commands.CommandError('This guild is not subscribed to the Discord Status Feed.')

        check = await self.bot.pool.execute("SELECT * FROM discord_incidents WHERE id = $1 AND guild_id = $2;",
                                            latest.id, ctx.guild.id)
        if check.endswith('0'):
            query = "INSERT INTO discord_incidents (id, status, guild_id, channel_id) VALUES ($1, $2, $3, $4) RETURNING *;"
            values = (latest.id, latest.status, subscriber.guild_id, subscriber.channel_id)
        else:
            query = "UPDATE discord_incidents SET status = $2 WHERE id = $1 AND guild_id = $3 RETURNING *;"
            values = (latest.id, latest.status, subscriber.guild_id)

        incident = IncidentItem(self.bot, record=await self.bot.pool.fetchrow(query, *values))

        if incident.id == latest.id and incident.status == latest.status:
            raise commands.CommandError('This incident is already released.')

        message = await incident.get_channel().send(embed=latest.build_embed())

        if message:
            query = "UPDATE discord_incidents SET message_id = $1 WHERE id = $2 AND guild_id = $3;"
            await self.bot.pool.execute(query, message.id, incident.id, ctx.guild.id)

        self.get_subscribers.invalidate(self)
        self.get_subscriber.invalidate(self, ctx.guild.id)

    @commands.command(dstatus.command, name='subscribe', description='Subscribe to Discord Status updates.')
    @commands.permissions(user=PermissionTemplate.mod)
    @commands.guild_only()
    async def dstatus_subscribe(self, ctx: GuildContext, channel: discord.TextChannel):
        """Subscribes to Discord Status updates.

        Leave the channel empty to unsubscribe.
        """

        query = "INSERT INTO discord_incidents (guild_id, channel_id) VALUES ($1, $2) RETURNING *;"
        async with ctx.db.acquire() as connection:
            tr = connection.transaction()
            await tr.start()

            try:
                await connection.execute(query, ctx.guild.id, channel.id)
            except Exception as e:
                # Rollback the transaction if anything goes wrong
                await tr.rollback()

                match e:
                    case asyncpg.UniqueViolationError():
                        query = "UPDATE discord_incidents SET channel_id = $2 WHERE guild_id = $1;"
                        await ctx.db.execute(query, ctx.guild.id, channel.id)
                    case _:
                        raise commands.CommandError(f'An error occurred while subscribing to Discord Status updates: {e}')
            else:
                await tr.commit()
                await ctx.stick(True, f'Successfully subscribed to Discord Status updates in [{channel.mention}].')

        self.get_subscribers.invalidate(self)
        self.get_subscriber.invalidate(self, ctx.guild.id)

    @commands.command(dstatus.command, name='unsubscribe', description='Unsubscribe from Discord Status updates.')
    @commands.permissions(user=PermissionTemplate.mod)
    @commands.guild_only()
    async def dstatus_unsubscribe(self, ctx: GuildContext):
        """Unsubscribes from Discord Status updates."""

        query = "DELETE FROM discord_incidents WHERE guild_id = $1;"
        await ctx.db.execute(query, ctx.guild.id)

        self.get_subscribers.invalidate(self)
        self.get_subscriber.invalidate(self, ctx.guild.id)

        await ctx.stick(True, 'Successfully unsubscribed from Discord Status updates.')

    @tasks.loop(minutes=3)
    async def check_new_incident(self):
        """|coro|

        Checks for new incidents and updates the subscribers.
        This is a loop that runs every 3 minutes.
        The bot calls this automatically.
        """
        await self.bot.wait_until_ready()

        incidents = await self.fetch_unresolved_incidents()

        if not incidents:
            return

        subscribers = await self.get_subscribers()

        if not subscribers:
            return

        for incident in incidents:
            for subscriber in subscribers:
                await self._compare_changes_and_update(incident, subscriber)


async def setup(bot: Percy) -> None:
    await bot.add_cog(DiscordStatus(bot))
