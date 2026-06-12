from __future__ import annotations

import contextlib
import dataclasses
import datetime
import enum
from dataclasses import dataclass
from typing import Any

import asyncpg
import discord
from discord.ext import tasks

from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.utils import cache, utcparse
from config import Emojis

DS_ENDPOINT = "https://discordstatus.com/api/v2/incidents.json"
DISCORD_ICON_URL = "https://klappstuhl.me/gallery/raw/EzWyA.png"


class Status(enum.Enum):
    RESOLVED = "resolved"
    INVESTIGATING = "investigating"
    MONITORING = "monitoring"
    IDENTIFIED = "identified"
    UPDATE = "update"

    @property
    def emoji(self) -> str:
        return {  # type: ignore[return-value]
            "resolved": Emojis.Status.online,
            "investigating": Emojis.Status.idle,
            "monitoring": Emojis.Status.idle,
            "identified": Emojis.Status.dnd,
            "update": Emojis.Status.offline,
        }.get(self.value)

    @property
    def color(self) -> int:
        return {  # type: ignore[return-value]
            "resolved": 0x7BCBA7,
            "investigating": 0xFCC25E,
            "monitoring": 0xFCC25E,
            "identified": 0xF57E7E,
            "update": 0xFCC25E,
        }.get(self.value)


class IncidentItem(BaseRecord):
    """Represents a Discord Status Feed incident item."""

    bot: Bot
    id: str
    name: str
    status: str
    started_at: datetime.datetime
    guild_id: int
    channel_id: int
    message_id: int | None

    __slots__ = ("bot", "channel_id", "guild_id", "id", "message_id", "name", "started_at", "status")

    def get_channel(self) -> discord.TextChannel | None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None or not self.channel_id:
            return None
        channel = guild.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def get_message(self) -> discord.Message | None:
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
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None
    position: int
    description: str
    showcase: bool
    start_date: str
    group_id: str
    page_id: str
    group: bool
    only_show_if_degraded: bool

    def __post_init__(self) -> None:
        self.created_at = utcparse(self.created_at)  # type: ignore[arg-type]
        self.updated_at = utcparse(self.updated_at)  # type: ignore[arg-type]


@dataclass
class Update:
    id: str
    status: str
    body: str
    incident_id: str
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None
    display_at: datetime.datetime | None
    affected_components: list[ShortComponent]
    deliver_notifications: bool
    custom_tweet: str
    tweet_id: str

    def __post_init__(self) -> None:
        self.created_at = utcparse(self.created_at)  # type: ignore[arg-type]
        self.updated_at = utcparse(self.updated_at)  # type: ignore[arg-type]
        self.display_at = utcparse(self.display_at)  # type: ignore[arg-type]

        if self.affected_components:
            self.affected_components = [ShortComponent(**component_data) for component_data in self.affected_components]  # type: ignore


@dataclass
class Incident:
    id: str
    name: str
    status: str
    created_at: datetime.datetime | None
    updated_at: datetime.datetime | None
    monitoring_at: datetime.datetime | None
    resolved_at: datetime.datetime | None
    impact: str
    shortlink: str
    started_at: datetime.datetime | None
    page_id: str
    incident_updates: list[Update]
    components: list[Component]
    reminder_intervals: Any

    def __post_init__(self) -> None:
        self.created_at = utcparse(self.created_at)  # type: ignore[arg-type]
        self.updated_at = utcparse(self.updated_at)  # type: ignore[arg-type]
        self.monitoring_at = utcparse(self.monitoring_at)  # type: ignore[arg-type]
        self.resolved_at = utcparse(self.resolved_at)  # type: ignore[arg-type]
        self.started_at = utcparse(self.started_at)  # type: ignore[arg-type]

        self.components = [Component(**component_data) for component_data in self.components]  # type: ignore

        if self.incident_updates:
            self.incident_updates = [Update(**update_data) for update_data in self.incident_updates]  # type: ignore

    def build_embed(self) -> discord.Embed:
        updates = self.incident_updates.copy()
        updates.reverse()

        embed = discord.Embed(
            title=self.name,
            timestamp=self.started_at,  # type: ignore[arg-type]
            url=self.shortlink,
            colour=Status(updates[-1].status).color,
        )
        embed.set_author(name="Discord Status", url="https://discordstatus.com/", icon_url=DISCORD_ICON_URL)
        embed.set_footer(text="Started at")

        for update in updates:
            embed.add_field(
                name=f"{Status(update.status).emoji} {update.status.title()} "
                f"({discord.utils.format_dt(update.created_at, 'R')})",  # type: ignore[arg-type]
                value=update.body,
                inline=False,
            )

        return embed

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class DiscordStatus(Cog):
    """Discord Status Feed related commands.

    Visit: https://discordstatus.com/ for more information.
    """

    emoji = "<:redinfo:1322338316435062974>"

    async def cog_load(self) -> None:
        self.check_new_incident.start()

    async def cog_unload(self) -> None:
        self.check_new_incident.stop()

    @cache.cache()
    async def get_subscribers(self) -> list[IncidentItem] | None:
        """|coro|

        Gets the open incidents from the database.

        Returns
        -------
        IncidentItem
            The open incident item.
        """

        records = await self.bot.db.incidents.get_all_subscribers()

        if not records:
            return None

        return [IncidentItem(bot=self.bot, record=record) for record in records]

    async def fetch_unresolved_incidents(self, bypass: bool = False) -> list[Incident] | None:
        """|coro|

        Fetches the latest incident from the Discord Status Feed.

        Returns
        -------
        list[Incident]
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
            return [Incident(**data) for data in data["incidents"]]

        return [
            Incident(**data)
            for data in data["incidents"]
            if (utcparse(data["updated_at"]) or discord.utils.utcnow())
            > discord.utils.utcnow() - datetime.timedelta(minutes=10)
        ]

    async def _compare_changes_and_update(self, incident: Incident, saved: IncidentItem) -> discord.Message | None:
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
            saved = IncidentItem(
                bot=self.bot, record=await self.bot.db.incidents.set_incident_id(incident.id, saved.guild_id)
            )

        if incident.id == saved.id:
            if incident.status == saved.status:
                return

            await self.bot.db.incidents.set_status(saved.id, saved.guild_id, incident.status)
        else:
            await self.bot.db.incidents.replace_incident(incident.id, saved.id, incident.status, saved.guild_id)

        channel = saved.get_channel()
        message = await saved.get_message()
        if not message:
            if channel is not None:
                with contextlib.suppress(discord.HTTPException):
                    message = await channel.send(embed=incident.build_embed())

            if message:
                await self.bot.db.incidents.set_message_id(message.id, saved.id, saved.guild_id)
        else:
            await message.edit(embed=incident.build_embed())

        self.get_subscribers.invalidate()

    @cache.cache()
    async def get_subscriber(self, guild_id: int) -> IncidentItem | None:
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

        record = await self.bot.db.incidents.get_subscriber(guild_id)

        if not record:
            return None
        return IncidentItem(bot=self.bot, record=record)

    @group(
        "discord-status",
        aliases=["dstatus"],
        fallback="show",
        description="Shows the current Discord Status.",
        guild_only=True,
    )
    async def dstatus(self, ctx: Context) -> None:
        """Shows the current Discord Status."""
        latest = await self.fetch_unresolved_incidents()
        if not latest:
            await ctx.send_error("No incidents found. *There should be though? Contact the developer!*")
            return

        embeds = [incident.build_embed() for incident in latest]
        content = (
            f"Displaying the **10** last incidents, ***{abs(10 - len(embeds))}** more incidents...*"
            if len(embeds) > 10
            else None
        )
        await ctx.send(content=content, embeds=embeds[:10], ephemeral=True)

    @dstatus.command(
        "release",
        description="Releases the last incident if not posted.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def dstatus_release(self, ctx: Context) -> None:
        """Releases the last incident again."""
        assert ctx.guild is not None
        subscriber = await self.get_subscriber(ctx.guild.id)  # type: ignore[misc]
        if not subscriber:
            await ctx.send_error("This guild is not subscribed to the Discord Status Feed.")
            return

        incidents = await self.fetch_unresolved_incidents(bypass=True)
        if not incidents:
            await ctx.send_error("No incidents found. *There should be though?* Contact the developer!")
            return
        latest = incidents[0]

        if not await self.bot.db.incidents.incident_exists(latest.id, ctx.guild.id):
            record = await self.bot.db.incidents.create_incident(
                latest.id, latest.status, subscriber.guild_id, subscriber.channel_id
            )
        else:
            record = await self.bot.db.incidents.update_incident_status(latest.id, latest.status, subscriber.guild_id)

        incident = IncidentItem(bot=self.bot, record=record)

        if incident.id == latest.id and incident.status == latest.status:
            await ctx.send_error("This incident is already released.")
            return

        incident_channel = incident.get_channel()
        if incident_channel is not None:
            message = await incident_channel.send(embed=latest.build_embed())

            if message:
                await self.bot.db.incidents.set_message_id(message.id, incident.id, ctx.guild.id)

        self.get_subscribers.invalidate()
        self.get_subscriber.invalidate(ctx.guild.id)

    @dstatus.command(
        "subscribe",
        description="Subscribe to Discord Status updates.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to subscribe to.")
    async def dstatus_subscribe(self, ctx: Context, channel: discord.TextChannel) -> None:
        """Subscribes to Discord Status updates."""
        assert ctx.guild is not None
        async with ctx.db.acquire() as connection:
            tr = connection.transaction()
            await tr.start()

            try:
                await ctx.db.incidents.create_subscription(ctx.guild.id, channel.id, connection=connection)  # type: ignore[arg-type]
            except Exception as e:
                # Rollback the transaction if anything goes wrong
                await tr.rollback()
                match e:
                    case asyncpg.UniqueViolationError():
                        await ctx.db.incidents.update_channel(ctx.guild.id, channel.id)

                        await ctx.send_success(f"Successfully updated the channel to [{channel.mention}].")
                    case _:
                        await ctx.send_error(f"An error occurred while subscribing to Discord Status updates: {e}")
                        return
            else:
                await tr.commit()
                await ctx.send_success(f"Successfully subscribed to Discord Status updates in [{channel.mention}].")

        self.get_subscribers.invalidate()
        self.get_subscriber.invalidate(ctx.guild.id)

    @dstatus.command(
        "unsubscribe",
        description="Unsubscribe from Discord Status updates.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def dstatus_unsubscribe(self, ctx: Context) -> None:
        """Unsubscribes from Discord Status updates."""
        assert ctx.guild is not None
        await ctx.db.incidents.unsubscribe(ctx.guild.id)

        self.get_subscribers.invalidate()
        self.get_subscriber.invalidate(ctx.guild.id)

        await ctx.send_success("Successfully unsubscribed from Discord Status updates.")

    @tasks.loop(minutes=3)
    async def check_new_incident(self) -> None:
        """|coro|

        Checks for new incidents and updates the subscribers.
        This is a loop that runs every 3 minutes.
        The bot calls this automatically.
        """
        await self.bot.wait_until_ready()

        incidents = await self.fetch_unresolved_incidents()

        if not incidents:
            return

        subscribers = await self.get_subscribers()  # type: ignore[misc]

        if not subscribers:
            return

        for incident in incidents:
            for subscriber in subscribers:
                await self._compare_changes_and_update(incident, subscriber)


async def setup(bot: Bot) -> None:
    await bot.add_cog(DiscordStatus(bot))
