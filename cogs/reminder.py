from __future__ import annotations

import asyncio
import datetime
import textwrap
from typing import TYPE_CHECKING, Any, Optional, Sequence, Callable, Union

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING
from typing_extensions import Annotated

from . import command
from .utils import timetools, formats
from .utils.context import Context
from .utils.formats import plural, MaybeAcquire
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy


class SnoozeModal(discord.ui.Modal, title='Snooze'):
    duration = discord.ui.TextInput(label='Duration', placeholder='e.g. 10 minutes (Must be a future time.)',
                                    default='10 minutes', min_length=2)

    def __init__(self, parent: ReminderView, cog: Reminder, timer: Timer) -> None:
        super().__init__()
        self.parent: ReminderView = parent
        self.timer: Timer = timer
        self.cog: Reminder = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            when = timetools.FutureTime(str(self.duration)).dt
        except Exception:  # noqa
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Duration could not be parsed, sorry. Try something like "5 minutes" or "1 hour"',
                ephemeral=True
            )
            return

        self.parent.snooze.disabled = True
        await interaction.response.edit_message(view=self.parent)

        config = await self.cog.bot.user_settings.get_user_config(interaction.user.id)
        zone = config.timezone if config else None
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
    created: datetime.datetime
    expires: datetime.datetime
    timezone: str
    extra: dict[str, Any]

    __slots__ = ('args', 'kwargs', 'extra', 'event', 'id', 'created', 'expires', 'timezone')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.args: Sequence[Any] = self.extra.get('args', [])
        self.kwargs: dict[str, Any] = self.extra.get('kwargs', {})

    @property
    def human_delta(self) -> str:
        return discord.utils.format_dt(self.created, style="R")

    @property
    def author_id(self) -> Optional[int]:
        if self.args:
            return int(self.args[0])
        return None


class Reminder(commands.Cog):
    """Set reminders for a certain period of time and get notified."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self._have_data = asyncio.Event()
        self._current_timer: Optional[Timer] = None
        self._task = bot.loop.create_task(self.dispatch_timers())

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
            self.MaybeSkipTask(True)

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
        query = f"DELETE FROM reminders WHERE event = $1 AND {' AND '.join(filtered_clause)} RETURNING id;"
        record: Any = await self.bot.pool.fetchrow(query, event, *kwargs.values())

        self.MaybeSkipTask(record is not None and self._current_timer and self._current_timer.id == record['id'])

    def MaybeSkipTask(self, key: Union[Callable, bool]) -> bool:
        if not key:
            return False

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

        try:
            now = kwargs.pop('created')
        except KeyError:
            now = discord.utils.utcnow()

        timezone_name = kwargs.pop('timezone', 'UTC')
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        timer = Timer.temporary(
            event=event,
            expires=when,
            created=now,
            timezone=timezone_name,
            extra={'args': args, 'kwargs': kwargs}
        )
        delta = (when - now).total_seconds()

        if delta <= 60:  # dont want delta to be negative
            self.bot.loop.create_task(self.short_timer_optimisation(delta, timer))
            return timer

        query = """
            INSERT INTO reminders (event, extra, expires, created, timezone)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            RETURNING id;
        """

        row = await self.bot.pool.fetchrow(query, event, {'args': args, 'kwargs': kwargs}, when, now, timezone_name)
        timer.id = row[0]

        if delta <= (86400 * 40):  # 40 days
            self._have_data.set()

        self.MaybeSkipTask(self._current_timer and when < self._current_timer.expires)

        return timer

    @command(
        commands.hybrid_group,
        name="reminder",
        aliases=['timer', 'remindme', 'remind'],
        description="Reminds you of something after a certain amount of timetools.",
        examples=["next thursday at 3pm do something funny",
                  "do the dishes tomorrow",
                  "in 3 days do the thing",
                  "2d unmute someone"],
        usage='<when...>'
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

        Times are in UTC unless a timezone is specified
        using the "timezone set" command.
        """
        config = await self.bot.user_settings.get_user_config(ctx.author.id)
        zone = config.timezone if config else None
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
        config = await self.bot.user_settings.get_user_config(interaction.user.id)
        zone = config.timezone if config else None
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

        self.MaybeSkipTask(self._current_timer and self._current_timer.id == reminder_id)

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

        self.MaybeSkipTask(self._current_timer and self._current_timer.author_id == ctx.author.id)

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
