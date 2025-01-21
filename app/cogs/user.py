from __future__ import annotations

import zoneinfo
from typing import TYPE_CHECKING, ClassVar, Final, NamedTuple, Self

import dateutil.tz
import discord
from discord import app_commands
from discord.ext import commands
from lxml import etree

from app.core.models import Cog, describe, group, command
from app.utils import fuzzy, helpers, timetools

if TYPE_CHECKING:
    from app.core import Bot, Context


class TimeZone(NamedTuple):
    label: str
    key: str

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        cog: UserSettings | None = ctx.bot.get_cog('User Settings')
        if cog is None:
            # should never happen though?
            raise commands.BadArgument('The user settings cog is not loaded.')

        if argument in cog.timezone_aliases:
            return cls(key=argument, label=cog.timezone_aliases[argument])

        if argument in cog.valid_timezones:
            return cls(key=argument, label=argument)

        timezones = cog.find_timezones(argument)

        try:
            return await ctx.disambiguate(timezones, lambda t: t[0], ephemeral=True)
        except ValueError:
            raise commands.BadArgument(f'Could not find timezone for {argument!r}')

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.key)


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: str | None


class UserSettings(Cog, name='User Settings'):
    """Handling user-based settings for the bot."""

    emoji = '<:gear:1322354639248691291>'

    DEFAULT_POPULAR_TIMEZONE_IDS: Final[ClassVar[list[str]]] = [
        # America
        'usnyc',  # America/New_York
        'uslax',  # America/Los_Angeles
        'uschi',  # America/Chicago
        'usden',  # America/Denver
        # India
        'inccu',  # Asia/Kolkata
        # Europe
        'trist',  # Europe/Istanbul
        'rumow',  # Europe/Moscow
        'gblon',  # Europe/London
        'frpar',  # Europe/Paris
        'esmad',  # Europe/Madrid
        'deber',  # Europe/Berlin
        'grath',  # Europe/Athens
        'uaiev',  # Europe/Kyev
        'itrom',  # Europe/Rome
        'nlams',  # Europe/Amsterdam
        'plwaw',  # Europe/Warsaw
        # Canada
        'cator',  # America/Toronto
        # Australia
        'aubne',  # Australia/Brisbane
        'ausyd',  # Australia/Sydney
        # Brazil
        'brsao',  # America/Sao_Paulo
        # Japan
        'jptyo',  # Asia/Tokyo
        # China
        'cnsha',  # Asia/Shanghai
    ]

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self.valid_timezones: set[str] = set(zoneinfo.available_timezones())
        self.timezone_aliases: dict[str, str] = {
            'Eastern Time': 'America/New_York',
            'Central Time': 'America/Chicago',
            'Mountain Time': 'America/Denver',
            'Pacific Time': 'America/Los_Angeles',
            # (Unfortunately) special case American timezone abbreviations
            'EST': 'America/New_York',
            'CST': 'America/Chicago',
            'MST': 'America/Denver',
            'PST': 'America/Los_Angeles',
            'EDT': 'America/New_York',
            'CDT': 'America/Chicago',
            'MDT': 'America/Denver',
            'PDT': 'America/Los_Angeles',
        }
        self.default_timezones: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        await self.parse_bcp47_timezones()

    async def parse_bcp47_timezones(self) -> None:
        async with self.bot.session.get(
                'https://raw.githubusercontent.com/unicode-org/cldr/main/common/bcp47/timezone.xml'
        ) as resp:
            if resp.status != 200:
                return

            parser = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')
            tree = etree.fromstring(await resp.read(), parser=parser)

            entries: dict[str, CLDRDataEntry] = {
                node.attrib['name']: CLDRDataEntry(
                    description=node.attrib['description'],
                    aliases=node.get('alias', 'Etc/Unknown').split(' '),
                    deprecated=node.get('deprecated', 'false') == 'true',
                    preferred=node.get('preferred'),
                )
                for node in tree.iter('type')
                if (
                        not node.attrib['name'].startswith(('utcw', 'utce', 'unk'))
                        and not node.attrib['description'].startswith('POSIX')
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
                    self.default_timezones.append(app_commands.Choice(name=entry.description, value=entry.aliases[0]))

    @group(
        name='settings',
        description='Shows your personal bot settings.',
        fallback='show',
        hybrid=True,
    )
    async def settings(self, ctx: Context) -> None:
        """Shows your settings."""
        config = await self.bot.db.get_user_config(ctx.author.id)

        embed = discord.Embed(title='User Settings', colour=helpers.Colour.white())

        if config.timezone:
            time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
            offset = timetools.get_timezone_offset(time, with_name=True)
            time = time.strftime('%Y-%m-%d %I:%M %p')
            tz_text = f'**{config.timezone}** - `{time} {offset}`'
        else:
            tz_text = 'Not set'

        embed.add_field(name='Time', value=tz_text, inline=False)
        embed.add_field(name='Track Presence', value='Yes' if config.track_presence else 'No')

        await ctx.send(embed=embed)

    @settings.group(
        name='timezone',
        fallback='show',
        alias='tz',
        description='Commands related to managing or retrieving timezone info.',
        hybrid=True,
    )
    @describe(user='The user to manage the timezone of.')
    async def timezone(self, ctx: Context, *, user: discord.User = commands.Author) -> None:
        """Shows/Manages the timezone of a user."""
        config = await self.bot.db.get_user_config(user.id)
        if config is None or (config and config.timezone is None):
            await ctx.send_error(f'{user} has not set their timezone.')
            return

        time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
        offset = timetools.get_timezone_offset(time, with_name=True)
        time = time.strftime('%Y-%m-%d %I:%M %p')

        if user.id == ctx.author.id:
            await ctx.send_success(f'Your timezone is *{config.timezone!r}*. The current time is `{time} {offset}`.')
        else:
            await ctx.send_success(f'The current time for {user} is `{time} {offset}`.')

    @timezone.command(name='info', description='Retrieves info about a timezone.')
    @describe(tz='The timezone to get info about.')
    async def timezone_info(self, ctx: Context, *, tz: TimeZone) -> None:
        """Retrieves info about a timezone."""

        embed = discord.Embed(title=f'ID: {tz.key}', colour=helpers.Colour.white())
        dt = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz.key))
        time = dt.strftime('%Y-%m-%d %I:%M %p')

        embed.add_field(name='Current Time', value=time, inline=False)
        embed.add_field(name='UTC Offset', value=timetools.get_timezone_offset(dt))
        embed.add_field(name='Daylight Savings', value='Yes' if dt.dst() else 'No')
        embed.add_field(name='Abbreviation', value=dt.tzname())

        await ctx.send(embed=embed)

    @timezone.command(
        name='set',
        description='Sets the timezone of a user.',
    )
    @describe(tz='The timezone to change to.')
    async def timezone_set(self, ctx: Context, *, tz: TimeZone) -> None:
        """Sets your timezone.
        This is used to convert times to your local timezone when
        using the reminder command and other miscellaneous commands
        such as tempblock, tempmute, etc.
        """
        query = """
            INSERT INTO user_settings (id, timezone)
            VALUES ($1, $2)
                ON CONFLICT (id) DO UPDATE SET timezone = $2;
        """
        await ctx.db.execute(query, ctx.author.id, tz.key)

        self.bot.db.get_user_config.invalidate(ctx.author.id)
        await ctx.send_success(f'Your timezone has been set to **{tz.label}** (IANA ID: {tz.key}).',
                               ephemeral=True, delete_after=10)

    @timezone_set.autocomplete('tz')
    @timezone_info.autocomplete('tz')
    async def timezone_set_autocomplete(self, _, argument: str) -> list[app_commands.Choice[str]]:
        if not argument:
            return self.default_timezones
        matches = self.find_timezones(argument)
        return [tz.to_choice() for tz in matches[:25]]

    @timezone.command(
        name='purge',
        description='Clears the timezone of a user.',
    )
    async def timezone_purge(self, ctx: Context) -> None:
        """Clears your timezone."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        if config is None or (config and config.timezone is None):
            raise commands.BadArgument('You have not set your timezone.')

        await ctx.db.execute("UPDATE user_settings SET timezone = NULL WHERE id=$1;", ctx.author.id)
        self.bot.db.get_user_config.invalidate(ctx.author.id)
        await ctx.send_success('Your timezone has been deleted.', ephemeral=True)

    def find_timezones(self, query: str) -> list[TimeZone]:
        if '/' in query:
            return [TimeZone(key=a, label=a) for a in fuzzy.finder(query, self.valid_timezones)]

        keys = fuzzy.finder(query, self.timezone_aliases.keys())
        return [TimeZone(label=k, key=self.timezone_aliases[k]) for k in keys]

    @settings.command(
        name='presence',
        description='Toggles tracking of your presence status.',
    )
    @describe(enabled='Whether to enable or disable presence tracking.')
    async def settings_presence(self, ctx: Context, enabled: bool) -> None:
        """Toggles tracking of your presence status."""
        config = await self.bot.db.get_user_config(ctx.author.id)
        await config.update(track_presence=enabled)
        await ctx.send_success(f'Presence tracking has been {"enabled" if enabled else "disabled"}.')

    @settings.command(
        name='remove-personal-data',
        with_app_command=False,
        description='Remove all your personal data thats stored from the bots database.',
    )
    async def settings_request_data_removal(self, ctx: Context) -> None:
        """Request the removal of your data from the bot.

        This includes removal from the following tables:
        - presence history
        - avatar history
        - name history
        """

        async with self.bot.db.acquire(timeout=300.0) as conn, conn.transaction():
            await conn.execute(
                """
                DELETE FROM presence_history WHERE uuid = $1;
                DELETE FROM avatar_history WHERE uuid = $1;
                DELETE FROM item_history WHERE uuid = $1;
                """,
                ctx.author.id
            )

        await ctx.send_success('Your data has been removed from the bot.', ephemeral=True)


async def setup(bot: Bot) -> None:
    await bot.add_cog(UserSettings(bot))
