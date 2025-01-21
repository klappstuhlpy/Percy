import datetime
import locale
import logging
import os
import re
import textwrap
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, BinaryIO, ParamSpec, TypeVar, Mapping

import dateparser
import discord

P = ParamSpec('P')
T = TypeVar('T')

KwargT = TypeVar('KwargT')

try:
    # Try to set the locale
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    # If it fails, fall back to 'C' locale or log the issue
    locale.setlocale(locale.LC_ALL, 'C')
    logging.warning(f"WARNING: Locale 'en_US.UTF-8' not supported, falling back to 'C'.")


class SentinelConstant:  # Exists for type hinting purposes
    pass


ConstantT = TypeVar('ConstantT', bound=SentinelConstant)


def _create_sentinel_callback(v: KwargT) -> Callable[[ConstantT], KwargT]:
    def wrapper(_self: ConstantT) -> KwargT:
        return v

    return wrapper


def sentinel(name: str, **dunders: KwargT) -> ConstantT:
    """Creates a constant singleton object.

    Parameters
    ----------
    name : `str`
        The name of the constant.
    dunders : `dict`
        The dunder methods to add to the constant.
        Those are getting set to a callback that returns the value of the dunder double underscore.

    Returns
    -------
    `ConstantT`
        The constant singleton object.
    """
    attrs = {f'__{k}__': _create_sentinel_callback(v) for k, v in dunders.items()}
    return type(name, (SentinelConstant,), attrs)()


class pluralize:
    """A format spec which handles making words pluralize or singular based off of its value."""

    def __init__(self, sized: int | float, pass_content: bool = False) -> None:
        self.sized: int | float = sized
        self.pass_content: bool = pass_content

    def __format__(self, format_spec: str) -> str:
        _sized = self.sized
        singular, _, _pluralize = format_spec.partition('|')
        final = _pluralize or f'{singular}s'
        if self.pass_content:
            return singular if abs(_sized) == 1 else final

        if abs(_sized) != 1:
            return f'{_sized} {final}'
        return f'{_sized} {singular}'


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

    def __init__(self) -> None:
        self._widths: list[int] = []
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def set_columns(self, columns: list[str]) -> None:
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

        def get_entry(d: list[str]) -> str:
            elem = '|'.join(f'{e:^{self._widths[i]}}' for i, e in enumerate(d))
            return f'|{elem}|'

        to_draw.append(get_entry(self._columns))
        to_draw.append(sep)

        for row in self._rows:
            to_draw.append(get_entry(row))

        to_draw.append(sep)
        return '\n'.join(to_draw)


def deep_to_with(obj: Any, attr: str) -> Any:
    """Deeply gets an attribute from an object until it can't.

    Useful for parenting cases where you want to get an attribute from a parent object.
    """
    if not hasattr(obj, attr):
        return obj

    current = None
    while hasattr(obj, attr):
        current = obj
        obj = getattr(obj, attr)
    return current


def find_nth_occurrence(string: str, substring: str, n: int) -> int | None:
    """Return index of `n`th occurrence of `substring` in `string`, or None if not found."""
    index = 0
    for _ in range(n):
        index = string.find(substring, index + 1)
        if index == -1:
            return None
    return index


def find_word(text: str, word: str) -> tuple[int, int, int] | None:
    """Finds a word in a string and returns its line, start column, and end column.

    Parameters
    ----------
    text : `str`
        The text to search in.
    word : `str`
        The word to search for.

    Returns
    -------
    `tuple[int | None, int | None, int | None]`
        The line, start column, and end column of the word.
    """
    lines = text.split('\n')
    for line_num, line in enumerate(lines):
        index = line.find(word)
        if index != -1:
            start_column = index + 1
            end_column = start_column + len(word) - 1
            return line_num + 1, start_column, end_column
    return None


def fnumb(number: int | float, *, _locale: str = 'en_US.UTF-8') -> str:
    """Formats a number according to the given locale."""

    # locale defined at the top of the file temporarily,
    # NOTE: temporary solution, will be moved

    # Format the number with the (set or fallback) locale
    return locale.format_string('%.2f', number, grouping=True)


def pagify(
        text: str,
        delims: Sequence[str] = ['\n'],
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
        closest_delim = next((x for x in closest_delim if x > 0), -1) if priority else max(closest_delim)
        stop = closest_delim if closest_delim != -1 else stop
        to_send = discord.utils.escape_mentions(text[start:stop]) if escape_mass_mentions else text[start:stop]
        if len(to_send.strip()) > 0:
            yield to_send
        start = stop

    if len(text[start:end].strip()) > 0:
        if escape_mass_mentions:
            yield discord.utils.escape_mentions(text[start:end])
        else:
            yield text[start:end]


def merge(*iterables: Iterable[T]) -> Iterator[T]:
    """Merge multiple iterables into one.

    Parameters
    ----------
    *iterables : `iterable` of `iterable`
        The iterables to merge.
    """

    for iterable in iterables:
        yield from iterable


INVITE_REGEX = re.compile(r'(?:https?:)?discord(?:\.gg|\.com|app\.com(/invite)?)?[A-Za-z0-9]+')


def censor_invite(obj: Any, *, _regex: re.Pattern = INVITE_REGEX) -> str:
    """Censors an invite link."""
    return _regex.sub('[censored-invite]', str(obj))


def censor_object(iterable: list[int] | Any, obj: str | discord.abc.Snowflake) -> str:
    """Censors an object if it's in the iterable."""
    if not isinstance(obj, str) and obj.id in iterable:
        return '[censored]'
    return censor_invite(obj)


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


def WrapList(list_: list, length: int) -> list[list]:
    """Wrap a list into sublists of a certain length."""

    def chunks(seq: list, size: int) -> Iterator[list]:
        for i in range(0, len(seq), size):
            yield seq[i: i + size]

    return list(chunks(list_, length))


def WrapDict(dict_: dict, length: int) -> list[dict]:
    """Wrap a dict into subdicts of a certain length."""

    def chunks(seq: dict, size: int) -> Iterator[dict]:
        for i in range(0, len(seq), size):
            yield {k: seq[k] for k in list(seq)[i: i + size]}

    return list(chunks(dict_, length))


def RevDict(dict_: dict) -> dict:
    """Reverse a dict."""
    return {v: k for k, v in dict_.items()}


def SortDict(dict_: dict, key: Any = None, reverse: bool = False) -> dict:
    """Sorts a dict by a key and returns an actual dict."""
    return dict(sorted(dict_.items(), key=key, reverse=reverse))


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


async def aenumerate(asequence: AsyncIterable[T], start: int = 0) -> AsyncIterator[tuple[int, T]]:
    """Asynchronously enumerate an async iterator from a given start value"""
    n = start
    async for elem in asequence:
        yield n, elem
        n += 1


def resolve_entity_id(x: int, *, guild: discord.Guild) -> str:
    """Resolves an entity ID to a mention or a name."""
    if guild.get_role(x):
        return f'<@&{x}>'
    if guild.get_channel_or_thread(x):
        return f'<#{x}>'
    return f'<@{x}>'


def validate_snowflakes(*ids: str, guild: discord.Guild, to_obj: bool = False) -> list[int]:
    """Returns all ids that match the following conditions:

    - The id is a valid snowflake.
    - The id is a user or channel in the guild.

    Parameters
    ----------
    *ids : `str`
        The ids to validate.
    guild : `discord.Guild`
        The guild to validate the ids in.
    to_obj : `bool`
        Whether to return the object instead of the id.
        If this is set to true, it only returns the object if it's a valid Snowflake object.
    """

    def _check_id(x: Any) -> int | discord.abc.Snowflake | None:
        if not x.isdigit():
            return
        x = int(x)
        if to_obj:
            return next(filter(
                lambda v: v is not None, [guild.get_role(x), guild.get_member(x), guild.get_channel_or_thread(x)]))
        return x

    return [x for x in map(_check_id, ids) if x]


def get_asset_url(obj: discord.Guild | discord.User | discord.Member | discord.ClientUser) -> str:
    """Returns the asset URL of an available discord object."""
    if isinstance(obj, discord.Guild):
        if not obj.icon:
            return ''
        return obj.icon.url
    if isinstance(obj, (discord.Member, discord.ClientUser)) and obj.display_avatar:
        return obj.display_avatar.url
    if obj.avatar:
        return obj.display_avatar.url


def tail(f: BinaryIO, n: int = 10) -> list[bytes]:
    """Reads 'n' lines from f with buffering"""
    assert n >= 0
    pos, lines = n + 1, []

    f.seek(0, os.SEEK_END)
    isFileSmall = False

    while len(lines) <= n:
        try:
            f.seek(f.tell() - pos, os.SEEK_SET)
        except ValueError:
            f.seek(0, os.SEEK_SET)
            isFileSmall = True
        except OSError:
            print('Some problem reading/seeking the file')
            return []
        finally:
            lines = f.readlines()
            if isFileSmall:
                break

        pos *= 2

    return lines[-n:]


def format_fields(mapping: Mapping[str, Any], field_width: int | None = None) -> str:
    """Format a mapping to be readable to a human."""
    fields = sorted(mapping.items(), key=lambda item: item[0])

    if field_width is None:
        field_width = len(max(mapping.keys(), key=len))

    out = ""
    for key, val in fields:
        if isinstance(val, dict):
            inner_width = int(field_width * 1.6)
            val = '\n' + format_fields(val, field_width=inner_width)

        elif isinstance(val, str):
            text = textwrap.fill(val, width=100, replace_whitespace=False)
            val = textwrap.indent(text, ' ' * (field_width + len(': ')))
            val = val.lstrip()

        if key == 'color':
            val = hex(val)

        out += '{0:>{width}}: {1}\n'.format(key, val, width=field_width)

    return out.rstrip()


def sanitize_snowflakes(
        mapping: dict[discord.abc.Snowflake | int, T]
) -> dict[discord.abc.Snowflake | int, T]:
    return {int(k): v for k, v in mapping.items()}


def to_bool(arg: str | int) -> bool | None:
    """Converts a string into a boolean."""
    bool_map = {
        'true': True,
        'yes': True,
        'on': True,
        '1': True,
        'false': False,
        'no': False,
        'off': False,
        '0': False,
    }
    argument = str(arg).lower()
    try:
        key = bool_map[argument]
    except KeyError:
        raise ValueError(f'{arg!r} is not a recognized boolean value')
    else:
        return key


def usage_per_day(dt: datetime.datetime, usages: int) -> float:
    now = discord.utils.utcnow()

    days = (now - dt).total_seconds() / 86400
    if int(days) == 0:
        return usages
    return usages / days


def utcparse(timestring: str | None, tz: datetime.timezone = datetime.UTC) -> datetime.datetime | None:
    """Parse a timestring into a timezone aware utc datetime object."""
    if not timestring:
        return None

    parsed = dateparser.parse(timestring)

    if not parsed:
        raise ValueError(f'Could not parse `{timestring}` as a datetime object.')

    return parsed.astimezone(tz)


def merge_perms(
        overwrite: discord.PermissionOverwrite,
        permissions: discord.Permissions,
        **perms: bool
) -> None:
    """Merge permissions into an overwrite object.

    Parameters
    -----------
    overwrite: :class:`discord.PermissionOverwrite`
        The overwrite object to merge into.
    permissions: :class:`discord.Permissions`
        The permissions object to check against.
    perms: :class:`bool`
        The permissions to merge.
    """
    for perm, value in perms.items():
        if getattr(permissions, perm):
            setattr(overwrite, perm, value)


def number_suffix(number: int) -> str:
    """Returns the suffix for a number.

    Parameters
    ----------
    number : `int`
        The number to get the suffix for.
    """
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')

    return f'{number}{suffix}'


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


def medal_emoji(n: int, numerate: bool = False) -> str:
    """Returns a medal emoji based on the position."""
    LOOKUP = {
        1: '\N{FIRST PLACE MEDAL}',
        2: '\N{SECOND PLACE MEDAL}',
        3: '\N{THIRD PLACE MEDAL}',
    }
    return LOOKUP.get(n, '\N{SPORTS MEDAL}' if not numerate else f'{n}.')


def letter_emoji(index: int) -> str:
    return f'{index + 1}️⃣'


def humanize_list(li: list[Any]) -> str:
    """Takes a list and returns it joined."""
    if len(li) <= 2:
        return ' and '.join(li)

    return ', '.join(li[:-1]) + f', and {li[-1]}'


def txt(path: str | Path) -> str:
    """Reads a file and returns its content.

    Parameters
    ----------
    path : `str`
        The path to the file.

    Returns
    -------
    `str`
        The content of the file.

    Raises
    ------
    FileNotFoundError
        The file was not found.
    """
    with Path(path).open(encoding='utf-8') as file:
        return file.read()


def ProgressBar(key_min: float, key_max: float, key_current: float, key_full: int = 32) -> str:
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
    from app.utils.timetools import convert_duration

    convertable = [
        convert_duration(position if not position < 0 else 0.0),
        ProgressBar(0, length, position),
        convert_duration(length)
    ]
    return ' '.join(convertable)
