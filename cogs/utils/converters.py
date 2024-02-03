import datetime
import inspect
import os
import sys
from io import BufferedIOBase, BytesIO
from types import ModuleType
from typing import Any, List, Iterable, Sequence, overload, BinaryIO, Optional, Union
from urllib.parse import urlparse

import aiohttp
import discord
from dateutil.parser import parse
from discord import app_commands, Colour
from discord.utils import MISSING

from cogs.utils import fuzzy
from . import commands
from .constants import IgnoreableEntity, COLOUR_DICT, _TContext, URL_REGEX
from ..utils.context import GuildContext


def get_asset_url(obj: Union[discord.Guild, discord.User, discord.Member, discord.ClientUser]) -> str:
    if isinstance(obj, discord.Guild):
        if not obj.icon:
            return ''
        return obj.icon.url
    if obj.avatar:
        return obj.avatar.url
    if isinstance(obj, (discord.Member, discord.ClientUser)):
        if obj.display_avatar:
            return obj.display_avatar.url


async def aenumerate(asequence, start=0):
    """Asynchronously enumerate an async iterator from a given start value"""
    n = start
    async for elem in asequence:
        yield n, elem
        n += 1


def tail(f: BinaryIO, n: int = 10) -> List[bytes]:
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
        except IOError:
            print('Some problem reading/seeking the file')
            sys.exit(-1)
        finally:
            lines = f.readlines()
            if isFileSmall:
                break

        pos *= 2

    return lines[-n:]


def to_bool(arg: str | int) -> Optional[bool]:
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


@overload
def group_by(items: Iterable[str]) -> Iterable[str]:
    ...


def group_by(items: Sequence[str], max_len: int = 3000) -> Iterable[str]:
    start, count = 0, 0

    for end, item in enumerate(items):
        n = len(item)
        if n + count >= max_len:
            yield '\n'.join(items[start: end])
            count = 0
            start = end
        count += n

    if count > 0:
        yield '\n'.join(items[start:])


def usage_per_day(dt: datetime.datetime, usages: int) -> float:
    now = discord.utils.utcnow()

    days = (now - dt).total_seconds() / 86400
    if int(days) == 0:
        return usages
    return usages / days


def utcparse(timestring: Optional[str]) -> Optional[datetime]:
    """Parse a timestring into a timezone aware utc datetime object."""
    if not timestring:
        return None

    parsed = parse(timestring)

    if not parsed:
        raise ValueError(f'Could not parse `{timestring}` as a datetime object.')

    return parsed.astimezone(datetime.timezone.utc)


class Snowflake(commands.Converter[int]):
    """Basically a :class:`int` converter but with an argument type error."""

    async def convert(self, ctx: _TContext, argument: str) -> int:
        try:
            return int(argument)
        except ValueError:
            param = ctx.current_parameter
            if param:
                raise commands.BadArgument(f'{param.name} argument expected a Discord ID not {argument!r}')
            raise commands.BadArgument(f'expected a Discord ID not {argument!r}')


class Prefix(commands.Converter):
    """A converter that validates bot prefixes for set."""

    async def convert(self, ctx: _TContext, argument: str) -> str:
        user_id = ctx.bot.user.id
        if argument.startswith((f'<@{user_id}>', f'<@!{user_id}>')):
            raise commands.BadArgument('That is a reserved prefix already in use.')
        if len(argument) > 150:
            raise commands.BadArgument('That prefix is too long.')
        return argument


class ColorTransformer(commands.Converter[Union[Colour, str]], app_commands.Transformer):
    """A color converter that will try to match a color HEX or name to a :class:``discord.Color``."""

    async def transform(self, interaction: discord.Interaction, value: str) -> Union[Colour, str]:
        """Transform a color HEX to the matching :class:``discord.Color` if possible else return None."""
        try:
            value = value.strip()

            if value.startswith('#'):
                value = value[1:]
            elif value.startswith('0x'):
                value = value[2:]
            else:
                value = value

            result = discord.Colour.from_rgb(*bytes.fromhex(value))
        except ValueError:
            results: list[tuple[str, str]] = fuzzy.finder(value, COLOUR_DICT.items(), key=lambda x: x[0], limit=1)
            if results:
                try:
                    result = discord.Colour.from_rgb(*bytes.fromhex(results[0][1]))
                except (ValueError, IndexError):
                    return discord.Colour.blurple()
                else:
                    return result
        else:
            return result

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[Colour]]:
        results = fuzzy.extract(current, COLOUR_DICT, limit=20)
        return [app_commands.Choice(name=f'{result[0]} ({result[2]})', value=result[2]) for result in results]

    async def convert(self, ctx: _TContext, argument: str) -> Union[str, Colour]:
        """Converts a color HEX to the matching :class:``discord.Color` if possible else return None."""

        try:
            argument = argument.strip()

            if argument.startswith('#'):
                argument = argument[1:]
            elif argument.startswith('0x'):
                argument = argument[2:]
            else:
                argument = argument

            result = discord.Colour.from_rgb(*bytes.fromhex(argument))
        except ValueError:
            results: list[tuple[str, str]] = fuzzy.finder(argument, COLOUR_DICT.items(), key=lambda x: x[0], limit=1)
            if results:
                try:
                    result = discord.Colour.from_rgb(*bytes.fromhex(results[0][1]))
                except (ValueError, IndexError):
                    return discord.Colour.blurple()
                else:
                    return result
        else:
            return result


class URLObject:
    """Represents a URL object that can read and save to a file.

    Attributes
    -----------
    url: :class:`str`
        The URL of the asset.
    filename: :class:`str`
        The filename of the asset.
    name: :class:`str`
        The name of the asset.
    """

    def __init__(self, url: str):
        if not URL_REGEX.match(url):
            raise TypeError(f'Invalid url provided')

        self.url: str = url
        self.filename: str = url.split('/')[-1]

        self.name: str = MISSING

    async def read(self, *, session=None) -> bytes:
        """Reads this asset."""
        _session = session or aiohttp.ClientSession()
        try:
            async with _session.get(self.url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 404:
                    raise discord.NotFound(resp, 'Asset not found')
                elif resp.status == 403:
                    raise discord.Forbidden(resp, 'Cannot retrieve asset')
                else:
                    raise discord.HTTPException(resp, 'Failed to get asset')
        finally:
            if not session:
                await _session.close()

    async def save(
            self,
            fp: BufferedIOBase | os.PathLike[Any],
            *,
            data: bytes = None,
            seek_begin: bool = True,
    ) -> int:
        """Saves to an object or buffer."""
        data = data or await self.read()
        if isinstance(fp, BufferedIOBase):
            written = fp.write(data)
            if seek_begin:
                fp.seek(0)
            return written

        with open(fp, 'wb') as f:
            return f.write(data)

    @property
    def spoiler(self):
        """Wether the file is a spoiler"""
        return self.name.startswith('SPOILER_')

    @spoiler.setter
    def spoiler(self, value: bool):
        if value != self.spoiler:
            if value is True:
                self.name = f'SPOILER_{self.name}'
            else:
                self.name = self.name.split('_', maxsplit=1)[1]

    async def to_file(self, *, session: aiohttp.ClientSession = None):
        return discord.File(
            BytesIO(await self.read(session=session)), self.name, spoiler=False)


class URLConverter(commands.Converter[str], app_commands.Transformer):
    """Converts a URL to a URLObject"""

    async def convert(self, ctx: _TContext, argument: str) -> str:
        parsed_url = urlparse(argument)

        if str(parsed_url.netloc).split(':')[0] in (
                '127.0.0.1',
                'localhost',
                '0.0.0.0',
        ) and not await ctx.bot.is_owner(ctx.author):
            raise commands.BadArgument('Invalid URL')

        return argument

    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        parsed_url = urlparse(value)

        if str(parsed_url.netloc).split(':')[0] in (
                '127.0.0.1',
                'localhost',
                '0.0.0.0',
        ) and not await interaction.client.is_owner(interaction.user):
            raise commands.BadArgument('Invalid URL')

        return value


class FileConverter(commands.Converter[Union[URLObject, discord.Attachment]]):
    """Converts a file to a discord.Attachment or URLObject"""

    async def convert(
            self, ctx: commands.Context | discord.Interaction, file: str = None
    ) -> Union[URLObject, discord.Attachment]:
        if file is None:
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
            elif ctx.message.reference:
                if ctx.message.reference.resolved.attachments:
                    attachment = ctx.message.reference.resolved.attachments[0]
                else:
                    raise commands.MissingRequiredArgument(
                        inspect.Parameter('file', inspect.Parameter.KEYWORD_ONLY)
                    )
            else:
                raise commands.MissingRequiredArgument(
                    inspect.Parameter('file', inspect.Parameter.KEYWORD_ONLY)
                )
        else:
            attachment = URLObject(await URLConverter().convert(ctx, file))

        return attachment


class IgnoreEntity(commands.Converter[str]):
    async def convert(self, ctx: GuildContext, argument: str):  # noqa
        assert ctx.current_parameter is not None
        return await commands.run_converters(ctx, IgnoreableEntity, argument, ctx.current_parameter)


class ModuleConverter(commands.Converter[ModuleType]):
    """A converter interface to resolve imported modules."""

    async def convert(self, ctx: commands.Context, argument: str) -> ModuleType:
        """Converts a name into a :class:`ModuleType` object."""
        argument = argument.lower().strip()
        module = sys.modules.get(argument, None)

        icon = '\N{OUTBOX TRAY}' if ctx.invoked_with == 'ml' else '\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}'

        if not module:
            raise commands.BadArgument(f'{icon}\N{WARNING SIGN} `{argument!r}` is not a valid module.')
        return module
