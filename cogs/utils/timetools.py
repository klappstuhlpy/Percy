from __future__ import annotations

import datetime
import math
import random
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional, Union, Collection, Tuple

import discord
import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from discord import app_commands
from discord.ext import commands

from . import errors
from .formats import plural, human_join

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
    - 4min
    - Unix Timestamps
    """

    SHORT_TIME_COMPILED = re.compile(
        r"""
           (?:(?P<years>[0-9])(\s+)?(?:years?|y))?
           (?:(?P<months>[0-9]{1,2})(\s+)?(?:months?|mon?))?
           (?:(?P<weeks>[0-9]{1,4})(\s+)?(?:weeks?|w))?
           (?:(?P<days>[0-9]{1,5})(\s+)?(?:days?|d))?
           (?:(?P<hours>[0-9]{1,5})(\s+)?(?:hours?|hr?|hr?s?))?
           (?:(?P<minutes>[0-9]{1,5})(\s+)?(?:minutes?|m(?:ins?)?))?
           (?:(?P<seconds>[0-9]{1,5})(\s+)?(?:seconds?|s(?:ecs?)?))?
           (?:(?P<microseconds>[0-9]{1,7})(\s+)?(?:microseconds?|ms))?
        """,
        re.VERBOSE,
    )

    DISCORD_UNIX_TS = re.compile(r'<t:(?P<ts>[0-9]+)(?::?[RFfDdTt])?>')

    dt: datetime.datetime

    def __init__(
            self,
            argument: str,
            *,
            now: Optional[datetime.datetime] = None,
            tzinfo: datetime.tzinfo = datetime.timezone.utc,
    ):
        match = self.SHORT_TIME_COMPILED.fullmatch(argument)
        if match is None or not match.group(0):
            match = self.DISCORD_UNIX_TS.fullmatch(argument)
            if match is not None:
                self.dt = datetime.datetime.fromtimestamp(int(match.group('ts')), tz=datetime.timezone.utc)
                if tzinfo is not datetime.timezone.utc:
                    self.dt = self.dt.astimezone(tzinfo)
                return
            else:
                raise errors.BadArgument('Invalid time passed, try e.g. "30m", "2 hours".')

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        now = now or datetime.datetime.now(datetime.timezone.utc)
        self.dt = now + relativedelta(**data)
        if tzinfo is not datetime.timezone.utc:
            self.dt = self.dt.astimezone(tzinfo)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.timezone.utc
        config = await ctx.bot.user_settings.get_user_config(ctx.author.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
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
        dt, status = self.calendar.parseDT(argument, sourceTime=now, tzinfo=None)  # type: datetime.datetime, Any

        if not status.hasDateOrTime:  # If no date or time was provided, means it could not be parsed, raise an error
            raise errors.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days"')

        if not status.hasTime:  # If no time was provided, set it to the current time
            dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)

        self.dt: datetime.datetime = dt.replace(tzinfo=tzinfo)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        self._past: bool = self.dt < now

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.timezone.utc
        config = await ctx.bot.user_settings.get_user_config(ctx.author.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
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
            time = ShortTime(argument, now=now, tzinfo=tzinfo)
        except:  # noqa
            super().__init__(argument, now=now, tzinfo=tzinfo)
        else:
            self.dt = time.dt
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
            raise errors.BadArgument('This time is in the past')


class BadTimeTransform(app_commands.AppCommandError):
    """Raised when a time transform fails."""
    pass


class TimeTransformer(app_commands.Transformer):
    """Transforms a string into a :class:`datetime.datetime` object.

    Basically :class:`UserFriendlyTime` but with no context and just time parsing.
    """

    def __init__(
            self, future: bool = False, short: bool = False
    ):
        self.future = future
        self.short = short

    async def transform(self, interaction: discord.Interaction, value: str) -> datetime.datetime:
        tzinfo = datetime.timezone.utc
        config = await interaction.client.user_settings.get_user_config(interaction.user.id)
        if config and config.timezone:
            tzinfo = config.tzinfo

        now = interaction.created_at.astimezone(tzinfo)
        with suppress(commands.BadArgument):
            try:
                if self.future:
                    time = FutureTime(value, now=now, tzinfo=tzinfo)
                elif self.short:
                    time = ShortTime(value, now=now, tzinfo=tzinfo)
                else:
                    try:
                        time = ShortTime(value, now=now, tzinfo=tzinfo)
                    except commands.BadArgument:
                        time = FutureTime(value, now=now, tzinfo=tzinfo)
            except commands.BadArgument as e:
                raise BadTimeTransform(str(e)) from None

            return time.dt


class FriendlyTimeResult:
    """Provides a result for :class:`UserFriendlyTime`."""

    dt: datetime.datetime
    arg: str

    __slots__ = ('dt', 'arg')

    def __init__(self, dt: datetime.datetime):
        self.dt: datetime.datetime = dt
        self.arg: str = ''

    async def ensure_constraints(
            self, ctx: Context, uft: UserFriendlyTime, now: datetime.datetime, remaining: str
    ) -> None:
        if self.dt < now:
            raise errors.BadArgument('This time is in the past.')

        if not remaining:
            if uft.default is None:
                raise errors.BadArgument('Missing argument after the time.')
            remaining = uft.default

        if uft.converter is not None:
            self.arg = await uft.converter.convert(ctx, remaining)
        else:
            self.arg = remaining


class RelativeDelta(app_commands.Transformer, commands.Converter):
    """A converter that parses a relative delta."""

    @classmethod
    def __do_conversion(cls, argument: str) -> relativedelta:
        """Converts a string into a :class:`relativedelta` object."""
        match = ShortTime.SHORT_TIME_COMPILED.fullmatch(argument)
        if match is None or not match.group(0):
            raise ValueError

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        return relativedelta(**data)

    async def convert(self, ctx: Context, argument: str) -> relativedelta:
        try:
            return self.__do_conversion(argument)
        except ValueError:
            raise errors.BadArgument('Invalid time provided.') from None

    async def transform(self, interaction, value: str) -> relativedelta:
        try:
            return self.__do_conversion(value)
        except ValueError:
            raise app_commands.AppCommandError('<:redTick:1079249771975413910> Invalid time provided.') from None


class UserFriendlyTime(commands.Converter):
    """Converter Class to convert a human time input with optional context into a datetime.datetime object.

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
            raise TypeError('Object `converter` needs to be subclass of commands.Converter.')

        self.converter: commands.Converter = converter
        self.default: Any = default

    async def convert(self, ctx: Context, argument: str) -> FriendlyTimeResult:
        calendar = HumanTime.calendar
        regex = ShortTime.SHORT_TIME_COMPILED

        tzinfo = datetime.timezone.utc
        config = await ctx.bot.user_settings.get_user_config(ctx.author.id)
        if config and config.timezone:
            tzinfo = config.tzinfo

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
            match = ShortTime.DISCORD_UNIX_TS.match(argument)
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
            raise errors.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        dt, status, begin, end, dt_string = elements[0]

        if not status.hasDateOrTime:
            raise errors.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        if begin not in (0, 1) and end != len(argument):
            raise errors.BadArgument(
                'Time is either in an inappropriate location, which '
                'must be either at the end or beginning of your input, '
                'or I just flat out did not understand what you meant. Sorry.'
            )

        dt = dt.replace(tzinfo=tzinfo)
        if not status.hasTime:
            dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)

        if status.hasTime and not status.hasDate and dt < now:
            dt = dt + datetime.timedelta(days=1)

        if status.accuracy == pdt.pdtContext.ACU_HALFDAY:
            dt = dt.replace(day=now.day + 1)

        result = FriendlyTimeResult(dt)
        remaining = ''

        if begin in (0, 1):
            if begin == 1:
                if argument[0] != '"':
                    raise errors.BadArgument('Expected quote before time input...')

                if not (end < len(argument) and argument[end] == '"'):
                    raise errors.BadArgument(''
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
    """Returns a human-readable timedelta since the datetime object was passed."""
    now = source or datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    now = now.replace(microsecond=0)
    dt = dt.replace(microsecond=0)

    now = now.astimezone(datetime.timezone.utc)
    dt = dt.astimezone(datetime.timezone.utc)

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


def get_timezone_offset(dt: datetime.datetime, with_name: bool = False) -> str:
    """Returns the Timezone offset of a datetime object as a string.

    Example: 'UTC +00:00'
    """

    offset = dt.utcoffset()
    if offset is None:
        offset_format = '+00:00'
    else:
        minutes, _ = divmod(int(offset.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        offset_format = f'{hours:+03d}:{minutes:02d}'

    if not with_name:
        return offset_format

    return f'{dt.tzname()} {offset_format}'


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
    tzinfo = datetime.timezone.utc
    config = await interaction.client.user_settings.get_user_config(interaction.user.id)
    if config and config.timezone:
        timezone = config.tzinfo
    else:
        timezone = 'UTC'

    dt = ensure_future_time(argument, interaction.created_at, tzinfo)
    return timezone, dt
