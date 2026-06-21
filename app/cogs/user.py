from __future__ import annotations

import io
import json
import zoneinfo
from typing import TYPE_CHECKING, ClassVar, Final, NamedTuple

import dateutil.tz
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from lxml import etree

from app.core.models import Cog, describe, group
from app.utils import fuzzy, helpers, timetools
from config import Emojis

if TYPE_CHECKING:
    from app.core import Bot, Context


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

        embed = discord.Embed(title="User Settings", colour=helpers.Colour.white())

        if config.timezone:
            time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
            offset = timetools.get_timezone_offset(time, with_name=True)
            time = time.strftime("%Y-%m-%d %I:%M %p")
            tz_text = f"**{config.timezone}** - `{time} {offset}`"
        else:
            tz_text = "Not set"

        embed.add_field(name="Time", value=tz_text, inline=False)
        embed.add_field(name="Track Presence", value="Yes" if config.track_presence else "No")
        embed.add_field(name="Track Name/Avatar History", value="Yes" if config.track_history else "No")
        embed.add_field(
            name="Your data",
            value=(
                f"Tracking is on by default. Turn it **all** off with `{ctx.clean_prefix}settings tracking false`, "
                f"export a copy with `{ctx.clean_prefix}settings request-data`, "
                f"or delete it with `{ctx.clean_prefix}settings remove-personal-data`."
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

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
        time = time.strftime("%Y-%m-%d %I:%M %p")

        if user.id == ctx.author.id:
            await ctx.send_success(f"Your timezone is *{config.timezone!r}*. The current time is `{time} {offset}`.")
        else:
            await ctx.send_success(f"The current time for {user} is `{time} {offset}`.")

    @timezone.command(name="info", description="Retrieves info about a timezone.")
    @describe(tz="The timezone to get info about.")
    async def timezone_info(self, ctx: Context, *, tz: TimeZone) -> None:
        """Retrieves info about a timezone."""

        embed = discord.Embed(title=f"ID: {tz.key}", colour=helpers.Colour.white())
        dt = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz.key))
        time = dt.strftime("%Y-%m-%d %I:%M %p")

        embed.add_field(name="Current Time", value=time, inline=False)
        embed.add_field(name="UTC Offset", value=timetools.get_timezone_offset(dt))
        embed.add_field(name="Daylight Savings", value="Yes" if dt.dst() else "No")
        embed.add_field(name="Abbreviation", value=dt.tzname())

        await ctx.send(embed=embed)

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
        name="purge",
        description="Clears the timezone of a user.",
    )
    async def timezone_purge(self, ctx: Context) -> None:
        """Clears your timezone."""
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
        name="tracking",
        description="Turn ALL of Percy's data tracking about you on or off in one go.",
    )
    @describe(enabled="True keeps tracking on (the default); False disables presence and name/avatar history.")
    async def settings_tracking(self, ctx: Context, enabled: bool) -> None:
        """Master switch for every kind of data tracking Percy keeps about you.

        Turning this off disables both presence tracking and name/avatar history. It
        does not delete data already stored — use `settings remove-personal-data` for that.
        """
        config = await self.bot.db.get_user_config(ctx.author.id)
        await config.update(track_presence=enabled, track_history=enabled)
        if enabled:
            await ctx.send_success("All data tracking has been **enabled**.")
        else:
            await ctx.send_success(
                "All data tracking has been **disabled**. Nothing new will be stored. "
                f"To erase what's already saved, use `{ctx.clean_prefix}settings remove-personal-data`."
            )

    @settings.command(
        name="presence",
        description="Toggles tracking of your presence status.",
    )
    @describe(enabled="Whether to enable or disable presence tracking.")
    async def settings_presence(self, ctx: Context, enabled: bool) -> None:
        """Toggles tracking of your presence status."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        await config.update(track_presence=enabled)
        await ctx.send_success(f"Presence tracking has been {'enabled' if enabled else 'disabled'}.")

    @settings.command(
        name="history",
        description="Toggles tracking of your username, nickname and avatar history.",
    )
    @describe(enabled="Whether to enable or disable name/nickname/avatar history tracking.")
    async def settings_history(self, ctx: Context, enabled: bool) -> None:
        """Toggles tracking of your username, nickname and avatar history."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        await config.update(track_history=enabled)
        await ctx.send_success(f"Name/avatar history tracking has been {'enabled' if enabled else 'disabled'}.")

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
