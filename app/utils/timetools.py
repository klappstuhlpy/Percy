from __future__ import annotations

import datetime
import random
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypeAlias

import discord
import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from discord import app_commands
from discord.ext import commands

from app.utils import human_join, humanize_list, pluralize
from app.utils.helpers import copy_dict
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context
else:
    Context: TypeAlias = commands.Context

units = pdt.pdtLocales['en_US'].units
units['minutes'].append('mins')
units['seconds'].append('secs')


class ShortTime:
    """Time parser that parses short human times.

    Examples
    --------
    - 2y
    - 2 months
    - 4 weeks
    - 4min
    - Unix Timestamps (<t:1620000000:f>)
    """

    SHORT_TIME_COMPILED: ClassVar[re.Pattern] = re.compile(
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

    DISCORD_UNIX_TS: ClassVar[re.Pattern] = re.compile(r'<t:(?P<ts>[0-9]+)(?::?[RFfDdTt])?>')

    dt: datetime.datetime

    def __init__(
            self,
            argument: str,
            *,
            now: datetime.datetime | None = None,
            tzinfo: datetime.tzinfo = datetime.UTC,
            as_timedelta: bool = False,
    ) -> None:
        match = self.SHORT_TIME_COMPILED.fullmatch(argument)
        if match is None or not match.group(0):
            match = self.DISCORD_UNIX_TS.fullmatch(argument)
            if match is not None:
                self.dt = datetime.datetime.fromtimestamp(int(match.group('ts')), tz=datetime.UTC)
                if tzinfo is not datetime.UTC:
                    self.dt = self.dt.astimezone(tzinfo)
                return
            else:
                raise commands.BadArgument('Invalid time passed, try e.g. "30m", "2 hours".')

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        now = now or datetime.datetime.now(datetime.UTC)

        self.dt = now + relativedelta(**data)
        if tzinfo is not datetime.UTC:
            self.dt = self.dt.astimezone(tzinfo)

        if as_timedelta:
            self.dt = self.dt - now  # type: ignore

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.UTC
        config = await ctx.bot.db.get_user_config(ctx.author.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
        return cls(argument, now=ctx.message.created_at, tzinfo=tzinfo)

    @classmethod
    async def transform(cls, interaction: discord.Interaction, value: str) -> Self:
        tzinfo = datetime.UTC
        config = await interaction.client.db.get_user_config(interaction.user.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
        now = interaction.created_at.astimezone(tzinfo)
        return cls(value, now=now, tzinfo=tzinfo)


# we do this to ensure that the annotation checker from dpy recognizes the transform and convert methods
# if we use this class as an annotation in a commands params
@copy_dict(ShortTime)
class TimeDelta(ShortTime):
    """A Converter that parses a time delta string.

    Examples
    --------
    - 2y
    - 2 months
    - 4 weeks
    - 4min
    """

    dt: datetime.timedelta

    def __init__(
            self,
            argument: str,
            *,
            now: datetime.datetime | None = None,
            tzinfo: datetime.tzinfo = datetime.UTC,
    ) -> None:
        super().__init__(argument, now=now, tzinfo=tzinfo, as_timedelta=True)

    __dict__ = ShortTime.__dict__


class HumanTime:
    """A Converter that parses a human time string.

    Examples
    --------
    - Tomorrow
    - 3 days
    - 2 weeks
    """
    CALENDER: ClassVar[pdt.Calendar] = pdt.Calendar(version=pdt.VERSION_CONTEXT_STYLE)

    def __init__(
            self,
            argument: str,
            *,
            now: datetime.datetime | None = None,
            tzinfo: datetime.tzinfo = datetime.UTC,
    ) -> None:
        now = now or datetime.datetime.now(tzinfo)
        dt, status = self.CALENDER.parseDT(argument, sourceTime=now, tzinfo=None)  # type: datetime.datetime, Any

        if not status.hasDateOrTime:  # If no date or time was provided, means it could not be parsed, raise an error
            raise commands.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        if not status.hasTime:  # If no time was provided, set it to the current time
            dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second, microsecond=now.microsecond)

        self.dt: datetime.datetime = dt.replace(tzinfo=tzinfo)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.UTC)
        self._past: bool = self.dt < now

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Self:
        tzinfo = datetime.UTC
        config = await ctx.bot.db.get_user_config(ctx.author.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
        return cls(argument, now=ctx.message.created_at, tzinfo=tzinfo)

    @classmethod
    async def transform(cls, interaction: discord.Interaction, value: str) -> Self:
        tzinfo = datetime.UTC
        config = await interaction.client.db.get_user_config(interaction.user.id)
        if config and config.timezone:
            tzinfo = config.tzinfo
        return cls(value, now=interaction.created_at.astimezone(tzinfo), tzinfo=tzinfo)


@copy_dict(HumanTime)
class Time(HumanTime):
    """A Converter that parses a time and ensures it is in the future."""

    def __init__(
            self,
            argument: str,
            *,
            now: datetime.datetime | None = None,
            tzinfo: datetime.tzinfo = datetime.UTC,
    ) -> None:
        try:
            time = ShortTime(argument, now=now, tzinfo=tzinfo)
        except commands.BadArgument:
            super().__init__(argument, now=now, tzinfo=tzinfo)
        else:
            self.dt = time.dt
            self._past = False


@copy_dict(HumanTime)
class FutureTime(Time):
    """A Converter that ensures the time is in the future."""

    def __init__(
            self,
            argument: str,
            *,
            now: datetime.datetime | None = None,
            tzinfo: datetime.tzinfo = datetime.UTC,
    ) -> None:
        super().__init__(argument, now=now, tzinfo=tzinfo)

        if self._past:
            raise commands.BadArgument('This time is in the past.')


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
            raise commands.BadArgument('Invalid time provided.') from None

    async def transform(self, interaction: discord.Interaction, value: str) -> relativedelta:
        try:
            return self.__do_conversion(value)
        except ValueError:
            raise app_commands.AppCommandError(f'{Emojis.error} Invalid time provided.') from None


class FriendlyTimeResult:
    """Provides a result for :class:`UserFriendlyTime`."""

    dt: datetime.datetime
    arg: str

    __slots__ = ('dt', 'arg')

    def __init__(self, dt: datetime.datetime) -> None:
        self.dt: datetime.datetime = dt
        self.arg: str = ''

    async def ensure_constraints(
            self, ctx: Context, uft: UserFriendlyTime, now: datetime.datetime, remaining: str
    ) -> None:
        if self.dt < now:
            raise commands.BadArgument('This time is in the past.')

        if not remaining:
            if uft.default is None:
                raise commands.BadArgument('Missing argument after the time.')
            remaining = uft.default

        if uft.converter is not None:
            self.arg = await uft.converter.convert(ctx, remaining)
        else:
            self.arg = remaining


class UserFriendlyTime(commands.Converter):
    """Converter to extract a time from a string with optional remaining arguments (context).

    Parameters
    ----------
    converter: type[commands.Converter] | None
        A converter to use to convert the remaining argument after the time.
    default: Any
        The default argument to use if none is provided after the time.

    Examples
    --------
    - Do this on 4th of July at 8pm: `4th of July 8pm`
    - Remind me at 8pm tomorrow: `8pm tomorrow`
    - Remind me in 3 days: `3 days`
    - Help me with my homework in 3 days: `in 3 days`
    """

    def __init__(
            self,
            converter: type[commands.Converter] | None = None,
            *,
            default: Any = None,
    ) -> None:
        if isinstance(converter, type) and issubclass(converter, commands.Converter):
            converter = converter()

        if converter is not None and not isinstance(converter, commands.Converter):
            raise TypeError('Object `converter` needs to be subclass of commands.Converter.')

        self.converter: commands.Converter = converter
        self.default: Any = default

    async def convert(self, ctx: Context, argument: str) -> FriendlyTimeResult:
        calendar = HumanTime.CALENDER
        regex = ShortTime.SHORT_TIME_COMPILED

        tzinfo = datetime.UTC
        config = await ctx.bot.db.get_user_config(ctx.author.id)
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
                    datetime.datetime.fromtimestamp(int(match.group('ts')), tz=datetime.UTC).astimezone(tzinfo)
                )
                remaining = argument[match.end():].strip()
                await result.ensure_constraints(ctx, self, now, remaining)
                return result

        if argument.endswith('from now'):
            argument = argument[:-8].strip()

        if argument[0:2] == 'me' and argument[0:6] in ('me to ', 'me in ', 'me at '):
            argument = argument[6:]

        now = now.astimezone(tzinfo)
        elements = calendar.nlp(argument, sourceTime=now)
        if elements is None or len(elements) == 0:
            raise commands.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        dt, status, begin, end, dt_string = elements[0]

        if not status.hasDateOrTime:
            raise commands.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        if begin not in (0, 1) and end != len(argument):
            raise commands.BadArgument(
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
                    raise commands.BadArgument('Expected quote before time input...')

                if not (end < len(argument) and argument[end] == '"'):
                    raise commands.BadArgument('If the time is quoted, you must unquote it.')

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
        source: datetime.datetime | None = None,
        accuracy: int | None = 3,
        brief: bool = False,
        suffix: bool = True,
) -> str:
    """Returns a human-readable time difference between the given datetime and the current time.

    Parameters
    ----------
    dt : datetime.datetime
        The datetime to compare with.
    source : datetime.datetime | None
        The source datetime to compare with. Defaults to the current time.
    accuracy : int | None
        The accuracy of the output. Defaults to 3.
    brief : bool
        Whether to output the time difference in a brief format. Defaults to False.
    suffix : bool
        Whether to add a suffix to the output. Defaults to True.

    Returns
    -------
    str
        The human-readable time difference.
    """
    now = source or datetime.datetime.now(datetime.UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)

    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)

    now = now.replace(microsecond=0)
    dt = dt.replace(microsecond=0)

    now = now.astimezone(datetime.UTC)
    dt = dt.astimezone(datetime.UTC)

    if dt > now:
        delta = relativedelta(dt, now)
        output_suffix = ''
    else:
        delta = relativedelta(now, dt)
        output_suffix = ' ago' if suffix else ''

    UNIT_MAP = [
        ('year', 'y'),
        ('month', 'mo'),
        ('day', 'd'),
        ('hour', 'h'),
        ('minute', 'm'),
        ('second', 's'),
    ]

    output = []
    for attr, brief_attr in UNIT_MAP:
        elem = getattr(delta, attr + 's')
        if not elem:
            continue

        if attr == 'day':
            weeks = delta.weeks
            if weeks:
                elem -= weeks * 7
                if not brief:
                    output.append(format(pluralize(weeks), 'week'))
                else:
                    output.append(f'{weeks}w')

        if elem <= 0:
            continue

        if brief:
            output.append(f'{elem}{brief_attr}')
        else:
            output.append(format(pluralize(elem), attr))

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


def ensure_future_time(
        argument: str, now: datetime.datetime, tzinfo: datetime.tzinfo = datetime.UTC
) -> datetime.datetime:
    """Ensures that the given argument is a valid time in the future. (At least 5 minutes from now)"""
    if now.tzinfo is not None:
        now = now.astimezone(datetime.UTC).replace(tzinfo=None)

    try:
        converter = Time(argument, now=now, tzinfo=tzinfo)
    except commands.BadArgument:
        random_future = now + datetime.timedelta(days=random.randint(3, 60))
        raise commands.BadArgument(f'Due date could not be parsed, sorry. Try something like "tomorrow" or "{random_future.date()}".')

    minimum_time = now + datetime.timedelta(minutes=5)
    if converter.dt < minimum_time:
        raise commands.BadArgument('Due date must be at least 5 minutes in the future.')

    return converter.dt


def humanize_duration(seconds: float | datetime.timedelta, depth: int = 3) -> str:
    """Formats a duration (in seconds) into one that is human-readable."""
    if isinstance(seconds, datetime.timedelta):
        seconds = seconds.total_seconds()
    if seconds < 1:
        return '<1 second'

    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    mo, d = divmod(d, 30)
    y, mo = divmod(mo, 12)

    if y > 100:
        return ">100 years"

    y, mo, d, h, m, s = (int(entity) for entity in (y, mo, d, h, m, s))
    items = (y, 'year'), (mo, 'month'), (d, 'day'), (h, 'hour'), (m, 'minute'), (s, 'second')

    as_list = [f'{quantity} {unit}{'s' if quantity != 1 else ''}' for quantity, unit in items if quantity > 0]
    return humanize_list(as_list[:depth])


def humanize_small_duration(seconds: float) -> str:
    """Turns a very small duration into a human-readable string."""
    units = ('ms', 'μs', 'ns', 'ps')

    for i, unit in enumerate(units, start=1):
        boundary = 10 ** (3 * i)

        if seconds > 1 / boundary:
            m = seconds * boundary
            m = round(m, 2) if m >= 10 else round(m, 3)

            return f'{m} {unit}'

    return '<1 ps'


def convert_duration(milliseconds: float) -> time:
    seconds = milliseconds / 1000
    formaT = '%H:%M:%S' if seconds >= 3600 else '%M:%S'
    return time.strftime(formaT, time.gmtime(seconds))
