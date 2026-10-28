from __future__ import annotations

import contextlib
import dataclasses
import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp
import asyncpg
import discord
from discord.ext import tasks

from app.core.components_v2 import Accent, NoticeView
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.utils import cache, utcparse
from config import Emojis

if TYPE_CHECKING:
    from app.core import Bot

log = logging.getLogger(__name__)

DS_ENDPOINT = "https://discordstatus.com/api/v2/incidents.json"
DS_HOMEPAGE = "https://discordstatus.com/"
DISCORD_ICON_URL = "https://klappstuhl.me/gallery/raw/EzWyA.png"

#: An incident updated within this window counts as "active" for posting. Change detection
#: (status + latest update id) keeps re-checks idempotent, so the window only has to outlast
#: a few failed polls — it no longer has to be tight to avoid duplicate posts.
RECENT_WINDOW = datetime.timedelta(minutes=30)

#: Discord's per-message display-text budget for a Components V2 layout.
_TEXT_BUDGET = 4000
#: Longest single update body kept before truncation.
_BODY_LIMIT = 1000
#: Most updates rendered on one card (each costs two components out of 40).
_MAX_UPDATES = 10

RESOLVED_STATES = frozenset({"resolved", "postmortem", "completed"})
MAINTENANCE_STATES = frozenset({"scheduled", "in_progress", "verifying"})

#: Statuspage update status -> status dot. Unknown statuses fall back to ``offline``.
_STATUS_EMOJI: dict[str, str] = {
    "resolved": Emojis.Status.online,
    "postmortem": Emojis.Status.online,
    "completed": Emojis.Status.online,
    "investigating": Emojis.Status.idle,
    "monitoring": Emojis.Status.idle,
    "verifying": Emojis.Status.idle,
    "in_progress": Emojis.Status.idle,
    "identified": Emojis.Status.dnd,
    "scheduled": Emojis.Status.offline,
    "update": Emojis.Status.offline,
}


def status_label(status: str) -> str:
    return status.replace("_", " ").title()


def incident_accent(status: str, impact: str | None) -> Accent:
    """Pick the container rail colour: resolved > maintenance > major outage > in-progress."""
    if status in RESOLVED_STATES:
        return Accent.success
    if impact == "maintenance" or status in MAINTENANCE_STATES:
        return Accent.info
    if impact in ("major", "critical"):
        return Accent.error
    return Accent.warning


def _from_payload[T](cls: type[T], data: dict[str, Any]) -> T:
    """Build a dataclass from an API dict, ignoring unknown keys and defaulting missing ones to ``None``.

    Statuspage adds fields over time; a strict ``cls(**data)`` would raise inside the poll loop.
    """
    return cls(**{f.name: data.get(f.name) for f in dataclasses.fields(cls)})  # type: ignore[arg-type]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class IncidentItem(BaseRecord, table="discord_incidents", pk="guild_id"):
    """Represents a Discord Status Feed incident item."""

    bot: Bot
    id: str | None
    name: str
    status: str | None
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
        self.affected_components = [
            _from_payload(ShortComponent, c)  # type: ignore[arg-type]
            for c in (self.affected_components or ())
        ]

    def render(self) -> str:
        when = self.display_at or self.created_at
        stamp = f" · {discord.utils.format_dt(when, 'R')} ({discord.utils.format_dt(when, 'f')})" if when else ""
        emoji = _STATUS_EMOJI.get(self.status, Emojis.Status.offline)
        body = _truncate((self.body or "").strip() or "*No details provided.*", _BODY_LIMIT)
        return f"{emoji} **{status_label(self.status)}**{stamp}\n{body}"


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

        self.components = [_from_payload(Component, c) for c in (self.components or ())]  # type: ignore[arg-type]
        updates = [_from_payload(Update, u) for u in (self.incident_updates or ())]  # type: ignore[arg-type]
        # Latest first, regardless of the order the API happened to return.
        epoch = datetime.datetime.min.replace(tzinfo=datetime.UTC)
        updates.sort(key=lambda u: u.display_at or u.created_at or epoch, reverse=True)
        self.incident_updates = updates

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Incident:
        return _from_payload(cls, data)

    @property
    def latest_update_id(self) -> str | None:
        return self.incident_updates[0].id if self.incident_updates else None

    @property
    def url(self) -> str:
        return self.shortlink or f"{DS_HOMEPAGE}incidents/{self.id}"

    def affected_names(self) -> list[str]:
        """Affected component names, from the updates first, falling back to the incident's list."""
        names: dict[str, None] = {}
        for update in self.incident_updates:
            for component in update.affected_components:
                if component.name:
                    names.setdefault(component.name)
        if not names:
            for component in self.components:
                if component.name:
                    names.setdefault(component.name)
        return list(names)

    def is_recent(self, now: datetime.datetime | None = None) -> bool:
        stamp = self.updated_at or self.created_at
        if stamp is None:
            return False
        return stamp > (now or discord.utils.utcnow()) - RECENT_WINDOW

    def build_container(self, *, max_updates: int = _MAX_UPDATES) -> discord.ui.Container:
        """The incident as a CV2 card: linked heading, affected components, latest-first timeline, footer."""
        accent = incident_accent(self.status, self.impact)
        container = discord.ui.Container(accent_colour=accent.colour)

        impact = f" · {self.impact.title()} impact" if self.impact and self.impact != "none" else ""
        heading = (
            f"-# Discord Status{impact}\n"
            f"## [{_truncate(self.name or 'Untitled incident', 200)}]({self.url})\n"
            f"{_STATUS_EMOJI.get(self.status, Emojis.Status.offline)} **{status_label(self.status)}**"
        )
        container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(DISCORD_ICON_URL)))

        texts: list[str] = [heading]
        affected = self.affected_names()
        if affected:
            text = _truncate("**Affected components**\n" + ", ".join(affected), 500)
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(text))
            texts.append(text)

        started = self.started_at or self.created_at
        footer = f"-# Started {discord.utils.format_dt(started, 'f')} · " if started else "-# "
        footer += f"[discordstatus.com]({DS_HOMEPAGE})"
        texts.append(footer)

        budget = _TEXT_BUDGET - sum(map(len, texts)) - 100  # headroom for the "earlier updates" note
        shown = 0
        for update in self.incident_updates[:max_updates]:
            text = update.render()
            if len(text) > budget:
                break
            # Large separator before the timeline, small ones between its entries.
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small) if shown else discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(text))
            budget -= len(text)
            shown += 1

        hidden = len(self.incident_updates) - shown
        if hidden > 0:
            container.add_item(discord.ui.TextDisplay(
                f"-# +{hidden} earlier update{'s' if hidden != 1 else ''} on the [status page]({self.url})"
            ))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(footer))
        return container

    def build_view(self) -> NoticeView:
        view = NoticeView(self.build_container())
        row = discord.ui.ActionRow()
        row.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="View incident", url=self.url))
        view.add_item(row)
        return view

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class DiscordStatusMixin:
    """Discord Status Feed related commands.

    Visit: https://discordstatus.com/ for more information.
    """

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)  # type: ignore[call-arg]
        # (guild_id, incident_id) -> latest incident update id already rendered. In-memory on purpose:
        # after a restart the first poll re-edits a recent card once, which is idempotent.
        self._seen_updates: dict[tuple[int, str], str | None] = {}

    async def _setup_status(self) -> None:
        self.check_new_incident.start()

    async def _teardown_status(self) -> None:
        self.check_new_incident.stop()

    @cache.cache()
    async def get_subscribers(self) -> list[IncidentItem] | None:
        """|coro|

        Gets every subscribed guild's tracked incident row.
        """
        records = await self.bot.db.incidents.get_all_subscribers()

        if not records:
            return None

        return [IncidentItem(bot=self.bot, record=record) for record in records]

    async def fetch_incidents(self) -> list[Incident] | None:
        """|coro|

        Fetches the recent incidents from the Discord Status Feed, newest first.
        Returns ``None`` on any transport or payload failure so the poll just skips a tick.
        """
        try:
            async with self.bot.session.get(DS_ENDPOINT) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            # Transient DNS/network blips or a non-JSON body: skip this tick instead of a traceback.
            return None

        try:
            incidents = [Incident.from_payload(item) for item in (data or {}).get("incidents") or ()]
        except (TypeError, ValueError, AttributeError):
            log.warning("Unexpected Discord status payload shape", exc_info=True)
            return None

        epoch = datetime.datetime.min.replace(tzinfo=datetime.UTC)
        incidents.sort(key=lambda i: i.started_at or i.created_at or epoch, reverse=True)
        return incidents

    async def _load_posts(self, saved: IncidentItem) -> dict[str, tuple[int, str | None]]:
        """The guild's posted cards: incident id -> (message id, status last rendered)."""
        rows = await self.bot.db.incidents.get_posts(saved.guild_id)
        return {r['incident_id']: (r['message_id'], r['status']) for r in rows}

    async def _publish(
        self, saved: IncidentItem, incident: Incident, *, edit: bool, post: tuple[int, str | None] | None = None
    ) -> bool:
        """|coro|

        Render ``incident`` into the subscriber's channel and record the post.

        ``edit`` re-renders the incident's own earlier message (``post``) in place (legacy embed
        posts are converted by clearing content/embeds; if Discord refuses, the old post is deleted
        and reposted). Otherwise a fresh message is posted. State is only written once a message
        exists, so a failed send is retried on the next poll rather than silently skipped.
        """
        channel = saved.get_channel()
        if channel is None:
            return False

        view = incident.build_view()
        message: discord.Message | None = None
        if edit and post is not None:
            partial = channel.get_partial_message(post[0])
            try:
                message = await partial.edit(content=None, embeds=[], attachments=[], view=view)
            except discord.HTTPException:
                with contextlib.suppress(discord.HTTPException):
                    await partial.delete()

        if message is None:
            try:
                message = await channel.send(view=view)
            except discord.HTTPException as exc:
                log.info("Could not post Discord status to %s in guild %s: %s", channel.id, saved.guild_id, exc)
                return False

        incidents = self.bot.db.incidents
        await incidents.upsert_post(saved.guild_id, incident.id, message.id, incident.status)
        self._seen_updates[(saved.guild_id, incident.id)] = incident.latest_update_id

        # The guild row mirrors the newest-posted incident (the `show`/subscribe checks read it); a
        # concurrent older incident being edited must not rewind it.
        if not edit or saved.id in (None, incident.id):
            if saved.id != incident.id:
                await incidents.set_incident_id(incident.id, saved.guild_id)
            await incidents.set_status(incident.id, saved.guild_id, incident.status)
            if message.id != saved.message_id:
                await incidents.set_message_id(message.id, incident.id, saved.guild_id)
            # Keep the (cached) row in step so later incidents in the same tick see the new state.
            saved.id, saved.status, saved.message_id = incident.id, incident.status, message.id
        return True

    async def _sync_subscriber(self, saved: IncidentItem, incidents: list[Incident]) -> bool:
        """|coro|

        Bring one subscriber up to date. Returns whether anything was written.

        Every incident gets its own card, tracked per ``(guild, incident)``: an incident that was
        already posted is edited in place when its status or latest update changes (however many
        newer ones followed), and a still-active incident with no card gets a fresh post, oldest
        first. Resolved incidents past the recency window are never re-posted.
        """
        posts = await self._load_posts(saved)
        changed = False
        for incident in reversed(incidents):
            post = posts.get(incident.id)
            if post is not None:
                if post[1] != incident.status or (
                    incident.is_recent()
                    and self._seen_updates.get((saved.guild_id, incident.id)) != incident.latest_update_id
                ):
                    changed |= await self._publish(saved, incident, edit=True, post=post)
            elif incident.is_recent():
                changed |= await self._publish(saved, incident, edit=False)
        return changed

    @cache.cache()
    async def get_subscriber(self, guild_id: int) -> IncidentItem | None:
        """|coro|

        The guild's subscription row, or ``None`` when it is not subscribed.
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
        incidents = await self.fetch_incidents()
        if not incidents:
            await ctx.send_error("Could not reach the Discord status page right now. Try again in a moment.")
            return

        active = [incident for incident in incidents if incident.status not in RESOLVED_STATES]
        if not active:
            # All clear: show the most recent incident for context.
            container = incidents[0].build_container(max_updates=3)
            container.add_item(discord.ui.TextDisplay(f"{Emojis.Status.online} **All systems operational.** Latest incident shown above."))
            await ctx.send(view=NoticeView(container), ephemeral=True)
            return

        container = active[0].build_container()
        if len(active) > 1:
            others = "\n".join(f"- [{_truncate(i.name, 100)}]({i.url})" for i in active[1:6])
            container.add_item(discord.ui.TextDisplay(f"-# Also active:\n{others}"))
        await ctx.send(view=NoticeView(container), ephemeral=True)

    @dstatus.command(
        "release",
        description="Releases the last incident if not posted.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def dstatus_release(self, ctx: Context) -> None:
        """Posts the latest incident to the subscribed channel if it is not already there."""
        assert ctx.guild is not None
        record = await self.bot.db.incidents.get_subscriber(ctx.guild.id)
        if not record:
            await ctx.send_error("This guild is not subscribed to the Discord Status Feed.")
            return
        subscriber = IncidentItem(bot=self.bot, record=record)

        incidents = await self.fetch_incidents()
        if not incidents:
            await ctx.send_error("Could not reach the Discord status page right now. Try again in a moment.")
            return
        latest = incidents[0]

        channel = subscriber.get_channel()
        if channel is None:
            await ctx.send_error("The subscribed channel no longer exists. Re-run `discord-status subscribe`.")
            return

        if subscriber.id == latest.id and subscriber.status == latest.status and subscriber.message_id:
            with contextlib.suppress(discord.HTTPException):
                await channel.fetch_message(subscriber.message_id)
                await ctx.send_error("This incident is already released.")
                return

        if not await self._publish(subscriber, latest, edit=False):
            await ctx.send_error(f"I could not post in {channel.mention}. Check my permissions there.")
            return

        self.get_subscribers.invalidate()
        self.get_subscriber.invalidate(ctx.guild.id)
        await ctx.send_success(f"Released the latest incident in {channel.mention}.")

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

        Polls the status page every 3 minutes and brings every subscriber up to date.
        One guild's failure (deleted channel, missing permission, DB hiccup) is logged and
        skipped — an exception escaping a ``tasks.loop`` would stop polling for good.
        """
        await self.bot.wait_until_ready()

        incidents = await self.fetch_incidents()
        if not incidents:
            return

        subscribers = await self.get_subscribers()  # type: ignore[misc]
        if not subscribers:
            return

        changed = False
        for subscriber in subscribers:
            try:
                changed |= await self._sync_subscriber(subscriber, incidents)
            except Exception:
                log.exception("Discord status sync failed for guild %s", subscriber.guild_id)

        if changed:
            self.get_subscribers.invalidate()
