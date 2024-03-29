from __future__ import annotations

import datetime
import re
from typing import Any, Iterable, Optional, Sequence, Iterator, TypeVar, AsyncIterator, TYPE_CHECKING, Union

import asyncpg
import discord
from discord.utils import TimestampStyle  # noqa

from cogs.utils import converters
from cogs.utils.constants import INVITE_REGEX

if TYPE_CHECKING:
    from bot import Percy

T = TypeVar('T')


class plural:
    """A format spec which handles making words plural or singular based off of its value.

    Credit: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/utils/formats.py#L8-L18
    """
    def __init__(self, sized: int | float, pass_content: bool = False):
        self.sized: int | float = sized
        self.pass_content: bool = pass_content

    def __format__(self, format_spec: str) -> str:
        s = self.sized
        singular, sep, _plural = format_spec.partition('|')
        _plural = _plural or f'{singular}s'
        if self.pass_content:
            return singular if abs(s) == 1 else _plural

        if abs(s) != 1:
            return f'{s} {_plural}'
        return f'{s} {singular}'


def find_word(text: str, word: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Finds a word in a string and returns its line, start column, and end column."""
    lines = text.split('\n')
    for line_num, line in enumerate(lines):
        index = line.find(word)
        if index != -1:
            start_column = index + 1
            end_column = start_column + len(word) - 1
            return line_num + 1, start_column, end_column
    return None, None, None


def censor_invite(obj: Any, *, _regex=INVITE_REGEX) -> str:
    """Censors an invite link."""
    return _regex.sub('[censored-invite]', str(obj))


def censor_object(iterable: list[int] | Any, obj: str | discord.abc.Snowflake) -> str:
    """Censors an object if it's in the iterable."""
    if not isinstance(obj, str) and obj.id in iterable:
        return '[censored]'
    return censor_invite(obj)


def resolve_entity_id(x: int, *, guild: discord.Guild):
    """Resolves an entity ID to a mention or a name."""
    if guild.get_role(x):
        return f'<@&{x}>'
    if guild.get_channel_or_thread(x):
        return f'<#{x}>'
    return f'<@{x}>'


def valid_filename(sentence: str):
    """Returns a valid filename from a sentence."""
    disallowed_chars_pattern = re.compile(r'[^\w.-]')
    filename = sentence.replace(' ', '_')
    return re.sub(disallowed_chars_pattern, '', filename)


def betterget(obj: Any, attr: Union[str, Any], default: Any = None):
    """Gets a nested attribute from a dictionary/object and formats the output accordingly.

    Resolves
    --------
    - `dict` objects to the value of the attribute.
    - `str` objects to a `datetime` object if it's in ISO format.
    """
    if isinstance(obj, dict):
        obj = obj.get(attr, default)
    else:
        obj = getattr(obj, attr, default)

    if isinstance(obj, str):
        try:
            dt_obj = datetime.datetime.fromisoformat(obj)
        except (TypeError, ValueError):
            pass
        else:
            return dt_obj.astimezone(datetime.timezone.utc)

    return obj


def medal_emojize(seq: Iterable):
    """Yield tuples of (emoji, value) for each item in `seq`.
    The emojis are unicode emojis of the form :first_place:, :second_place:, etc.

    Note
    ----
    The maximum number of emojis is 3. (Otherwise, the emojis won't be medal emojis.)
    """
    emoji = 129351  # ord(':first_place:') # max 3
    for index, value in enumerate(seq):
        yield chr(emoji + index), value


def find_nth_occurrence(string: str, substring: str, n: int) -> int | None:
    """Return index of `n`th occurrence of `substring` in `string`, or None if not found."""
    index = 0
    for _ in range(n):
        index = string.find(substring, index+1)
        if index == -1:
            return None
    return index


async def plonk_iterator(bot: Percy, guild: discord.Guild, records: list[asyncpg.Record]) -> AsyncIterator[str]:
    """Iterates over a list of records and resolves them to a mention or a name."""
    for record in records:
        entity_id = record[0]
        resolved = guild.get_channel(entity_id) or await bot.get_or_fetch_member(guild, entity_id)
        if resolved is None:
            yield f'<Not Found: {entity_id}>'
        yield str(resolved)


def remove_html_tags(content: str) -> str:
    """Removes HTML tags from a string."""
    clean_text = re.sub('<.*?>', '', content)  # Remove HTML tags
    clean_text = re.sub(r'\s+', ' ', clean_text)  # Remove extra whitespace
    return clean_text


def readable_time(seconds: int | float, decimal: bool = False, short: bool = False) -> str:
    """Returns a human-readable time format.

    Parameters
    ----------
    seconds : `int` | `float`
        The amount of seconds to convert.
    decimal : `bool`, optional
        Whether to round the values to 2 decimal places.
    short : `bool`, optional
        Whether to use short names for the units.
    """

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    months, days = divmod(days, 30)  # Approximately
    years, months = divmod(months, 12)

    attrs = {
        'y' if short else 'year': years,
        'mo' if short else 'month': months,
        'd' if short else 'day': days,
        'hr' if short else 'hour': hours,
        'm' if short else 'minute': minutes,
        's' if short else 'second': seconds,
    }

    output = []
    for unit, value in attrs.items():
        value = round(value, 2 if decimal else None)
        if value > 0:
            output.append(f'{value}{' ' * (not short)}{unit}{('s' if value != 1 else '') * (not short)}')

    return ', '.join(output)


def shorten_number(number: int | float) -> str:
    """Shortens a number to a more readable format.

    Parameters
    ----------
    number : `int` | `float`
        The number to shorten.
    """

    number = float(f'{number:.3g}')
    magnitude = 0

    while abs(number) >= 1000:
        magnitude += 1
        number /= 1000

    return f'{f'{number:f}'.rstrip('0').rstrip('.')}{['', 'K', 'M', 'B', 'T'][magnitude]}'


def number_suffix(number: int):
    """Returns the suffix for a number.

    Parameters
    ----------
    number : `int`
        The number to get the suffix for.
    """
    if 10 <= number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')

    return f'{number}{suffix}'


def pagify(
        text: str,
        delims: Sequence[str] = ['\n'],  # noqa
        *,
        priority: bool = False,
        escape_mass_mentions: bool = True,
        shorten_by: int = 8,
        page_length: int = 2000,
) -> Iterator[str]:
    """Generate multiple pages from the given text.

    Note
    ----
    This does not respect code blocks or inline code.

    Parameters
    ----------
    text: str
        The content to pagify and send.
    delims: `sequence` of `str`, optional
        Characters where page breaks will occur. If no delimiters are found
        in a page, the page will break after ``page_length`` characters.
        By default, this only contains the newline.

    Other Parameters
    ----------------
    priority : `bool`
        Set to :code:`True` to choose the page  break delimiter based on the
        order of ``delims``. Otherwise, the page will always break at the
        last possible delimiter.
    escape_mass_mentions : `bool`
        If :code:`True`, any mass mentions (here or everyone) will be
        silenced.
    shorten_by : `int`
        How much to shorten each page by. Defaults to 8.
    page_length : `int`
        The maximum length of each page. Defaults to 2000.

    Yields
    ------
    `str`
        Pages of the given text.
    """
    page_length -= shorten_by
    start = 0
    end = len(text)
    while (end - start) > page_length:
        stop = start + page_length
        if escape_mass_mentions:
            stop -= text.count('@here', start, stop) + text.count('@everyone', start, stop)
        closest_delim = (text.rfind(d, start + 1, stop) for d in delims)
        if priority:
            closest_delim = next((x for x in closest_delim if x > 0), -1)
        else:
            closest_delim = max(closest_delim)
        stop = closest_delim if closest_delim != -1 else stop
        if escape_mass_mentions:
            to_send = discord.utils.escape_mentions(text[start:stop])
        else:
            to_send = text[start:stop]
        if len(to_send.strip()) > 0:
            yield to_send
        start = stop

    if len(text[start:end].strip()) > 0:
        if escape_mass_mentions:
            yield discord.utils.escape_mentions(text[start:end])
        else:
            yield text[start:end]


def format_date(dt: Optional[datetime.datetime], style: TimestampStyle = 'f') -> str:
    """Format a date in a human-readable format."""
    if dt is None:
        return 'N/A'
    return f'{discord.utils.format_dt(dt, style)} ({discord.utils.format_dt(dt, style='R')})'


def human_join(seq: Sequence[str], delim: str = ', ', final: str = 'or') -> str:
    """Join a sequence of strings in a human-readable format."""
    size = len(seq)
    if size == 0:
        return ''

    if size == 1:
        return seq[0]

    if size == 2:
        return f'{seq[0]} {final} {seq[1]}'

    return delim.join(seq[:-1]) + f' {final} {seq[-1]}'


class TabularData:
    """A class to create a table in rST format.

    Example
    -------

        .. code-block:: rst

            +-------+-----+
            | Name  | Age |
            +-------+-----+
            | Alice | 24  |
            |  Bob  | 19  |
            +-------+-----+
    """
    def __init__(self):
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]):
        self._columns = columns
        self._widths = [len(c) + 2 for c in columns]

    def add_row(self, row: Iterable[Any]) -> None:
        rows = [str(r) for r in row]
        self._rows.append(rows)
        for index, element in enumerate(rows):
            width = len(element) + 2
            if width > self._widths[index]:
                self._widths[index] = width

    def add_rows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.add_row(row)

    def render(self) -> str:
        """Renders a table in rST format."""
        sep = '+'.join('-' * w for w in self._widths)
        sep = f'+{sep}+'

        to_draw = [sep]

        def get_entry(d):
            elem = '|'.join(f'{e:^{self._widths[i]}}' for i, e in enumerate(d))
            return f'|{elem}|'

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_entry(row))

        to_draw.append(sep)
        return '\n'.join(to_draw)


def truncate(text: str, length: int) -> str:
    """Truncate a string to a certain length, adding an ellipsis if it was truncated."""
    if len(text) > length:
        return text[:length - 1] + '…'
    return text


def truncate_iterable(iterable: Iterable[Any], length: int) -> str:
    """Truncate an iterable to a certain length, adding an ellipsis if it was truncated."""
    if len(iterable) > length:  # type: ignore
        return ', '.join(iterable[:length]) + ', …'
    return ', '.join(iterable)


def WrapList(list_: list, length: int):
    """Wrap a list into sublists of a certain length."""
    def chunks(seq, size):
        for i in range(0, len(seq), size):
            yield seq[i: i + size]

    return list(chunks(list_, length))


def WrapDict(dict_: dict, length: int):
    """Wrap a dict into subdicts of a certain length."""
    def chunks(seq, size):
        for i in range(0, len(seq), size):
            yield {k: seq[k] for k in list(seq)[i: i + size]}

    return list(chunks(dict_, length))


def RevDict(dict_: dict):
    """Reverse a dict."""
    return {v: k for k, v in dict_.items()}


def SortDict(dict_: dict, key: Any = None, reverse: bool = False):
    """Sorts a dict by a key and returns an actual dict."""
    return dict(sorted(dict_.items(), key=key, reverse=reverse))


def get_shortened_string(length: int, start: int, string: str) -> str:
    """Shorten a string to a certain length, adding an ellipsis if it was shortened.

    Needs to be compined with the :func:`fuzzy.finder` function.

    Parameters
    ----------
    length : `int`
        The maximum length of the string.
    start : `int`
        The start index of the string.
    string : `str`
        The string to shorten.
    """

    full_length = len(string)
    if full_length <= 100:
        return string

    _id, _, remaining = string.partition(' - ')
    start_index = len(_id) + 3
    max_remaining_length = 100 - start_index

    end = start + length
    if start < start_index:
        start = start_index

    if end < 100:
        if full_length > 100:
            return string[:99] + '…'
        return string[:100]

    has_end = end < full_length
    excess = (end - start) - max_remaining_length + 1
    if has_end:
        return f'[{_id}] …{string[start + excess + 1:end]}…'
    return f'[{_id}] …{string[start + excess:end]}'


def VisualStamp(key_min: float, key_max: float, key_current: float, key_full: int = 32) -> str:
    """
    Example
    -------

    .. code-block:: python
        ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬🔘▬▬▬▬▬▬▬
    """
    if key_min == key_current:
        before = key_max
        after = key_min
    else:
        before = key_min + key_current
        after = key_max - key_current
    for i in range(int(key_min + 2), int(key_max)):
        if len(int(before / i) * '▬' + '🔘' + int(
                after / i) * '▬') <= key_full:
            return str(int(before / i) * '▬' + '🔘' + int(
                after / i) * '▬')


def PlayerStamp(length: float, position: float) -> str:
    """Converts a position and length to a human-readable format."""
    convertable = [
        converters.convert_duration(position if not position < 0 else 0.0),
        VisualStamp(0, length, position),
        converters.convert_duration(length)
    ]
    return ' '.join(convertable)
