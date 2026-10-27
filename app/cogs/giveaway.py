from __future__ import annotations

import datetime
import logging
import random
import re
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from discord.utils import MISSING

from app.core import Bot, Cog, Flags, flag
from app.core.components_v2 import Accent
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.services import EventExtractor
from app.utils import checks, fuzzy, get_shortened_string, timetools
from config import Emojis

if TYPE_CHECKING:
    from discord.app_commands import Choice

    from app.core.timer import Timer

log = logging.getLogger(__name__)

GiveawayState = Literal["running", "ended", "cancelled"]

# Discord caps a CV2 message at 4000 characters of text across every TextDisplay.
PRIZE_LIMIT = 256
DESCRIPTION_LIMIT = 1000
SHOWN_WINNERS_LIMIT = 50

WINNER_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\N{HORIZONTAL ELLIPSIS}"


def _mention_list(winners: list[discord.Member]) -> str:
    shown = ", ".join(w.mention for w in winners[:SHOWN_WINNERS_LIMIT])
    hidden = len(winners) - SHOWN_WINNERS_LIMIT
    return f"{shown} and {hidden} more" if hidden > 0 else shown


class GiveawayCreateFlags(Flags):
    winners: commands.Range[int, 1, 100] = flag(aliases=("winner", "win"), short="w", default=1)
    description: str = flag(aliases=("msg", "description", "desc", "comment"), short="d")
    channel: discord.TextChannel = flag(aliases=("chan", "c"), short="ch")


class GiveawayRerollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"giveaway:reroll:(?P<id>[0-9]+)"):
    def __init__(self, giveaway: Giveaway | None) -> None:
        self.giveaway: Giveaway | None = giveaway
        super().__init__(
            discord.ui.Button(
                label="Reroll",
                style=discord.ButtonStyle.gray,
                emoji="\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}",
                custom_id=f"giveaway:reroll:{giveaway.id if giveaway else 0}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> GiveawayRerollButton:
        cog: Giveaways | None = interaction.client.get_cog("Giveaways")  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AssertionError(f"{Emojis.error} Giveaways cog is not loaded")
        return cls(await cog.get_giveaway(int(match["id"])))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.giveaway is None:
            await interaction.response.send_message(f"{Emojis.error} This giveaway no longer exists.", ephemeral=True)
            return False

        # The host, or anyone who can manage the server (dashboard giveaways are hosted by the bot itself).
        if interaction.user.id != self.giveaway.author_id and not interaction.permissions.manage_guild:
            await interaction.response.send_message(
                f"{Emojis.error} Only the host or a server manager can reroll this giveaway.", ephemeral=True
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.giveaway is not None
        giveaway = self.giveaway
        winners = await giveaway.get_winners()
        # content/embed=None also upgrades a legacy plain-text announcement to the CV2 card.
        await interaction.response.edit_message(
            content=None, embed=None, view=winners_layout(giveaway, winners, rerolled=True),
            allowed_mentions=WINNER_MENTIONS,
        )
        if winners:
            await giveaway.edit_message(giveaway.to_layout(winners=winners))


class GiveawayEnterButton(discord.ui.DynamicItem[discord.ui.Button], template=r"giveaway:enter:(?P<id>[0-9]+)"):
    """The persistent Enter button. The custom_id format is load-bearing: legacy embed giveaways carry it too."""

    def __init__(self, giveaway: Giveaway | None) -> None:
        self.giveaway: Giveaway | None = giveaway
        super().__init__(
            discord.ui.Button(
                label="Enter",
                style=discord.ButtonStyle.green,
                emoji=Emojis.giveaway,  # type: ignore
                custom_id=f"giveaway:enter:{giveaway.id if giveaway else 0}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> GiveawayEnterButton:
        cog: Giveaways | None = interaction.client.get_cog("Giveaways")  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AssertionError(f"{Emojis.error} Giveaways cog is not loaded")
        return cls(await cog.get_giveaway(int(match["id"])))

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        giveaway = self.giveaway
        if giveaway is None:
            await interaction.response.send_message(f"{Emojis.error} This giveaway no longer exists.", ephemeral=True)
            return False

        error = entry_error(giveaway, interaction.user.id)
        if error is not None:
            await interaction.response.send_message(f"{Emojis.error} {error}", ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> None:
        assert self.giveaway is not None
        # Atomic append: concurrent clicks each land instead of the last full-set write winning.
        if not await interaction.client.db.giveaways.add_entry(self.giveaway.id, interaction.user.id):  # type: ignore
            await interaction.response.send_message(
                f"{Emojis.error} You have already entered this giveaway.", ephemeral=True
            )
            return
        self.giveaway.entries.add(interaction.user.id)

        # content/embed=None also upgrades a legacy embed giveaway to the CV2 card on its first entry.
        await interaction.response.edit_message(content=None, embed=None, view=self.giveaway.to_layout())
        await interaction.followup.send(f"{Emojis.success} You have successfully entered this giveaway.", ephemeral=True)


def entry_error(giveaway: Giveaway, user_id: int) -> str | None:
    """Why ``user_id`` may not enter, or ``None`` when they may."""
    if giveaway.ended:
        return "This giveaway has already ended."
    if user_id == giveaway.author_id:
        return "You cannot enter your own giveaway."
    if user_id in giveaway.entries:
        return "You have already entered this giveaway."
    return None


def winners_layout(giveaway: Giveaway, winners: list[discord.Member], *, rerolled: bool = False) -> discord.ui.LayoutView:
    """The winner announcement replied under the giveaway, with a Reroll button while entrants remain."""
    prize = _clip(giveaway.prize or "Giveaway", PRIZE_LIMIT)
    container = discord.ui.Container(accent_colour=(Accent.success if winners else Accent.neutral).colour)
    if winners:
        verb = "rerolled" if rerolled else "ended"
        container.add_item(discord.ui.TextDisplay(
            f"### {Emojis.giveaway} Giveaway {verb}\n"
            f"Congratulations {_mention_list(winners)}! You won **{prize}**!"
        ))
    else:
        reason = "No entrants are left to reroll" if rerolled else "No valid entrants"
        container.add_item(discord.ui.TextDisplay(f"### {Emojis.error} No winners\n{reason} for **{prize}**."))

    if giveaway.jump_url:
        container.add_item(discord.ui.TextDisplay(f"-# [Jump to the giveaway]({giveaway.jump_url}) \N{BULLET} ID {giveaway.id}"))
    if winners and giveaway.entries:
        container.add_item(discord.ui.ActionRow(GiveawayRerollButton(giveaway)))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


class Giveaway(BaseRecord, table="giveaways", pk="id"):
    """Represents a giveaway item."""

    cog: Giveaways
    id: int
    channel_id: int
    message_id: int
    guild_id: int
    author_id: int
    entries: set[int]
    metadata: dict[str, Any]

    __slots__ = (
        "args",
        "author_id",
        "channel_id",
        "cog",
        "description",
        "entries",
        "guild_id",
        "id",
        "kwargs",
        "message",
        "message_id",
        "metadata",
        "prize",
        "winner_count",
    )

    def __init__(self, **kwargs: Any) -> None:
        self.message: discord.Message | None = MISSING
        super().__init__(**kwargs)

    def _coerce(self) -> None:
        self.args = self.metadata.get("args", [])
        self.kwargs = self.metadata.get("kwargs", {})
        self.prize = self.kwargs.get("prize")
        self.description = self.kwargs.get("description")
        self.winner_count = self.kwargs.get("winner_count", 0)
        self.entries = set(self.entries or [])

    @property
    def bot(self) -> Bot:
        # Never stored on the record: every Giveaway is built with its cog, which owns the bot.
        return self.cog.bot

    @property
    def choice_text(self) -> str:
        """The text to use for the autocomplete."""
        return f"[{self.id}] {self.prize}"

    @property
    def guild(self) -> discord.Guild | None:
        """The guild of the giveaway."""
        return self.bot.get_guild(self.guild_id)

    @property
    def jump_url(self) -> str | None:
        """The jump URL for the giveaway message."""
        if self.message_id and self.channel_id:
            guild = self.guild_id or "@me"
            return f"https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}"
        return None

    @property
    def entry_count(self) -> int:
        """The number of entries in the giveaway."""
        return len(self.entries)

    @property
    def created(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.kwargs["created"])

    @property
    def expires(self) -> datetime.datetime:
        expires = datetime.datetime.fromisoformat(self.kwargs["expires"])
        return expires if expires.tzinfo else expires.replace(tzinfo=datetime.UTC)

    @property
    def ended(self) -> bool:
        return bool(self.kwargs.get("ended")) or self.expires <= discord.utils.utcnow()

    def to_layout(
        self, *, winners: list[discord.Member] | None = None, state: GiveawayState | None = None
    ) -> discord.ui.LayoutView:
        """The giveaway card. ``state`` defaults to running/ended from the record; the Enter button only shows while running."""
        state = state or ("ended" if winners is not None or self.ended else "running")
        accent = {"running": Accent.brand, "ended": Accent.success, "cancelled": Accent.neutral}[state]
        container = discord.ui.Container(accent_colour=accent.colour)

        prize = _clip(self.prize or "Giveaway", PRIZE_LIMIT)
        heading = f"## {Emojis.giveaway} ~~{prize}~~" if state == "cancelled" else f"## {Emojis.giveaway} {prize}"
        if self.description and state != "cancelled":
            heading += f"\n{_clip(self.description, DESCRIPTION_LIMIT)}"
        container.add_item(discord.ui.TextDisplay(heading))
        container.add_item(discord.ui.Separator())

        when = f"{discord.utils.format_dt(self.expires, 'R')} ({discord.utils.format_dt(self.expires, 'f')})"
        lines = [f"**Hosted by** <@{self.author_id}>", f"**Entries** {self.entry_count}"]
        if state == "running":
            lines.insert(0, f"**Ends** {when}")
            lines.append(f"**Winners** {self.winner_count}")
        elif state == "ended":
            lines.insert(0, f"**Ended** {when}")
            lines.append(f"**Winners** {_mention_list(winners) if winners else 'No valid entrants'}")
        else:
            lines.insert(0, "**This giveaway was cancelled.** No winners will be drawn.")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        if state == "running":
            container.add_item(discord.ui.ActionRow(GiveawayEnterButton(self)))
        container.add_item(discord.ui.TextDisplay(f"-# Giveaway ID {self.id}"))

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    async def fetch_message(self) -> discord.Message | None:
        """Fetches (and caches) the giveaway message; ``None`` when it is gone or unreachable."""
        if self.message is not MISSING:
            return self.message
        self.message = None
        guild = self.guild
        channel = guild.get_channel_or_thread(self.channel_id) if guild else None
        if isinstance(channel, discord.abc.Messageable) and self.message_id:
            try:
                self.message = await channel.fetch_message(self.message_id)
            except discord.HTTPException:
                pass
        return self.message

    async def edit_message(self, view: discord.ui.LayoutView) -> bool:
        """Swap the giveaway message for ``view`` (clearing a legacy embed); ``False`` when it could not be edited."""
        message = await self.fetch_message()
        if message is None:
            return False
        try:
            await message.edit(content=None, embed=None, view=view)
        except discord.HTTPException:
            return False
        return True

    async def get_winners(self) -> list[discord.Member]:
        """Draws up to ``winner_count`` distinct winners and removes them from the pool (so a reroll can't repeat them).

        Entrants who left the server, bots and the host are discarded without consuming a winner slot.
        """
        guild = self.guild
        if guild is None:  # unavailable guild: draw nothing rather than burn the whole pool
            return []
        winners: list[discord.Member] = []
        pool = list(self.entries)
        random.shuffle(pool)
        for user_id in pool:
            if len(winners) >= self.winner_count:
                break
            self.entries.discard(user_id)
            if user_id == self.author_id:
                continue
            member = await self.bot.get_or_fetch_member(guild, user_id)
            if member is not None and not member.bot:
                winners.append(member)

        await self.bot.db.giveaways.set_entries(self.id, self.entries)
        return winners


class Giveaways(Cog):
    """Create Giveaways using Modals."""

    emoji = Emojis.giveaway

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        # Phase 5: natural-language → structured giveaway fields (gated on AIFlags.giveaways).
        self._event_ai: EventExtractor = EventExtractor(bot.ai)

        bot.add_dynamic_items(GiveawayEnterButton, GiveawayRerollButton)

    async def giveaway_id_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[int | str | float]]:
        assert interaction.guild is not None
        giveaways = [g for g in await self.get_guild_giveaways(interaction.guild.id) if not g.kwargs.get("ended")]
        results = fuzzy.finder(current, giveaways, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, giveaway.choice_text), value=giveaway.id)
            for length, start, giveaway in results[:20]
        ]

    async def get_giveaway(self, giveaway_id: int) -> Giveaway | None:
        """|coro| Gets a giveaway by ID, or ``None``."""
        record = await self.bot.db.giveaways.get_giveaway(giveaway_id)
        return Giveaway(cog=self, record=record) if record else None

    async def get_guild_giveaway(self, guild_id: int, giveaway_id: int) -> Giveaway | None:
        """|coro| Gets a giveaway by ID scoped to a guild, or ``None``."""
        record = await self.bot.db.giveaways.get_guild_giveaway(guild_id, giveaway_id)
        return Giveaway(cog=self, record=record) if record else None

    async def get_guild_giveaways(self, guild_id: int) -> list[Giveaway]:
        """|coro| Gets all the giveaways in a guild."""
        records = await self.bot.db.giveaways.get_guild_giveaways(guild_id)
        return [Giveaway(cog=self, record=record) for record in records]

    async def create_giveaway(
        self,
        channel_id: int,
        message_id: int,
        guild_id: int,
        author_id: int,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Giveaway:
        """|coro| Inserts a giveaway row. ``args``/``kwargs`` land in the JSON ``metadata`` and must be serializable."""
        metadata = {"args": args, "kwargs": kwargs}
        giveaway_id = await self.bot.db.giveaways.create_giveaway(channel_id, message_id, guild_id, author_id, metadata)
        return Giveaway(
            cog=self,
            record={
                "id": giveaway_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "guild_id": guild_id,
                "author_id": author_id,
                "entries": [],
                "metadata": metadata,
            },
        )

    async def start_giveaway(
        self,
        channel: discord.abc.Messageable,
        guild_id: int,
        author_id: int,
        *,
        prize: str,
        description: str | None,
        winners: int,
        expires: datetime.datetime,
        timezone: str = "UTC",
    ) -> tuple[Giveaway, discord.Message]:
        """|coro| Posts the giveaway card, stores the row and schedules the end timer. Shared by commands and the API."""
        message = await channel.send(embed=discord.Embed(description="*Preparing Giveaway...*"))
        now = discord.utils.utcnow()
        giveaway = await self.create_giveaway(
            message.channel.id,
            message.id,
            guild_id,
            author_id,
            description=description,
            prize=prize,
            winner_count=winners,
            created=now.isoformat(),
            expires=expires.isoformat(),
        )
        await self.bot.timers.create(expires, "giveaway", giveaway_id=giveaway.id, created=now, timezone=timezone)
        giveaway.message = message
        await message.edit(embed=None, view=giveaway.to_layout())
        return giveaway, message

    async def end_giveaway(self, giveaway_id: int) -> None:
        """|coro| Ends a giveaway now by deleting its timer and dispatching the timer event manually."""
        timer = await self.bot.timers.fetch("giveaway", giveaway_id=str(giveaway_id))
        if timer is None:  # already fired (or never scheduled): the listener would crash on ``None``
            return
        # Delete first so the timer loop can't fire the same giveaway while we end it.
        await self.bot.timers.delete("giveaway", giveaway_id=str(giveaway_id))
        self.bot.dispatch("giveaway_timer_complete", timer)

    @group("giveaway", description="Manage giveaways.", guild_only=True, hybrid=True)
    async def giveaway(self, ctx: Context) -> None:
        """Manage giveaways."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @giveaway.command("create", description="Create a giveaway.", guild_only=True, user_permissions=PermissionTemplate.mod)
    @checks.requires_timer()
    @describe(when="When the giveaway ends.", prize="The prize for the giveaway.")
    async def giveaway_create(
        self, ctx: Context, when: timetools.FutureTime, *, prize: str, flags: GiveawayCreateFlags
    ) -> None:
        """Creates a giveaway."""
        assert ctx.guild is not None
        channel = flags.channel or ctx.channel
        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        giveaway, message = await self.start_giveaway(
            channel, ctx.guild.id, ctx.author.id, prize=prize, description=flags.description,
            winners=flags.winners, expires=when.dt, timezone=zone or "UTC",
        )
        await ctx.send_success(f"Giveaway [`{giveaway.id}`] successfully created. {message.jump_url}", ephemeral=True)

    @giveaway.command("quick", description="Create a giveaway from a plain-language description (AI).",
                      guild_only=True, user_permissions=PermissionTemplate.mod)
    @checks.requires_timer()
    @describe(description="Describe the giveaway: the prize, how many winners, and how long it runs.")
    async def giveaway_quick(self, ctx: Context, *, description: str) -> None:
        """Create a giveaway by describing it in plain language — the AI fills in the details.

        e.g. "give away a Nitro classic to 2 winners for 1 day". Falls back to
        `giveaway create` when AI is unavailable.
        """
        assert ctx.guild is not None
        if not self.bot.ai.available:
            await ctx.send_error(f"The AI assistant is unavailable — use `{ctx.clean_prefix}giveaway create` instead.")
            return
        ai_config = await self.bot.db.get_guild_ai_config(ctx.guild.id)
        if not ai_config.is_enabled("giveaways", ctx.channel.id):
            await ctx.send_error(
                f"AI giveaways aren't enabled in this channel. A moderator can enable them on the "
                f"dashboard, or use `{ctx.clean_prefix}giveaway create`."
            )
            return

        async with ctx.typing():
            request = await self._event_ai.giveaway(description)

        if request is None:
            await ctx.send_error(
                f"I couldn't turn that into a giveaway. Try `{ctx.clean_prefix}giveaway create` directly."
            )
            return

        try:
            when = await timetools.FutureTime.convert(ctx, request.duration)
        except commands.BadArgument:
            await ctx.send_error(f"I couldn't understand the duration `{request.duration}`.")
            return

        channel = self._resolve_quick_channel(ctx, request.channel, description)
        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        winners = max(1, min(request.winners, 100))
        giveaway, message = await self.start_giveaway(
            channel, ctx.guild.id, ctx.author.id, prize=request.prize, description=request.description,
            winners=winners, expires=when.dt, timezone=zone or "UTC",
        )

        winners_label = f"{winners} winner{'s' if winners != 1 else ''}"
        await ctx.send_success(
            f"Giveaway [`{giveaway.id}`] for **{request.prize}** ({winners_label}) created in "
            f"{message.channel.mention} — ends {discord.utils.format_dt(when.dt, 'R')}. {message.jump_url}",  # type: ignore[union-attr]
            ephemeral=True,
        )

    def _resolve_quick_channel(self, ctx: Context, name: str | None, raw: str) -> discord.abc.Messageable:
        """Resolve the target channel for a quick giveaway, or fall back to the current one.

        Prefers an actual ``#channel`` mention in the message (the reliable signal — a typed
        ``#name`` becomes ``<#id>``), then an AI-named channel matched by name. Only returns a
        channel the bot can post in.
        """
        if ctx.guild is None:
            return ctx.channel
        me = ctx.guild.me

        # 1. A real channel mention (<#id>) anywhere in the request.
        mention = re.search(r"<#(\d+)>", raw)
        if mention is not None:
            channel = ctx.guild.get_channel(int(mention.group(1)))
            if isinstance(channel, discord.TextChannel) and channel.permissions_for(me).send_messages:
                return channel

        # 2. A plain channel name the model picked out.
        if name:
            lowered = name.lower()
            match = discord.utils.find(lambda c: c.name.lower() == lowered, ctx.guild.text_channels) or \
                discord.utils.find(lambda c: lowered in c.name.lower(), ctx.guild.text_channels)
            if match is not None and match.permissions_for(me).send_messages:
                return match

        return ctx.channel

    @giveaway.command("end", description="End a giveaway.", guild_only=True, user_permissions=PermissionTemplate.mod)
    @app_commands.autocomplete(giveaway_id=giveaway_id_autocomplete)  # type: ignore
    @describe(giveaway_id="The ID of the giveaway to end.")
    async def giveaway_end(self, ctx: Context, giveaway_id: int) -> None:
        """Ends a giveaway."""
        assert ctx.guild is not None
        await ctx.defer()

        giveaway = await self.get_guild_giveaway(ctx.guild.id, giveaway_id)
        if giveaway is None:
            raise commands.BadArgument(f"Giveaway with ID `{giveaway_id}` was not found.")
        if giveaway.kwargs.get("ended"):
            raise commands.BadArgument(f"Giveaway with ID `{giveaway_id}` has already ended.")

        await self.end_giveaway(giveaway.id)
        await ctx.send_success(f"Giveaway [`{giveaway.id}`] has been ended manually.")

    @Cog.listener()
    async def on_giveaway_timer_complete(self, timer: Timer) -> None:
        """|coro| Draws the winners, swaps the card to its ended state and announces the result."""
        await self.bot.wait_until_ready()
        _id: int | None = timer.kwargs.get("giveaway_id")
        if _id is None:
            log.warning("Giveaway timer %s is missing giveaway_id", timer.id)
            return

        giveaway = await self.get_giveaway(int(_id))
        if giveaway is None:  # cancelled from the dashboard after the timer was armed
            return

        # Keep the row (marked ended) so the Reroll button can still find it; the first caller wins the draw.
        ended_at = discord.utils.utcnow().isoformat()
        if not await self.bot.db.giveaways.mark_ended(giveaway.id, ended_at):
            return
        giveaway.kwargs.update(expires=ended_at, ended=True)

        winners = await giveaway.get_winners()
        self.bot.dispatch("percy_event", giveaway.guild_id, "giveaway.ended", {
            "giveaway_id": giveaway.id,
            "prize": giveaway.prize,
            "winner_ids": [str(w.id) for w in winners],
        })

        message = await giveaway.fetch_message()
        if message is None:  # the post was deleted: the draw still stands, there is just nowhere to announce it
            return
        try:
            await message.edit(content=None, embed=None, view=giveaway.to_layout(winners=winners))
            await message.reply(view=winners_layout(giveaway, winners), allowed_mentions=WINNER_MENTIONS)
        except discord.Forbidden:
            # The timer fires with no invoking user — route the permission failure to admins.
            guild_config = await self.bot.db.get_guild_config(giveaway.guild_id)
            if guild_config is not None:
                await guild_config.alert_missing_permission("announce giveaway winners", channel=message.channel)
        except discord.HTTPException as exc:
            log.warning("Could not announce giveaway %s: %s", giveaway.id, exc)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Giveaways(bot))
