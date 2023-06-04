from __future__ import annotations

import datetime
import math
import random
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional, Union, Collection, Tuple

import discord
import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from dateutil.tz import gettz

from .formats import plural, human_join
from discord.ext import commands
from discord import app_commands
import re

# Monkey patch mins and secs into the units
units = pdt.pdtLocales['en_US'].units
units['minutes'].append('mins')
units['seconds'].append('secs')

if TYPE_CHECKING:
    from typing_extensions import Self
    from ..utils.context import Context


class InvalidTime(RuntimeError):
    """An invalid time was provided."""
    pass


class ShortTime:
    """Time parser that parses short human times.

    Examples
    --------
    - 2y
    - 2 months
    - 4 weeks
    """
    compiled = re.compile(
        """
           (?:(?P<years>[0-9])(?:years?|y))?                    # e.g. 2y
           (?:(?P<months>[0-9]{1,2})(?:months?|mon?))?          # e.g. 2months
           (?:(?P<weeks>[0-9]{1,4})(?:weeks?|w))?               # e.g. 10w
           (?:(?P<days>[0-9]{1,5})(?:days?|d))?                 # e.g. 14d
           (?:(?P<hours>[0-9]{1,5})(?:hours?|hr?))?             # e.g. 12h
           (?:(?P<minutes>[0-9]{1,5})(?:minutes?|m(?:in)?))?    # e.g. 10m
           (?:(?P<seconds>[0-9]{1,5})(?:seconds?|s(?:ec)?))?    # e.g. 15s
        """,
        re.VERBOSE,
    )

    discord_fmt = re.compile(r'<t:(?P<ts>[0-9]+)(?::?[RFfDdTt])?>')

    dt: datetime.datetime

    def __init__(
            self,
            argument: str,
            *,
            now: Optional[datetime.datetime] = None,
            tzinfo: datetime.tzinfo = datetime.timezone.utc,
    ):
        match = self.compiled.fullmatch(argument)
        if match is None or not match.group(0):
            match = self.discord_fmt.fullmatch(argument)
            if match is not None:
                self.dt = datetime.datetime.fromtimestamp(int(match.group('ts')), tz=datetime.timezone.utc)
                if tzinfo is not datetime.timezone.utc:
                    self.dt = self.dt.astimezone(tzinfo)
                return
            else:
                raise commands.BadArgument('invalid time provided')

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        now = now or datetime.datetime.now(datetime.timezone.utc)
        self.dt = now + relativedelta(**data)
        if tzinfo is not datetime.timezone.utc:
            self.dt = self.dt.astimezone(tzinfo)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.timezone.utc
        reminder = ctx.bot.reminder
        if reminder is not None:
            tzinfo = await reminder.get_tzinfo(ctx.author.id)
        return cls(argument, now=ctx.message.created_at, tzinfo=tzinfo)


class HumanTime:
    """Time parser the uses pdt to parse short human times.

    Examples
    --------
    - Tomorrow
    - 3 days
    - 2 weeks
    """
    calendar = pdt.Calendar(version=pdt.VERSION_CONTEXT_STYLE)

    def __init__(
            self,
            argument: str,
            *,
            now: Optional[datetime.datetime] = None,
            tzinfo: datetime.tzinfo = datetime.timezone.utc,
    ):
        now = now or datetime.datetime.now(tzinfo)
        dt, status = self.calendar.parseDT(argument, sourceTime=now, tzinfo=None)

        if not status.hasDateOrTime:
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Invalid time provided, try e.g. "tomorrow" or "3 days"')

        if not status.hasTime:
            # replace it with the current time
            dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)

        self.dt: datetime.datetime = dt.replace(tzinfo=tzinfo)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        self._past: bool = self.dt < now

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.timezone.utc
        reminder = ctx.bot.reminder
        if reminder is not None:
            tzinfo = await reminder.get_tzinfo(ctx.author.id)
        return cls(argument, now=ctx.message.created_at, tzinfo=tzinfo)


class Time(HumanTime):
    """A time that is either in the past or future."""
    def __init__(
            self,
            argument: str,
            *,
            now: Optional[datetime.datetime] = None,
            tzinfo: datetime.tzinfo = datetime.timezone.utc,
    ):
        try:
            o = ShortTime(argument, now=now, tzinfo=tzinfo)
        except:
            super().__init__(argument, now=now, tzinfo=tzinfo)
        else:
            self.dt = o.dt
            self._past = False


class FutureTime(Time):
    """A time that is in the future."""
    def __init__(
            self,
            argument: str,
            *,
            now: Optional[datetime.datetime] = None,
            tzinfo: datetime.tzinfo = datetime.timezone.utc,
    ):
        super().__init__(argument, now=now, tzinfo=tzinfo)

        if self._past:
            raise commands.BadArgument('<:redTick:1079249771975413910> This time is in the past')


class BadTimeTransform(app_commands.AppCommandError):
    """Raised when a time transform fails."""
    pass


class TimeTransformer(app_commands.Transformer):
    """Transforms a string into a :class:`datetime.datetime` object.

    Basically :class:`UserFriendlyTime` but with no context and just time parsing.
    """
    async def transform(self, interaction: discord.Interaction, value: str) -> datetime.datetime:
        tzinfo = datetime.timezone.utc
        reminder = interaction.client.get_cog('Reminder')
        if reminder is not None:
            tzinfo = await reminder.get_tzinfo(interaction.user.id)

        now = interaction.created_at.replace(tzinfo=None)
        with suppress(commands.BadArgument):
            try:
                short = ShortTime(value, now=now, tzinfo=tzinfo)
            except commands.BadArgument:
                try:
                    human = FutureTime(value, now=now, tzinfo=tzinfo)
                except commands.BadArgument as e:
                    raise BadTimeTransform(str(e)) from None
                else:
                    return human.dt
            else:
                return short.dt


class FriendlyTimeResult:
    """Provides a result for :class:`UserFriendlyTime`."""

    dt: datetime.datetime
    arg: str

    __slots__ = ('dt', 'arg')

    def __init__(self, dt: datetime.datetime):
        self.dt = dt
        self.arg = ''

    async def ensure_constraints(
            self, ctx: Context, uft: UserFriendlyTime, now: datetime.datetime, remaining: str
    ) -> None:
        if self.dt < now:
            raise commands.BadArgument('<:redTick:1079249771975413910> This time is in the past.')

        if not remaining:
            if uft.default is None:
                raise commands.BadArgument('<:redTick:1079249771975413910> Missing argument after the time.')
            remaining = uft.default

        if uft.converter is not None:
            self.arg = await uft.converter.convert(ctx, remaining)
        else:
            self.arg = remaining


class RelativeDelta(app_commands.Transformer, commands.Converter):
    """A converter that parses a relative delta."""
    @classmethod
    def __do_conversion(cls, argument: str) -> relativedelta:
        match = ShortTime.compiled.fullmatch(argument)
        if match is None or not match.group(0):
            raise ValueError('Invalid time provided')

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        return relativedelta(**data)

    async def convert(self, ctx: Context, argument: str) -> relativedelta:
        try:
            return self.__do_conversion(argument)
        except ValueError as e:
            raise commands.BadArgument("<:redTick:1079249771975413910> " + str(e)) from None

    async def transform(self, interaction, value: str) -> relativedelta:
        try:
            return self.__do_conversion(value)
        except ValueError as e:
            raise app_commands.AppCommandError("<:redTick:1079249771975413910> " + str(e)) from None


class UserFriendlyTime(commands.Converter):
    """Converter Class to convert a human time input into a datetime.datetime object.

    Examples
    --------
    - Do this on 4th of July at 8pm: `4th of July 8pm`
    - Remind me at 8pm tomorrow: `8pm tomorrow`
    - Remind me in 3 days: `3 days`
    - Help me with my homework in 3 days: `in 3 days`
    """

    def __init__(
            self,
            converter: Optional[Union[type[commands.Converter], commands.Converter]] = None,
            *,
            default: Any = None,
    ):
        if isinstance(converter, type) and issubclass(converter, commands.Converter):
            converter = converter()

        if converter is not None and not isinstance(converter, commands.Converter):
            raise TypeError('commands.Converter subclass necessary.')

        self.converter: commands.Converter = converter  # type: ignore  # It doesn't understand this narrowing
        self.default: Any = default

    async def convert(self, ctx: Context, argument: str) -> FriendlyTimeResult:
        calendar = HumanTime.calendar
        regex = ShortTime.compiled
        reminder = ctx.bot.reminder

        tzinfo = datetime.timezone.utc
        if reminder is not None:
            tzinfo = await reminder.get_tzinfo(ctx.author.id)

        now = ctx.message.created_at

        match = regex.match(argument)
        if match is not None and match.group(0):
            data = {k: int(v) for k, v in match.groupdict(default=0).items()}
            remaining = argument[match.end():].strip()
            dt = now + relativedelta(**data)
            result = FriendlyTimeResult(dt.astimezone(tzinfo))
            await result.ensure_constraints(ctx, self, now, remaining)
            return result

        if match is None or not match.group(0):
            match = ShortTime.discord_fmt.match(argument)
            if match is not None:
                result = FriendlyTimeResult(
                    datetime.datetime.fromtimestamp(int(match.group('ts')), tz=datetime.timezone.utc).astimezone(tzinfo)
                )
                remaining = argument[match.end():].strip()
                await result.ensure_constraints(ctx, self, now, remaining)
                return result

        if argument.endswith('from now'):
            argument = argument[:-8].strip()

        if argument[0:2] == 'me':
            if argument[0:6] in ('me to ', 'me in ', 'me at '):
                argument = argument[6:]

        now = now.astimezone(tzinfo)
        elements = calendar.nlp(argument, sourceTime=now)
        if elements is None or len(elements) == 0:
            raise commands.BadArgument('<:redTick:1079249771975413910> '
                                       'Invalid time provided, try e.g. "tomorrow" or "3 days".')

        dt, status, begin, end, dt_string = elements[0]

        if not status.hasDateOrTime:
            raise commands.BadArgument('<:redTick:1079249771975413910> '
                                       'Invalid time provided, try e.g. "tomorrow" or "3 days".')

        if begin not in (0, 1) and end != len(argument):
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Time is either in an inappropriate location, which '
                'must be either at the end or beginning of your input, '
                'or I just flat out did not understand what you meant. Sorry.'
            )

        if not status.hasTime:
            dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)
        if status.accuracy == pdt.pdtContext.ACU_HALFDAY:
            dt = dt.replace(day=now.day + 1)

        result = FriendlyTimeResult(dt.replace(tzinfo=tzinfo))
        remaining = ''

        if begin in (0, 1):
            if begin == 1:
                if argument[0] != '"':
                    raise commands.BadArgument('<:redTick:1079249771975413910> Expected quote before time input...')

                if not (end < len(argument) and argument[end] == '"'):
                    raise commands.BadArgument('<:redTick:1079249771975413910> '
                                               'If the time is quoted, you must unquote it.')

                remaining = argument[end + 1:].lstrip(' ,.!')
            else:
                remaining = argument[end:].lstrip(' ,.!')
        elif len(argument) == end:
            remaining = argument[:begin].strip()

        await result.ensure_constraints(ctx, self, now, remaining)
        return result


def human_timedelta(
        dt: datetime.datetime,
        *,
        source: Optional[datetime.datetime] = None,
        accuracy: Optional[int] = 3,
        brief: bool = False,
        suffix: bool = True,
) -> str:
    """Returns a human readable timedelta since the datetime object was passed."""
    now = source or datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    now = now.replace(microsecond=0)
    dt = dt.replace(microsecond=0)

    if dt > now:
        delta = relativedelta(dt, now)
        output_suffix = ''
    else:
        delta = relativedelta(now, dt)
        output_suffix = ' ago' if suffix else ''

    attrs = [
        ('year', 'y'),
        ('month', 'mo'),
        ('day', 'd'),
        ('hour', 'h'),
        ('minute', 'm'),
        ('second', 's'),
    ]

    output = []
    for attr, brief_attr in attrs:
        elem = getattr(delta, attr + 's')
        if not elem:
            continue

        if attr == 'day':
            weeks = delta.weeks
            if weeks:
                elem -= weeks * 7
                if not brief:
                    output.append(format(plural(weeks), 'week'))
                else:
                    output.append(f'{weeks}w')

        if elem <= 0:
            continue

        if brief:
            output.append(f'{elem}{brief_attr}')
        else:
            output.append(format(plural(elem), attr))

    if accuracy is not None:
        output = output[:accuracy]

    if len(output) == 0:
        return 'now'
    else:
        if not brief:
            return human_join(output, final='and') + output_suffix
        else:
            return ' '.join(output) + output_suffix


def get_timezone_offset(dt: datetime.datetime) -> str:
    """Returns the Timezone offset of a datetime object as a string.

    Example: UTC +01:00
    """
    offset = dt.strftime('%z')
    offset_hours = int(offset) // 100
    offset_minutes = int(offset) % 100

    sign = '-' if offset_hours < 0 else '+'
    offset_hours = abs(offset_hours)
    offset_minutes = abs(offset_minutes)
    offset_formatted = f'{dt.tzname()} {sign}{offset_hours:02d}:{offset_minutes:02d}'

    return offset_formatted


def mean_stddev(collection: Collection[float]) -> Tuple[float, float]:
    """Takes a collection of floats and returns (mean, stddev) as a tuple."""

    average = sum(collection) / len(collection)

    if len(collection) > 1:
        stddev = math.sqrt(sum(math.pow(reading - average, 2) for reading in collection) / (len(collection) - 1))
    else:
        stddev = 0.0

    return average, stddev


def ensure_future_time(
        argument: str, now: datetime.datetime, tzinfo: datetime.tzinfo = datetime.timezone.utc
) -> datetime.datetime:
    """Ensures that the given argument is a valid time in the future. (At least 5 minutes from now)"""
    if now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    try:
        converter = Time(argument, now=now, tzinfo=tzinfo)
    except commands.BadArgument:
        random_future = now + datetime.timedelta(days=random.randint(3, 60))
        raise InvalidTime(
            f'<:redTick:1079249771975413910> Due date could not be parsed, sorry. Try something like "tomorrow" or "{random_future.date()}".')

    minimum_time = now + datetime.timedelta(minutes=5)
    if converter.dt < minimum_time:
        raise InvalidTime('<:redTick:1079249771975413910> Due date must be at least 5 minutes in the future.')

    return converter.dt


async def future_time_from_interaction(
        argument: str, interaction: discord.Interaction
) -> tuple[str, datetime.datetime]:
    """Ensures that the given argument is a valid time in the future. (At least 5 minutes from now)"""
    reminder: Optional[Reminder] = interaction.client.get_cog('Reminder')  # type: ignore
    timezone = 'UTC'
    tzinfo = datetime.timezone.utc
    if reminder is not None:
        timezone = await reminder.get_timezone(interaction.user.id)
        if timezone is not None:
            tzinfo = gettz(timezone) or datetime.timezone.utc
        else:
            timezone = 'UTC'

    dt = ensure_future_time(argument, interaction.created_at, tzinfo)
    return timezone, dt
