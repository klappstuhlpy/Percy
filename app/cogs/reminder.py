from __future__ import annotations

import datetime
import textwrap
from typing import Annotated, Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from app.core import Context, Flags, flag, store_true, View
from app.core.models import Cog, describe, group
from app.core.timer import Timer
from app.cogs.user import TimeZone
from app.utils import checks, formats, get_asset_url, helpers, pluralize, positive_reply, timetools
from config import Emojis


class SnoozeModal(discord.ui.Modal, title='Snooze'):
    duration = discord.ui.TextInput(
        label='Duration', placeholder='e.g. 10 minutes (Must be a future time.)',
        default='10 minutes', min_length=2
    )

    def __init__(self, view: SnoozeTimerView, cog: Reminder, timer: ReminderTimer) -> None:
        super().__init__()
        self.view: SnoozeTimerView = view
        self.timer: ReminderTimer = timer
        self.cog: Reminder = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            when = timetools.FutureTime(str(self.duration)).dt
        except commands.BadArgument:
            raise app_commands.AppCommandError('Duration could not be parsed, sorry. Try something like "5 minutes" or "1 hour"')

        self.view.snooze.disabled = True
        await interaction.response.edit_message(view=self.view)

        zone = await self.cog.bot.db.get_user_timezone(interaction.user.id)
        await self.timer.rerun(
            when,
            created=interaction.created_at,
            timezone=zone or 'UTC',
        )
        author_id, _, message = self.timer.args
        await interaction.followup.send(
            f'{Emojis.success} Alright <@{author_id}>, '
            f'I\'ve snoozed your reminder till {discord.utils.format_dt(when, 'R')} for *{message}*',
            ephemeral=True
        )


class SnoozeButton(discord.ui.Button['SnoozeTimerView']):
    def __init__(self, cog: Reminder, timer: ReminderTimer) -> None:
        super().__init__(label='Snooze', style=discord.ButtonStyle.blurple)
        self.timer: ReminderTimer = timer
        self.cog: Reminder = cog

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert self.view is not None
        await interaction.response.send_modal(SnoozeModal(self.view, self.cog, self.timer))


class SnoozeTimerView(View):
    """A view that is used to snooze a reminder."""
    message: discord.Message

    def __init__(self, cog: Reminder, *, url: str, timer: ReminderTimer, author_id: int) -> None:
        super().__init__(timeout=500, members=discord.Object(author_id))
        self.snooze = SnoozeButton(cog, timer)
        self.add_item(discord.ui.Button(url=url, label='Jump to Message'))
        self.add_item(self.snooze)

    async def on_timeout(self) -> None:
        self.snooze.disabled = True
        await self.message.edit(view=self)


class ReminderTimer(Timer):
    """A timer that will fire at a given time and send a message to a given channel."""

    @property
    def author_id(self) -> int | None:
        if self.args:
            return int(self.args[0])
        return None

    @property
    def channel_id(self) -> int | None:
        if self.args:
            return int(self.args[1])
        return None

    @property
    def text(self) -> str | None:
        if self.args:
            return self.args[2]
        return None


class ReminderFlags(Flags):
    timezone: TimeZone = flag(alias='tz', short='t', description='The timezone to use for the reminder.')
    dm: bool = store_true(alias='pm', short='d', description='Send the reminder as a direct message.')


class Reminder(Cog):
    """Set reminders for a certain period of time and get notified."""

    emoji = '<a:clock:1322338395799945247>'

    @group(
        'reminder',
        aliases=['timer', 'remindme', 'remind'],
        fallback='create',
        description='Reminds you of something after a certain amount of time.',
        examples=[
            'next thursday at 3pm do something funny',
            'do the dishes tomorrow',
            'in 3 days do the thing',
            '2d unmute someone'],
        hybrid=True
    )
    @checks.requires_timer()
    @describe(
        prompt='The time to remind you. Can be a direct date or a human-readable offset. (Context is also possible -> See examples)')
    async def reminder(
            self,
            ctx: Context,
            *,
            prompt: Annotated[
                timetools.FriendlyTimeResult,
                timetools.UserFriendlyTime(commands.clean_content, default='…')],  # type: ignore
            flags: ReminderFlags
    ) -> None:
        """Reminds you of something after a certain amount of timetools.
        The input can be any direct date (e.g. YYYY-MM-DD) or a human-readable offset.

        Times are in UTC unless a timezone is specified using the "timezone set" command.
        """
        if len(prompt.arg) > 1500:
            raise commands.BadArgument('The reminder message is too long.')

        # Check if time is too close to the current time
        if prompt.dt < discord.utils.utcnow() + datetime.timedelta(seconds=15):
            raise commands.BadArgument('This time is too close to the current time. Try a time at least 15 seconds in the future.')

        to_remind = prompt.arg
        if ctx.replied_message is not None and ctx.replied_message.content:
            to_remind = ctx.replied_message.content

        zone = flags.timezone or await self.bot.db.get_user_timezone(ctx.author.id)

        channel = ctx.channel
        if flags.dm:
            if not ctx.author.dm_channel:
                await ctx.author.create_dm()
            channel = ctx.author.dm_channel

        await self.bot.timers.create(
            prompt.dt,
            'reminder',
            ctx.author.id,
            channel.id,
            to_remind,
            created=ctx.message.created_at,
            message_id=ctx.message.id,
            timezone=zone or 'UTC',
        )
        await ctx.send_success(
            f'{positive_reply()} {ctx.author.mention}, I\'ll remind you {discord.utils.format_dt(prompt.dt, 'R')} for *{to_remind}*'
        )

    @reminder.command(
        name='list',
        description='Shows your latest currently running reminders.',
    )
    async def reminder_list(self, ctx: Context) -> None:
        """Shows your currently running reminders."""
        query = """
            SELECT id, expires, metadata #>> '{args,2}'
            FROM timers
            WHERE event = 'reminder'
              AND metadata #>> '{args,0}' = $1
            ORDER BY expires;
        """
        records = await self.bot.db.fetch(query, str(ctx.author.id))

        if len(records) == 0:
            await ctx.send_error('No currently running reminders.')
            return

        embed = discord.Embed(title='Your Reminders',
                              description='Here is a list of the last **reminders** you\'ve set.',
                              color=helpers.Colour.white())
        embed.set_author(name=str(ctx.author), icon_url=get_asset_url(ctx.author))
        embed.set_footer(text=f'Showing {pluralize(len(records)):Reminder}')

        for index, (reminder_id, expires, message) in enumerate(records, 1):
            shorten = textwrap.shorten(message, width=512)
            value = f'*{shorten!r}* expires {discord.utils.format_dt(expires, style='R')}'
            embed.add_field(name=f'#{index} • [{reminder_id}]', value=value, inline=False)

        await ctx.send(embed=embed)

    @reminder.command(
        name='delete',
        description='Deletes a reminder by its ID.',
        aliases=['del', 'remove', 'rm'],
    )
    @describe(reminder_id='The ID of the reminder to delete.')
    async def reminder_delete(self, ctx: Context, *, reminder_id: int) -> None:
        """Deletes a reminder by its ID.
        To get a reminder ID, use the reminder list command.
        You must own the reminder to delete it, obviously.
        """
        query = """
            DELETE FROM timers WHERE id=$1
            AND event = 'reminder'
            AND metadata #>> '{args,0}' = $2;
        """
        status = await ctx.db.execute(query, reminder_id, str(ctx.author.id))
        if status == 'DELETE 0':
            await ctx.send_error('Could not delete any reminders with that ID.')
            return

        timers = self.bot.timers
        if timers and timers._loaded_timer and timers._loaded_timer.id == reminder_id:
            timers.reset_task()

        await ctx.send_success('Successfully deleted reminder.', ephemeral=True)

    @reminder.command(
        name='purge',
        description='Purges all reminders you have set.',
    )
    async def reminder_purge(self, ctx: Context) -> None:
        """Purges all reminders you have set."""
        query = """
            SELECT COUNT(*) FROM timers
            WHERE event = 'reminder'
            AND metadata #>> '{args,0}' = $1;
        """
        total: int = await self.bot.db.fetchval(query, str(ctx.author.id))
        if total == 0:
            await ctx.send_success('You do not have any reminders to delete.')
            return

        confirm = await ctx.confirm(f'{Emojis.warning} Are you sure you want to delete {formats.pluralize(total):reminder}?')
        if not confirm:
            return

        query = "DELETE FROM timers WHERE event = 'reminder' AND metadata #>> '{args,0}' = $1;"
        await ctx.db.execute(query, str(ctx.author.id))

        timers = self.bot.timers
        if timers and timers._loaded_timer and timers._loaded_timer.args[0] == ctx.author.id:
            timers.reset_task()

        await ctx.send_success(
            f'Successfully deleted {pluralize(total):reminder}.', ephemeral=True)

    @Cog.listener()
    async def on_reminder_timer_complete(self, timer: Timer) -> None:
        """|coro|

        The event that is called when a reminder timer is complete.

        Parameters
        -----------
        timer: :class:`Timer`
            The timer that is complete.
        """
        timer = ReminderTimer.from_timer(timer)

        try:
            channel = self.bot.get_channel(timer.channel_id) or (await self.bot.fetch_channel(timer.channel_id))
        except discord.HTTPException:
            return

        guild_id = channel.guild.id if isinstance(channel, (discord.TextChannel, discord.Thread)) else '@me'
        message_id = timer['message_id']
        view = MISSING

        if message_id:
            url = f'https://discord.com/channels/{guild_id}/{channel.id}/{message_id}'
            view = SnoozeTimerView(self, url=url, timer=timer, author_id=timer.author_id)

        to_send = f'<@{timer.author_id}>, {timer.human_delta()}: *{timer.text}*'
        try:
            msg = await channel.send(to_send, view=view)
        except discord.HTTPException:
            return
        else:
            if view is not MISSING:
                view.message = msg


async def setup(bot) -> None:
    await bot.add_cog(Reminder(bot))
