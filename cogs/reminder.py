from __future__ import annotations

import asyncio
import datetime
import textwrap
import zoneinfo
from typing import TYPE_CHECKING, Any, Optional, Sequence, NamedTuple, Self

import asyncpg
import dateutil.tz
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING
from lxml import etree
from typing_extensions import Annotated

from . import command
from .utils import timetools, formats, cache, fuzzy, helpers
from .utils.context import Context
from .utils.formats import plural, MaybeAcquire
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy


class TimeZone(NamedTuple):
    label: str
    key: str

    # noinspection PyProtectedMember
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        assert isinstance(ctx.cog, Reminder)

        if argument in ctx.cog._timezone_aliases:
            return cls(key=argument, label=ctx.cog._timezone_aliases[argument])

        if argument in ctx.cog.valid_timezones:
            return cls(key=argument, label=argument)

        timezones = ctx.cog.find_timezones(argument)

        try:
            return await ctx.disambiguate(timezones, lambda t: t[0], ephemeral=True)
        except ValueError:
            raise commands.BadArgument(f'<:redTick:1079249771975413910> Could not find timezone for {argument!r}')

    def to_choice(self) -> app_commands.Choice[str]:
        return app_commands.Choice(name=self.label, value=self.key)


class SnoozeModal(discord.ui.Modal, title='Snooze'):
    duration = discord.ui.TextInput(label='Duration', placeholder='10 minutes', default='10 minutes', min_length=2)

    def __init__(self, parent: ReminderView, cog: Reminder, timer: Timer) -> None:
        super().__init__()
        self.parent: ReminderView = parent
        self.timer: Timer = timer
        self.cog: Reminder = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            when = timetools.FutureTime(str(self.duration)).dt
        except Exception:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Duration could not be parsed, sorry. Try something like "5 minutes" or "1 hour"',
                ephemeral=True
            )
            return

        self.parent.snooze.disabled = True
        await interaction.response.edit_message(view=self.parent)

        zone = await self.cog.get_timezone(interaction.user.id)
        await self.cog.create_timer(
            when,
            self.timer.event,
            *self.timer.args,
            **self.timer.kwargs,
            created=interaction.created_at,
            timezone=zone or 'UTC',
        )
        author_id, _, message = self.timer.args
        await interaction.followup.send(
            f"<:greenTick:1079249732364406854> Alright <@{author_id}>, "
            f"I've snoozed your reminder till {discord.utils.format_dt(when, 'R')} for *{message}*",
            ephemeral=True
        )


class SnoozeButton(discord.ui.Button['ReminderView']):
    def __init__(self, cog: Reminder, timer: Timer) -> None:
        super().__init__(label='Snooze', style=discord.ButtonStyle.blurple)
        self.timer: Timer = timer
        self.cog: Reminder = cog

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert self.view is not None
        await interaction.response.send_modal(SnoozeModal(self.view, self.cog, self.timer))


class ReminderView(discord.ui.View):
    message: discord.Message

    def __init__(self, *, url: str, timer: Timer, cog: Reminder, author_id: int) -> None:
        super().__init__(timeout=300)
        self.author_id: int = author_id
        self.snooze = SnoozeButton(cog, timer)
        self.add_item(discord.ui.Button(url=url, label='Jump to Message'))
        self.add_item(self.snooze)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Don\'t snooze other peoples timer?!', ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.snooze.disabled = True
        await self.message.edit(view=self)


class Timer(PostgresItem):
    """A timer that will fire at a given time and send a message to a given channel."""

    id: int
    event: str
    created_at: datetime.datetime
    expires: datetime.datetime
    timezone: str
    extra: dict[str, Any]
    args: Sequence[Any]
    kwargs: dict[str, Any]

    __slots__ = ('args', 'kwargs', 'extra', 'event', 'id', 'created_at', 'expires', 'timezone')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.args: Sequence[Any] = self.extra.get('args', [])
        self.kwargs: dict[str, Any] = self.extra.get('kwargs', {})

    @property
    def human_delta(self) -> str:
        return discord.utils.format_dt(self.created_at, style="R")

    @property
    def author_id(self) -> Optional[int]:
        if self.args:
            return int(self.args[0])
        return None


class CLDRDataEntry(NamedTuple):
    description: str
    aliases: list[str]
    deprecated: bool
    preferred: Optional[str]


class Reminder(commands.Cog):
    """Set reminders for a certain period of time and get notified."""

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
        self._have_data = asyncio.Event()
        self._current_timer: Optional[Timer] = None
        self._task = bot.loop.create_task(self.dispatch_timers())
        self.valid_timezones: set[str] = set(zoneinfo.available_timezones())
        # User-friendly timezone names, some manual and most from the CLDR database.
        self._timezone_aliases: dict[str, str] = {
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
        self._default_timezones: list[app_commands.Choice[str]] = []

    async def cog_load(self) -> None:
        await self.parse_bcp47_timezones()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="sleep", id=1087490868660928683)

    def cog_unload(self) -> None:
        self._task.cancel()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, commands.BadArgument):
            await ctx.send(str(error))
        if isinstance(error, commands.TooManyArguments):
            await ctx.send(
                f'<:redTick:1079249771975413910> You called the {ctx.command.name} command with too many arguments.')

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
                if not node.attrib['name'].startswith(('utcw', 'utce', 'unk'))
                   and not node.attrib['description'].startswith('POSIX')
            }

            for entry in entries.values():
                if entry.preferred is not None:
                    preferred = entries.get(entry.preferred)
                    if preferred is not None:
                        self._timezone_aliases[entry.description] = preferred.aliases[0]
                else:
                    self._timezone_aliases[entry.description] = entry.aliases[0]

            for key in self.DEFAULT_POPULAR_TIMEZONE_IDS:
                entry = entries.get(key)
                if entry is not None:
                    self._default_timezones.append(app_commands.Choice(name=entry.description, value=entry.aliases[0]))

    @cache.cache()
    async def get_timezone(self, user_id: int, /) -> Optional[str]:
        query = "SELECT timezone from user_settings WHERE id = $1;"
        record = await self.bot.pool.fetchrow(query, user_id)
        return record['timezone'] if record else None

    async def get_tzinfo(self, user_id: int, /) -> datetime.tzinfo:
        tz = await self.get_timezone(user_id)
        if tz is None:
            return datetime.timezone.utc
        return dateutil.tz.gettz(tz) or datetime.timezone.utc

    def find_timezones(self, query: str) -> list[TimeZone]:
        if '/' in query:
            return [TimeZone(key=a, label=a) for a in fuzzy.finder(query, self.valid_timezones)]

        keys = fuzzy.finder(query, self._timezone_aliases.keys())
        return [TimeZone(label=k, key=self._timezone_aliases[k]) for k in keys]

    async def get_active_timer(
            self, *, connection: Optional[asyncpg.Connection] = None, days: int = 7
    ) -> Optional[Timer]:
        query = """
            SELECT * FROM reminders
            WHERE (expires AT TIME ZONE 'UTC' AT TIME ZONE timezone) < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY expires
            LIMIT 1;
        """
        con = connection or self.bot.pool

        record = await con.fetchrow(query, datetime.timedelta(days=days))
        return Timer(record=record) if record else None

    async def wait_for_active_timers(
            self, *, connection: Optional[asyncpg.Connection] = None, days: int = 7
    ) -> Timer:
        async with MaybeAcquire(connection=connection, pool=self.bot.pool) as con:
            timer = await self.get_active_timer(connection=con, days=days)
            if timer is not None:
                self._have_data.set()
                return timer

            self._have_data.clear()
            self._current_timer = None
            await self._have_data.wait()

            return await self.get_active_timer(connection=con, days=days)  # type: ignore

    async def call_timer(self, timer: Timer) -> None:
        query = "DELETE FROM reminders WHERE id=$1;"
        await self.bot.pool.execute(query, timer.id)

        event_name = f'{timer.event}_timer_complete'
        self.bot.dispatch(event_name, timer)

    async def dispatch_timers(self) -> None:
        try:
            while not self.bot.is_closed():
                timer = self._current_timer = await self.wait_for_active_timers(days=40)
                now = datetime.datetime.utcnow()

                if timer.expires >= now:
                    to_sleep = (timer.expires - now).total_seconds()
                    await asyncio.sleep(to_sleep)

                await self.call_timer(timer)
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_timers())

    async def short_timer_optimisation(self, seconds: float, timer: Timer) -> None:
        await asyncio.sleep(seconds)
        event_name = f'{timer.event}_timer_complete'
        self.bot.dispatch(event_name, timer)

    async def get_timer(self, event: str, /, **kwargs: Any) -> Optional[Timer]:
        r"""Gets a timer from the database.
        Note you cannot find a database by its expiry or creation timetools.
        Parameters
        -----------
        event: str
            The name of the event to search for.
        \*\*kwargs
            Keyword arguments to search for in the database.
        Returns
        --------
        Optional[:class:`Timer`]
            The timer if found, otherwise None.
        """

        filtered_clause = [f"extra #>> ARRAY['kwargs', '{key}'] = ${i}" for (i, key) in
                           enumerate(kwargs.keys(), start=2)]
        query = f"SELECT * FROM reminders WHERE event = $1 AND {' AND '.join(filtered_clause)} LIMIT 1"
        record = await self.bot.pool.fetchrow(query, event, list(kwargs.values()))
        return Timer(record=record) if record else None

    async def delete_timer(self, event: str, /, **kwargs: Any) -> None:
        r"""Deletes a timer from the database.
        Note you cannot find a database by its expiry or creation timetools.
        Parameters
        -----------
        event: str
            The name of the event to search for.
        \*\*kwargs
            Keyword arguments to search for in the database.
        """

        filtered_clause = [f"extra #>> ARRAY['kwargs', '{key}'] = ${i}" for (i, key) in
                           enumerate(kwargs.keys(), start=2)]
        query = f"DELETE FROM reminders WHERE event = $1 AND {' AND '.join(filtered_clause)} RETURNING id"
        record: Any = await self.bot.pool.fetchrow(query, event, *kwargs.values())

        if record is not None and self._current_timer and self._current_timer.id == record['id']:
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_timers())

    async def create_timer(self, when: datetime.datetime, event: str, /, *args: Any, **kwargs: Any) -> Timer:
        r"""Creates a timer.
        Parameters
        -----------
        when: datetime.datetime
            When the timer should fire.
        event: str
            The name of the event to trigger.
            Will transform to 'on_{event}_timer_complete'.
        \*args
            Arguments to pass to the event
        \*\*kwargs
            Keyword arguments to pass to the event
        connection: asyncpg.Connection
            Special keyword-only argument to use a specific connection
            for the DB request.
        created: datetime.datetime
            Special keyword-only argument to use as the creation timetools.
            Should make the timedeltas a bit more consistent.
        timezone: str
            Special keyword-only argument to use as the timezone for the
            expiry timetools. This automatically adjusts the expiry time to be
            in the future, should it be in the past.
        Note
        ------
        Arguments and keyword arguments must be JSON serialisable.
        Returns
        --------
        :class:`Timer`
        """
        pool = self.bot.pool

        try:
            now = kwargs.pop('created')
        except KeyError:
            now = discord.utils.utcnow()

        timezone_name = kwargs.pop('timezone', 'UTC')
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        timer = Timer.temporary(event=event, expires=when, created=now, timezone=timezone_name, extra={'args': args, 'kwargs': kwargs})
        delta = (when - now).total_seconds()

        if delta <= 60:  # dont want delta to be negative
            self.bot.loop.create_task(self.short_timer_optimisation(delta, timer))
            return timer

        query = """INSERT INTO reminders (event, extra, expires, created, timezone)
                           VALUES ($1, $2::jsonb, $3, $4, $5)
                           RETURNING id;
                        """

        row = await pool.fetchrow(query, event, {'args': args, 'kwargs': kwargs}, when, now, timezone_name)
        timer.id = row[0]

        if delta <= (86400 * 40):  # 40 days
            self._have_data.set()

        if self._current_timer and when < self._current_timer.expires:
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_timers())

        return timer

    @command(
        commands.hybrid_group,
        name="reminder",
        aliases=['timer', 'remindme', 'remind'],
        description="Reminds you of something after a certain amount of timetools.",
        usage='<when>'
    )
    async def reminder(
            self,
            ctx: Context,
            *,
            when: Annotated[
                timetools.FriendlyTimeResult, timetools.UserFriendlyTime(commands.clean_content, default='…')],  # noqa
    ):
        """Reminds you of something after a certain amount of timetools.
        The input can be any direct date (e.g. YYYY-MM-DD) or a human
        readable offset.

        **Examples:**
        - "next thursday at 3pm do something funny"
        - "do the dishes tomorrow"
        - "in 3 days do the thing"
        - "2d unmute someone"

        Times are in UTC unless a timezone is specified
        using the "timezone set" command.

        """
        zone = await self.get_timezone(ctx.author.id)
        await self.create_timer(
            when.dt,
            'reminder',
            ctx.author.id,
            ctx.channel.id,
            when.arg,
            created=ctx.message.created_at,
            message_id=ctx.message.id,
            timezone=zone or 'UTC',
        )
        await ctx.send(
            f"<:greenTick:1079249732364406854> Okay {ctx.author.mention}, "
            f"I'll remind you *{discord.utils.format_dt(when.dt, 'R')}* for *{when.arg}*"
        )

    @command(
        reminder.app_command.command,
        name='create',
        description='Reminds you of something at a specific time.',
    )
    @app_commands.describe(when='When to be reminded of something.', prompt='What to be reminded of')
    async def reminder_create(
            self,
            interaction: discord.Interaction,
            when: app_commands.Transform[datetime.datetime, timetools.TimeTransformer],
            prompt: str = '…',
    ):
        """Sets a reminder to remind you of something at a specific timetools."""
        zone = await self.get_timezone(interaction.user.id)
        await self.create_timer(
            when,
            'reminder',
            interaction.user.id,
            interaction.channel_id,
            prompt,
            created=interaction.created_at,
            message_id=None,
            timezone=zone or 'UTC',
        )
        await interaction.response.send_message(
            f"<:greenTick:1079249732364406854> Okay {interaction.user.mention}, "
            f"I'll remind you *{discord.utils.format_dt(when, 'R')}* for *{prompt}*"
        )

    @reminder_create.error
    async def reminder_create_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, timetools.BadTimeTransform):
            await interaction.response.send_message(str(error), ephemeral=True)

    @command(
        commands.hybrid_group,
        name='timezone',
        fallback='show',
        description='Commands related to managing or retrieving timezone info.',
    )
    @app_commands.describe(user='The user to manage the timezone of.')
    async def timezone(self, ctx: Context, *, user: discord.User = commands.Author):
        """Shows/Manages the timezone of a user."""
        self_query = user.id == ctx.author.id
        tz = await self.get_timezone(user.id)
        if tz is None:
            return await ctx.send(f'<:redTick:1079249771975413910> {user} has not set their timezone.')

        time = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz))
        offset = timetools.get_timezone_offset(time)
        time = time.strftime('%Y-%m-%d %I:%M %p')
        if self_query:
            await ctx.send(
                f'<:greenTick:1079249732364406854> Your timezone is *{tz!r}*. The current time is `{time} {offset}`.')
        else:
            await ctx.send(f'<:greenTick:1079249732364406854> The current time for {user} is `{time} {offset}`.')

    @timezone.command(name='info')
    @app_commands.describe(tz='The timezone to get info about.')
    async def timezone_info(self, ctx: Context, *, tz: TimeZone):
        """Retrieves info about a timezone."""

        embed = discord.Embed(title=f"ID: {tz.key}", colour=helpers.Colour.darker_red())
        dt = discord.utils.utcnow().astimezone(dateutil.tz.gettz(tz.key))
        time = dt.strftime('%Y-%m-%d %I:%M %p')
        embed.add_field(name='Current Time', value=time, inline=False)

        offset = dt.utcoffset()
        if offset is not None:
            minutes, _ = divmod(int(offset.total_seconds()), 60)
            hours, minutes = divmod(minutes, 60)
            embed.add_field(name='UTC Offset', value=f'{hours:+03d}:{minutes:02d}')

        embed.add_field(name='Daylight Savings', value='Yes' if dt.dst() else 'No')
        embed.add_field(name='Abbreviation', value=dt.tzname())

        await ctx.send(embed=embed)

    @command(
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
        await ctx.db.execute(query, ctx.author.id, tz.key, )

        self.get_timezone.invalidate(self, ctx.author.id)
        await ctx.send(
            f'<:greenTick:1079249732364406854> Your timezone has been set to {tz.label} (IANA ID: {tz.key}).',
            ephemeral=True,
            delete_after=10)

    @timezone_set.autocomplete('tz')
    @timezone_info.autocomplete('tz')
    async def timezone_set_autocomplete(
            self, interaction: discord.Interaction, argument: str
    ) -> list[app_commands.Choice[str]]:
        if not argument:
            return self._default_timezones
        matches = self.find_timezones(argument)
        return [tz.to_choice() for tz in matches[:25]]

    @command(
        timezone.command,
        name='purge',
        description='Clears the timezone of a user.',
    )
    async def timezone_purge(self, ctx: Context):
        """Clears your timezone."""
        tz = await self.get_timezone(ctx.author.id)
        if tz is None:
            return await ctx.send(f'<:redTick:1079249771975413910> You currently have no custom timezone set.')

        await ctx.db.execute("UPDATE user_settings SET timezone = NULL WHERE id=$1", ctx.author.id)
        self.get_timezone.invalidate(self, ctx.author.id)
        await ctx.send('<:greenTick:1079249732364406854> Your timezone has been deleted.', ephemeral=True)

    @command(
        reminder.command,
        name='list',
        description='Shows your latest currently running reminders.',
    )
    async def reminder_list(self, ctx: Context):
        """Shows your currently running reminders."""
        query = """
            SELECT id, expires, extra #>> '{args,2}' FROM reminders
            WHERE event = 'reminder'
            AND extra #>> '{args,0}' = $1
            ORDER BY expires;
        """

        records = await self.bot.pool.fetch(query, str(ctx.author.id))

        if len(records) == 0:
            return await ctx.send('<:redTick:1079249771975413910> No currently running reminders.')

        e = discord.Embed(color=self.bot.colour.darker_red(), title="Your Reminders",
                          description="Here is a list of the last **Reminders** you've set.")
        e.set_author(name=str(ctx.author), icon_url=ctx.author.avatar.url)
        e.set_footer(text=f'Showing {plural(len(records)):Reminder}')

        for index, (reminder_id, expires, message) in enumerate(records, 1):
            shorten = textwrap.shorten(message, width=512)
            value = f'*{shorten!r}* expires {discord.utils.format_dt(expires, style="R")}'
            e.add_field(name=f'#{index} • [{reminder_id}]', value=value, inline=False)

        await ctx.send(embed=e)

    @command(
        reminder.command,
        name='delete',
        description='Deletes a reminder by its ID.',
        aliases=['del', 'remove', 'rm'],
    )
    @app_commands.describe(reminder_id='The ID of the reminder to delete.')
    async def reminder_delete(self, ctx: Context, *, reminder_id: int):
        """Deletes a reminder by its ID.
        To get a reminder ID, use the reminder list command.
        You must own the reminder to delete it, obviously.
        """

        query = """
            DELETE FROM reminders WHERE id=$1
            AND event = 'reminder'
            AND extra #>> '{args,0}' = $2;
        """

        status = await ctx.db.execute(query, reminder_id, str(ctx.author.id))
        if status == 'DELETE 0':
            return await ctx.send('<:redTick:1079249771975413910> Could not delete any reminders with that ID.')

        if self._current_timer and self._current_timer.id == reminder_id:
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_timers())

        await ctx.send('<:greenTick:1079249732364406854> Successfully deleted reminder.', ephemeral=True)

    @command(
        reminder.command,
        name='purge',
        description='Purges all reminders you have set.',
    )
    async def reminder_purge(self, ctx: Context):
        """Purges all reminders you have set."""

        query = """
            SELECT COUNT(*) FROM reminders
            WHERE event = 'reminder'
            AND extra #>> '{args,0}' = $1;
        """

        author_id = str(ctx.author.id)
        total: int = await self.bot.pool.fetchval(query, author_id)
        if total == 0:
            return await ctx.send('<:greenTick:1079249732364406854> You do not have any reminders to delete.')

        confirm = await ctx.prompt(
            f'<:warning:1113421726861238363> Are you sure you want to delete {formats.plural(total):reminder}?')
        if not confirm:
            return

        query = "DELETE FROM reminders WHERE event = 'reminder' AND extra #>> '{args,0}' = $1;"
        await ctx.db.execute(query, author_id)

        if self._current_timer and self._current_timer.author_id == ctx.author.id:
            self._task.cancel()
            self._task = self.bot.loop.create_task(self.dispatch_timers())

        await ctx.send(f'<:greenTick:1079249732364406854> Successfully deleted {formats.plural(total):reminder}.',
                       ephemeral=True)

    @commands.Cog.listener()
    async def on_reminder_timer_complete(self, timer: Timer):
        author_id, channel_id, message = timer.args

        try:
            channel = self.bot.get_channel(channel_id) or (await self.bot.fetch_channel(channel_id))
        except discord.HTTPException:
            return

        guild_id = channel.guild.id if isinstance(channel, (discord.TextChannel, discord.Thread)) else '@me'
        message_id = timer.kwargs.get('message_id')
        msg = f'<@{author_id}>, {timer.human_delta}: {message}'
        view = MISSING

        if message_id:
            url = f'https://discord.com/channels/{guild_id}/{channel.id}/{message_id}'
            view = ReminderView(url=url, timer=timer, cog=self, author_id=author_id)

        try:
            msg = await channel.send(msg, view=view)  # type: ignore
        except discord.HTTPException:
            return
        else:
            if view is not MISSING:
                view.message = msg


async def setup(bot):
    await bot.add_cog(Reminder(bot))
