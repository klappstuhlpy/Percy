from __future__ import annotations

import datetime
import re
from typing import Any, Iterable, Optional, Sequence, Iterator, TypeVar, AsyncIterator, TYPE_CHECKING, Literal

import asyncpg
import discord
from discord.utils import TimestampStyle

if TYPE_CHECKING:
    from bot import Percy

T = TypeVar('T')


class MaybeAcquire:
    def __init__(self, connection: Optional[asyncpg.Connection], *, pool: asyncpg.Pool) -> None:
        self.connection: Optional[asyncpg.Connection] = connection
        self.pool: asyncpg.Pool = pool
        self._cleanup: bool = False

    async def __aenter__(self) -> asyncpg.Connection:
        if self.connection is None:
            self._cleanup = True
            self._connection = c = await self.pool.acquire()
            return c
        return self.connection

    async def __aexit__(self, *args) -> None:
        if self._cleanup:
            await self.pool.release(self._connection)


class plural:
    """A format spec which handles making words plural or singular based off of its value.

    Credit: https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/utils/formats.py#L8-L18
    """

    def __init__(self, sized: int, pass_content: bool = False):
        self.sized: int = sized
        self.pass_content: bool = pass_content

    def __format__(self, format_spec: str) -> str:
        s = self.sized
        singular, sep, plural = format_spec.partition('|')
        plural = plural or f'{singular}s'
        if self.pass_content:
            return singular if abs(s) == 1 else plural

        if abs(s) != 1:
            return f'{s} {plural}'
        return f'{s} {singular}'


async def plonk_iterator(bot: Percy, guild: discord.Guild, records: list[asyncpg.Record]) -> AsyncIterator[str]:
    for record in records:
        entity_id = record[0]
        resolved = guild.get_channel(entity_id) or await bot.get_or_fetch_member(guild, entity_id)
        if resolved is None:
            yield f'<Not Found: {entity_id}>'
        yield str(resolved)


def remove_html_tags(content: str) -> str:
    clean_text = re.sub('<.*?>', '', content)  # Remove HTML tags
    clean_text = re.sub(r'\s+', ' ', clean_text)  # Remove extra whitespace
    return clean_text


def readable_time(seconds: int | float, decimal: bool = False, short: bool = False) -> str:
    """Returns a human readable time format.

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
        "y" if short else "year": years,
        "mo" if short else "month": months,
        "d" if short else "day": days,
        "hr" if short else "hour": hours,
        "m" if short else "minute": minutes,
        "s" if short else "second": seconds,
    }

    output = []
    for unit, value in attrs.items():
        value = round(value, 2 if decimal else None)
        if value > 0:
            output.append(f"{value}{' ' * (not short)}{unit}{('s' if value != 1 else '') * (not short)}")

    return ", ".join(output)


def shorten_number(number: int | float) -> str:
    """Shortens a number to a more readable format.

    Parameters
    ----------
    number : `int` | `float`
        The number to shorten.
    """

    number = float(f"{number:.3g}")
    magnitude = 0

    while abs(number) >= 1000:
        magnitude += 1
        number /= 1000

    return f"{f'{number:f}'.rstrip('0').rstrip('.')}{['', 'K', 'M', 'B', 'T'][magnitude]}"


def pagify(
        text: str,
        delims: Sequence[str] = ["\n"],
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
    text : str
        The content to pagify and send.
    delims : `sequence` of `str`, optional
        Characters where page breaks will occur. If no delimiters are found
        in a page, the page will break after ``page_length`` characters.
        By default this only contains the newline.

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
            stop -= text.count("@here", start, stop) + text.count("@everyone", start, stop)
        closest_delim = (text.rfind(d, start + 1, stop) for d in delims)
        if priority:
            closest_delim = next((x for x in closest_delim if x > 0), -1)
        else:
            closest_delim = max(closest_delim)
        stop = closest_delim if closest_delim != -1 else stop
        if escape_mass_mentions:
            to_send = escape(text[start:stop], mass_mentions=True)
        else:
            to_send = text[start:stop]
        if len(to_send.strip()) > 0:
            yield to_send
        start = stop

    if len(text[start:end].strip()) > 0:
        if escape_mass_mentions:
            yield escape(text[start:end], mass_mentions=True)
        else:
            yield text[start:end]


def escape(text: str, *, mass_mentions: bool = False, formatting: bool = False) -> str:
    """Get text with all mass mentions or markdown escaped.

    Parameters
    ----------
    text : str
        The text to be escaped.
    mass_mentions : `bool`, optional
        Set to :code:`True` to escape mass mentions in the text.
    formatting : `bool`, optional
        Set to :code:`True` to escape any markdown formatting in the text.

    Returns
    -------
    str
        The escaped text.

    """
    if mass_mentions:
        text = text.replace("@everyone", "@\u200beveryone")
        text = text.replace("@here", "@\u200bhere")
    if formatting:
        text = discord.utils.escape_markdown(text)
    return text


def format_date(dt: Optional[datetime.datetime], style: TimestampStyle = 'f') -> str:
    if dt is None:
        return 'N/A'
    return f'{discord.utils.format_dt(dt, style)} ({discord.utils.format_dt(dt, style="R")})'


def human_join(seq: Sequence[str], delim: str = ', ', final: str = 'or') -> str:
    size = len(seq)
    if size == 0:
        return ''

    if size == 1:
        return seq[0]

    if size == 2:
        return f'{seq[0]} {final} {seq[1]}'

    return delim.join(seq[:-1]) + f' {final} {seq[-1]}'


class TabularData:
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
        """Renders a table in rST format.
        Example:
        +-------+-----+
        | Name  | Age |
        +-------+-----+
        | Alice | 24  |
        |  Bob  | 19  |
        +-------+-----+
        """

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
    if len(text) > length:
        return text[:length - 1] + "…"
    return text


def truncate_iterable(iterable: Iterable[Any], length: int, attribute: str = None) -> str:
    if len(iterable) > length:  # type: ignore
        return ", ".join(iterable[:length]) + ", …"
    return ", ".join(iterable)


def WrapList(list_: list, length: int):
    def chunks(seq, size):
        for i in range(0, len(seq), size):
            yield seq[i: i + size]

    return list(chunks(list_, length))


def get_shortened_string(length: int, start: int, string: str) -> str:
    """Shorten a string to a certain length, adding an ellipsis if it was shortened.

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
