from __future__ import annotations
import datetime
import zoneinfo
from typing import NamedTuple, Self, Optional, TYPE_CHECKING

import dateutil.tz
import discord
from discord import app_commands
from lxml import etree

from .utils import cache, timetools, fuzzy, helpers, commands, errors
from .utils.context import Context
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy


class TimeZone(NamedTuple):
    label: str
    key: str

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        cog: UserSettings = ctx.cog  # type: ignore

        if argument in cog.timezone_aliases:  
            return cls(key=argument, label=cog.timezone_aliases[argument])

        if argument in cog.valid_timezones:
            return cls(key=argument, label=argument)

        timezones = cog.find_timezones(argument)

        try:
            return await ctx.disambiguate(timezones, lambda t: t[0], ephemeral=True)
        except ValueError:
            raise errors.BadArgument(f'Could not find timezone for {argument!r}')

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.key)


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: Optional[str]


class UserConfig(PostgresItem):
    id: int
    timezone: str

    __slots__ = ('cog', 'id', 'timezone')

    def __init__(self, cog: UserSettings, **kwargs):
        self.cog: UserSettings = cog

        super().__init__(**kwargs)

    @property
    def tzinfo(self) -> datetime.tzinfo:
        if self.timezone is None:
            return datetime.timezone.utc
        return dateutil.tz.gettz(self.timezone) or datetime.timezone.utc


class UserSettings(commands.Cog, name='User Settings'):
    """Handling user-based settings for the bot."""

    DEFAULT_POPULAR_TIMEZONE_IDS = (
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
    )

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self.valid_timezones: set[str] = set(zoneinfo.available_timezones())
        # User-friendly timezone names, some manual and most from the CLDR database.
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

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='gear', id=1116735104996364368)

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

    @commands.command(
        commands.hybrid_group,
        name='timezone',
        fallback='show',
        description='Commands related to managing or retrieving timezone info.',
    )
    @app_commands.describe(user='The user to manage the timezone of.')
    async def timezone(self, ctx: Context, *, user: discord.User = commands.Author):
        """Shows/Manages the timezone of a user."""
        self_query = user.id == ctx.author.id
        config = await self.get_user_config(user.id)
        if config is None or (config and config.timezone is None):
            return await ctx.stick(False, f'{user} has not set their timezone.')

        time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(config.timezone))
        offset = timetools.get_timezone_offset(time, with_name=True)
        time = time.strftime('%Y-%m-%d %I:%M %p')
        if self_query:
            await ctx.stick(True, f'Your timezone is *{config.timezone!r}*. The current time is `{time} {offset}`.')
        else:
            await ctx.stick(True, f'The current time for {user} is `{time} {offset}`.')

    @commands.command(
        timezone.command,
        name='info'
    )
    @app_commands.describe(tz='The timezone to get info about.')
    async def timezone_info(self, ctx: Context, *, tz: TimeZone):
        """Retrieves info about a timezone."""

        embed = discord.Embed(title=f'ID: {tz.key}', colour=helpers.Colour.darker_red())
        dt = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz.key))
        time = dt.strftime('%Y-%m-%d %I:%M %p')

        embed.add_field(name='Current Time', value=time, inline=False)
        embed.add_field(name='UTC Offset', value=timetools.get_timezone_offset(dt))
        embed.add_field(name='Daylight Savings', value='Yes' if dt.dst() else 'No')
        embed.add_field(name='Abbreviation', value=dt.tzname())

        await ctx.send(embed=embed)

    @commands.command(
        timezone.command,
        name='set',
        description='Sets the timezone of a user.',
    )
    @app_commands.describe(tz='The timezone to change to.')
    async def timezone_set(self, ctx: Context, *, tz: TimeZone):
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

        self.get_user_config.invalidate(self, ctx.author.id)
        await ctx.stick(True, f'Your timezone has been set to **{tz.label}** (IANA ID: {tz.key}).',
                        ephemeral=True, delete_after=10)

    @timezone_set.autocomplete('tz')
    @timezone_info.autocomplete('tz')
    async def timezone_set_autocomplete(
            self, interaction: discord.Interaction, argument: str  # noqa
    ) -> list[app_commands.Choice[str]]:
        if not argument:
            return self.default_timezones
        matches = self.find_timezones(argument)
        return [tz.to_choice() for tz in matches[:25]]

    @commands.command(
        timezone.command,
        name='purge',
        description='Clears the timezone of a user.',
    )
    async def timezone_purge(self, ctx: Context):
        """Clears your timezone."""
        config = await self.get_user_config(ctx.author.id)
        if config is None or (config and config.timezone is None):
            raise errors.CommandError('You have not set your timezone.')

        await ctx.db.execute("UPDATE user_settings SET timezone = NULL WHERE id=$1;", ctx.author.id)
        self.get_user_config.invalidate(self, ctx.author.id)
        await ctx.stick(True, 'Your timezone has been deleted.', ephemeral=True)

    @cache.cache()
    async def get_user_config(self, user_id: int, /) -> Optional[UserConfig]:
        """|coro| @cached

        Retrieves the user config for a user.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to retrieve the config for.

        Returns
        -------
        Optional[:class:`UserConfig`]
            The user config for the user, if it exists.
        """
        query = "SELECT * from user_settings WHERE id = $1;"
        record = await self.bot.pool.fetchrow(query, user_id)
        return UserConfig(self, record=record) if record else None

    def find_timezones(self, query: str) -> list[TimeZone]:
        if '/' in query:
            return [TimeZone(key=a, label=a) for a in fuzzy.finder(query, self.valid_timezones)]

        keys = fuzzy.finder(query, self.timezone_aliases.keys())
        return [TimeZone(label=k, key=self.timezone_aliases[k]) for k in keys]


async def setup(bot: Percy):
    await bot.add_cog(UserSettings(bot))
