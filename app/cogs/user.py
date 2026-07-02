from __future__ import annotations

import functools
import importlib.resources
import io
import json
import zoneinfo
from typing import TYPE_CHECKING, ClassVar, NamedTuple

import dateutil.tz
import discord
from discord import app_commands
from discord.ext import commands
from lxml import etree

from app.core import Accent, LayoutView, make_notice
from app.core.models import Cog, describe, group
from app.utils import fuzzy, get_asset_url, helpers, timetools
from config import Emojis

if TYPE_CHECKING:
    import datetime

    from discord.app_commands import Choice

    from app.core import Bot, Context
    from app.database.base import UserConfig


class TimeZone(NamedTuple):
    label: str
    key: str

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> TimeZone | TimeZone:
        cog: UserSettings | None = ctx.bot.get_cog("User Settings")  # type: ignore
        if cog is None:
            # should never happen though?
            raise commands.BadArgument("The user settings cog is not loaded.")

        if argument in cog.timezone_aliases:
            return cls(key=argument, label=cog.timezone_aliases[argument])

        if argument in cog.valid_timezones:
            return cls(key=argument, label=argument)

        timezones = cog.find_timezones(argument)

        try:
            return await ctx.disambiguate(timezones, lambda t: t[0], ephemeral=True)
        except ValueError:
            raise commands.BadArgument(f"Could not find timezone for {argument!r}")

    def to_choice(self) -> Choice[str | int | float]:
        return app_commands.Choice(name=self.label, value=self.key)


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: str | None


# ISO 3166-1 alpha-2 codes of countries that conventionally write the time of day
# on a 12-hour clock (AM/PM). Everywhere else Percy renders a 24-hour clock. This is
# a pragmatic convention map for display, not an exhaustive locale database.
_TWELVE_HOUR_COUNTRIES: frozenset[str] = frozenset(
    {
        "US",  # United States
        "CA",  # Canada
        "AU",  # Australia
        "NZ",  # New Zealand
        "PH",  # Philippines
        "IN",  # India
        "PK",  # Pakistan
        "BD",  # Bangladesh
        "EG",  # Egypt
        "MY",  # Malaysia
        "MX",  # Mexico
        "CO",  # Colombia
        "SV",  # El Salvador
        "HN",  # Honduras
        "NI",  # Nicaragua
        "CR",  # Costa Rica
        "GT",  # Guatemala
        "DO",  # Dominican Republic
        "SA",  # Saudi Arabia
        "JO",  # Jordan
        "IE",  # Ireland
        "GB",  # United Kingdom
    }
)


@functools.lru_cache(maxsize=1)
def _timezone_country_map() -> dict[str, str]:
    """Map each IANA timezone to its primary ISO 3166 country code.

    Parsed once from the ``zone1970.tab`` table bundled with the ``tzdata`` package
    (the first code in each row is the zone's primary country). Returns an empty map
    if the table can't be read, in which case callers fall back to a 24-hour clock.
    """
    mapping: dict[str, str] = {}
    try:
        table = importlib.resources.files("tzdata").joinpath("zoneinfo", "zone1970.tab").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return mapping

    for line in table.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        codes, zone = parts[0], parts[2]
        mapping[zone] = codes.split(",")[0]
    return mapping


def _uses_12_hour(tz_name: str | None) -> bool:
    """Whether ``tz_name``'s country conventionally uses a 12-hour (AM/PM) clock."""
    if not tz_name:
        return False
    return _timezone_country_map().get(tz_name) in _TWELVE_HOUR_COUNTRIES


def _format_clock(dt: datetime.datetime, tz_name: str | None) -> str:
    """Format ``dt`` using the 12- or 24-hour convention usual for ``tz_name``."""
    if _uses_12_hour(tz_name):
        return dt.strftime("%Y-%m-%d %I:%M %p")
    return dt.strftime("%Y-%m-%d %H:%M")


def _timezone_state(config: UserConfig) -> str:
    """Render the body line for the timezone setting section."""
    if config.timezone:
        time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
        offset = timetools.get_timezone_offset(time, with_name=True)
        clock = _format_clock(time, config.timezone)
        return f"-# **{config.timezone}** · `{clock} {offset}`"
    return "-# Not set — used to localise reminders and other times."


def _toggle_state(enabled: bool) -> str:
    """Render the body line for a boolean tracking setting section."""
    if enabled:
        return f"-# {Emojis.success} Currently **on** — Percy stores this for you."
    return f"-# {Emojis.error} Currently **off** — nothing new is stored."


class TimezoneModal(discord.ui.Modal, title="Set Your Timezone"):
    """Collects a timezone string (or blank to clear) from the settings card."""

    tz_input = discord.ui.TextInput(
        label="Timezone",
        placeholder="e.g. Europe/Berlin, Eastern Time — leave empty to clear",
        required=False,
        max_length=100,
    )

    def __init__(self, view: SettingsView) -> None:
        super().__init__(timeout=120)
        self._view = view
        if view.config.timezone:
            self.tz_input.default = view.config.timezone

    async def on_submit(self, interaction: discord.Interaction[Bot]) -> None:
        value = self.tz_input.value.strip()

        if not value:
            await interaction.client.db.users.clear_timezone(self._view.user.id)
            await self._view.refresh(interaction, status=f"{Emojis.success} Timezone cleared.")
            return

        resolved = self._view.cog.resolve_timezone(value)
        if resolved is None:
            await interaction.response.send_message(
                f"{Emojis.error} Could not find a timezone matching {value!r}.", ephemeral=True
            )
            return

        await interaction.client.db.users.set_timezone(self._view.user.id, resolved.key)
        await self._view.refresh(interaction, status=f"{Emojis.success} Timezone set to **{resolved.label}**.")


class DeleteDataModal(discord.ui.Modal, title="Delete Personal Data"):
    """Type-to-confirm gate before erasing a user's stored history."""

    confirm = discord.ui.TextInput(
        label='Type "DELETE" to confirm',
        placeholder="DELETE",
        required=True,
        max_length=10,
    )

    def __init__(self, view: SettingsView) -> None:
        super().__init__(timeout=120)
        self._view = view

    async def on_submit(self, interaction: discord.Interaction[Bot]) -> None:
        if self.confirm.value.strip().upper() != "DELETE":
            await interaction.response.send_message(
                f"{Emojis.error} Confirmation text didn't match — nothing was deleted.", ephemeral=True
            )
            return

        await interaction.client.db.users.delete_personal_data(self._view.user.id)
        await interaction.response.send_message(
            f"{Emojis.success} Your stored presence, name/nickname and avatar history has been removed.",
            ephemeral=True,
        )


class SettingsView(LayoutView):
    """Components V2 overview of a user's personal settings.

    Each setting is rendered as a :class:`discord.ui.Section`: the title and current
    state sit on the left, with the button that manages it as the section accessory.
    """

    def __init__(self, ctx: Context, *, cog: UserSettings, config: UserConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.cog = cog
        self.user = ctx.author
        self.config = config
        self._status: str | None = None

        self.timezone_btn: discord.ui.Button = discord.ui.Button(style=discord.ButtonStyle.blurple)
        self.timezone_btn.callback = self._on_timezone  # type: ignore[assignment]

        self.presence_btn: discord.ui.Button = discord.ui.Button()
        self.presence_btn.callback = self._on_toggle_presence  # type: ignore[assignment]

        self.history_btn: discord.ui.Button = discord.ui.Button()
        self.history_btn.callback = self._on_toggle_history  # type: ignore[assignment]

        self.export_btn: discord.ui.Button = discord.ui.Button(
            label="Export", style=discord.ButtonStyle.secondary, emoji="📤"
        )
        self.export_btn.callback = self._on_export  # type: ignore[assignment]

        self.delete_btn: discord.ui.Button = discord.ui.Button(
            label="Delete", style=discord.ButtonStyle.danger, emoji=Emojis.trash
        )
        self.delete_btn.callback = self._on_delete  # type: ignore[assignment]

        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()

        self.timezone_btn.label = "Change" if self.config.timezone else "Set"
        self.presence_btn.label = "Enabled" if self.config.track_presence else "Disabled"
        self.presence_btn.style = (
            discord.ButtonStyle.success if self.config.track_presence else discord.ButtonStyle.danger
        )
        self.history_btn.label = "Enabled" if self.config.track_history else "Disabled"
        self.history_btn.style = (
            discord.ButtonStyle.success if self.config.track_history else discord.ButtonStyle.danger
        )

        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(
            discord.ui.Section(
                "## User Settings\n-# Manage your personal preferences and the data Percy keeps about you.",
                accessory=discord.ui.Thumbnail(get_asset_url(self.user)),
            )
        )
        container.add_item(discord.ui.Separator())

        container.add_item(
            discord.ui.Section(f"### Timezone\n{_timezone_state(self.config)}", accessory=self.timezone_btn)
        )
        container.add_item(
            discord.ui.Section(
                f"### Presence Tracking\n{_toggle_state(self.config.track_presence)}",
                accessory=self.presence_btn,
            )
        )
        container.add_item(
            discord.ui.Section(
                f"### Name & Avatar History\n{_toggle_state(self.config.track_history)}",
                accessory=self.history_btn,
            )
        )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                "### Export My Data\n-# Get a copy of everything Percy has stored about you, sent to your DMs.",
                accessory=self.export_btn,
            )
        )
        container.add_item(
            discord.ui.Section(
                "### Delete My Data\n-# Permanently erase your stored presence, name/nickname and avatar history.",
                accessory=self.delete_btn,
            )
        )

        container.add_item(discord.ui.Separator())
        footer = self._status or "Tracking is on by default — toggle it off any time."
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))

        self.add_item(container)

    async def refresh(self, interaction: discord.Interaction[Bot], *, status: str | None = None) -> None:
        """Re-read the config, rebuild the card and edit the message in place."""
        self.config = await interaction.client.db.get_user_config(self.user.id)
        self._status = status
        self._rebuild()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def _on_timezone(self, interaction: discord.Interaction[Bot]) -> None:
        await interaction.response.send_modal(TimezoneModal(self))

    async def _on_toggle_presence(self, interaction: discord.Interaction[Bot]) -> None:
        self.config = await self.config.update(track_presence=not self.config.track_presence)
        state = "enabled" if self.config.track_presence else "disabled"
        await self.refresh(interaction, status=f"{Emojis.success} Presence tracking {state}.")

    async def _on_toggle_history(self, interaction: discord.Interaction[Bot]) -> None:
        self.config = await self.config.update(track_history=not self.config.track_history)
        state = "enabled" if self.config.track_history else "disabled"
        await self.refresh(interaction, status=f"{Emojis.success} Name/avatar history tracking {state}.")

    async def _on_export(self, interaction: discord.Interaction[Bot]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        data = await interaction.client.db.users.export_all_user_data(self.user.id)
        payload = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        file = discord.File(io.BytesIO(payload.encode("utf-8")), filename=f"percy-data-{self.user.id}.json")

        try:
            await self.user.send(
                "Here is a copy of all the personal data Percy has stored about you.", file=file
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{Emojis.error} I couldn't DM you — enable direct messages from server members and try again.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"{Emojis.success} I've sent a copy of your stored data to your DMs.", ephemeral=True
        )

    async def _on_delete(self, interaction: discord.Interaction[Bot]) -> None:
        await interaction.response.send_modal(DeleteDataModal(self))


class UserSettings(Cog, name="User Settings"):
    """Handling user-based settings for the bot."""

    emoji = "<:gear:1322354639248691291>"

    DEFAULT_POPULAR_TIMEZONE_IDS: ClassVar[list[str]] = [
        # America
        "usnyc",  # America/New_York
        "uslax",  # America/Los_Angeles
        "uschi",  # America/Chicago
        "usden",  # America/Denver
        # India
        "inccu",  # Asia/Kolkata
        # Europe
        "trist",  # Europe/Istanbul
        "rumow",  # Europe/Moscow
        "gblon",  # Europe/London
        "frpar",  # Europe/Paris
        "esmad",  # Europe/Madrid
        "deber",  # Europe/Berlin
        "grath",  # Europe/Athens
        "uaiev",  # Europe/Kyev
        "itrom",  # Europe/Rome
        "nlams",  # Europe/Amsterdam
        "plwaw",  # Europe/Warsaw
        # Canada
        "cator",  # America/Toronto
        # Australia
        "aubne",  # Australia/Brisbane
        "ausyd",  # Australia/Sydney
        # Brazil
        "brsao",  # America/Sao_Paulo
        # Japan
        "jptyo",  # Asia/Tokyo
        # China
        "cnsha",  # Asia/Shanghai
    ]

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.valid_timezones: set[str] = set(zoneinfo.available_timezones())
        self.timezone_aliases: dict[str, str] = {
            "Eastern Time": "America/New_York",
            "Central Time": "America/Chicago",
            "Mountain Time": "America/Denver",
            "Pacific Time": "America/Los_Angeles",
            # (Unfortunately) special case American timezone abbreviations
            "EST": "America/New_York",
            "CST": "America/Chicago",
            "MST": "America/Denver",
            "PST": "America/Los_Angeles",
            "EDT": "America/New_York",
            "CDT": "America/Chicago",
            "MDT": "America/Denver",
            "PDT": "America/Los_Angeles",
        }
        self.default_timezones: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        await self.parse_bcp47_timezones()

    async def parse_bcp47_timezones(self) -> None:
        async with self.bot.session.get(
            "https://raw.githubusercontent.com/unicode-org/cldr/main/common/bcp47/timezone.xml"
        ) as resp:
            if resp.status != 200:
                return

            parser = etree.XMLParser(ns_clean=True, recover=True, encoding="utf-8")
            tree = etree.fromstring(await resp.read(), parser=parser)  # type: ignore

            entries: dict[str, CLDRDataEntry] = {
                node.attrib["name"]: CLDRDataEntry(
                    description=node.attrib["description"],
                    aliases=node.get("alias", "Etc/Unknown").split(" "),
                    deprecated=node.get("deprecated", "false") == "true",
                    preferred=node.get("preferred"),
                )
                for node in tree.iter("type")
                if (
                    not node.attrib["name"].startswith(("utcw", "utce", "unk"))
                    and not node.attrib["description"].startswith("POSIX")
                )
            }

            for entry in entries.values():
                if entry.preferred is not None:
                    preferred = entries.get(entry.preferred)
                    if preferred is not None:
                        self.timezone_aliases[entry.description] = preferred.aliases[0]
                else:
                    self.timezone_aliases[entry.description] = entry.aliases[0]

            for key in self.DEFAULT_POPULAR_TIMEZONE_IDS:
                entry = entries.get(key)
                if entry is not None:
                    self.default_timezones.append(app_commands.Choice(name=entry.description, value=entry.aliases[0]))  # type: ignore

    @group(
        name="settings",
        description="Shows your personal bot settings.",
        fallback="show",
        hybrid=True,
    )
    async def settings(self, ctx: Context) -> None:
        """Shows your settings."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        view = SettingsView(ctx, cog=self, config=config)
        view.message = await ctx.send(view=view)

    def resolve_timezone(self, query: str) -> TimeZone | None:
        """Best-effort, non-interactive timezone resolution for the settings card.

        Mirrors :meth:`TimeZone.convert` but, since a modal can't disambiguate, falls
        back to the first fuzzy match instead of prompting.
        """
        if query in self.timezone_aliases:
            return TimeZone(key=self.timezone_aliases[query], label=query)
        if query in self.valid_timezones:
            return TimeZone(key=query, label=query)
        matches = self.find_timezones(query)
        return matches[0] if matches else None

    @settings.group(
        name="timezone",
        fallback="show",
        alias="tz",
        description="Commands related to managing or retrieving timezone info.",
        hybrid=True,
    )
    @describe(user="The user to manage the timezone of.")
    async def timezone(self, ctx: Context, *, user: discord.User = commands.Author) -> None:
        """Shows/Manages the timezone of a user."""
        config = await self.bot.db.get_user_config(user.id)
        if config is None or (config and config.timezone is None):
            await ctx.send_error(f"{user} has not set their timezone.")
            return

        time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
        offset = timetools.get_timezone_offset(time, with_name=True)
        clock = _format_clock(time, config.timezone)

        is_self = user.id == ctx.author.id
        card = make_notice(
            title="Your Timezone" if is_self else f"{user.display_name}'s Timezone",
            description=(
                f"Your timezone is set to **{config.timezone}**." if is_self
                else f"The current time for {user.mention} is shown below."
            ),
            accent=Accent.success,
            thumbnail=get_asset_url(user),
            fields=[("Current Time", f"`{clock} {offset}`")],
        )
        await ctx.send(view=card)

    @timezone.command(name="info", description="Retrieves info about a timezone.")
    @describe(tz="The timezone to get info about.")
    async def timezone_info(self, ctx: Context, *, tz: TimeZone) -> None:
        """Retrieves info about a timezone."""
        dt = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz.key))

        card = make_notice(
            title=f"Timezone: {tz.key}",
            accent=Accent.info,
            fields=[
                ("Current Time", f"`{_format_clock(dt, tz.key)}`"),
                ("UTC Offset", timetools.get_timezone_offset(dt)),
                ("Daylight Savings", "Yes" if dt.dst() else "No"),
                ("Abbreviation", dt.tzname() or "—"),
                ("Clock Format", "12-hour (AM/PM)" if _uses_12_hour(tz.key) else "24-hour"),
            ],
        )
        await ctx.send(view=card)

    @timezone.command(
        name="set",
        description="Sets the timezone of a user.",
    )
    @describe(tz="The timezone to change to.")
    async def timezone_set(self, ctx: Context, *, tz: TimeZone) -> None:
        """Sets your timezone.
        This is used to convert times to your local timezone when
        using the reminder command and other miscellaneous commands
        such as tempblock, tempmute, etc.
        """
        await ctx.db.users.set_timezone(ctx.author.id, tz.key)
        await ctx.send_success(
            f"Your timezone has been set to **{tz.label}** (IANA ID: {tz.key}).", ephemeral=True, delete_after=10
        )

    @timezone_set.autocomplete("tz")
    @timezone_info.autocomplete("tz")
    async def timezone_set_autocomplete(self, _, argument: str) -> list[Choice[str | int | float]] | list[Choice[str]]:
        if not argument:
            return self.default_timezones
        matches = self.find_timezones(argument)
        return [tz.to_choice() for tz in matches[:25]]

    @timezone.command(
        name="reset",
        description="Resets your timezone to UTC.",
    )
    async def timezone_reset(self, ctx: Context) -> None:
        """This is useful if you want to stop using timezone-aware features or if you want to reset a misconfigured timezone."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        if config is None or (config and config.timezone is None):
            raise commands.BadArgument("You have not set your timezone.")

        await ctx.db.users.clear_timezone(ctx.author.id)
        await ctx.send_success("Your timezone has been deleted.", ephemeral=True)

    def find_timezones(self, query: str) -> list[TimeZone]:
        if "/" in query:
            return [TimeZone(key=a, label=a) for a in fuzzy.finder(query, self.valid_timezones)]

        keys = fuzzy.finder(query, self.timezone_aliases.keys())
        return [TimeZone(label=k, key=self.timezone_aliases[k]) for k in keys]

    @settings.command(
        name="request-data",
        description="Export a copy of the personal data Percy has stored about you.",
    )
    async def settings_request_data(self, ctx: Context) -> None:
        """Request a copy of your stored personal data (a GDPR-style data export).

        DMs you a JSON file with **all** the data Percy has stored about you: your
        settings, consent-tracked history (presence, name/nickname, avatar), leveling,
        economy, game stats, content you created (tags, notes, reminders, playlists,
        giveaways, poll answers, highlights), linked accounts, vote rewards and the
        moderation cases that reference you.
        """
        await ctx.defer(ephemeral=True)
        data = await self.bot.db.users.export_all_user_data(ctx.author.id)
        payload = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        file = discord.File(io.BytesIO(payload.encode("utf-8")), filename=f"percy-data-{ctx.author.id}.json")

        try:
            await ctx.author.send(
                "Here is a copy of all the personal data Percy has stored about you.",
                file=file,
            )
        except discord.Forbidden:
            await ctx.send_error(
                "I couldn't DM you — enable direct messages from server members and run this again.",
                ephemeral=True,
            )
            return

        await ctx.send_success("I've sent a copy of your stored data to your DMs.", ephemeral=True)

    @settings.command(
        name="remove-personal-data",
        description="Permanently delete all personal data Percy has stored about you.",
    )
    async def settings_request_data_removal(self, ctx: Context) -> None:
        """Permanently delete your stored personal data.

        This removes your stored presence history, avatar history and name/nickname
        history. This action cannot be undone.
        """
        confirm = await ctx.confirm(
            f"{Emojis.warning} This permanently deletes your stored **presence, name/nickname and avatar history**. "
            "This cannot be undone. Continue?",
            ephemeral=True,
            timeout=60.0,
        )
        if not confirm:
            await ctx.send_info("Cancelled — nothing was deleted.", ephemeral=True)
            return

        await self.bot.db.users.delete_personal_data(ctx.author.id)
        await ctx.send_success("Your personal data has been removed from the bot.", ephemeral=True)


async def setup(bot: Bot) -> None:
    await bot.add_cog(UserSettings(bot))
