import datetime
import inspect
import os
import re
import sys
from io import BufferedIOBase, BytesIO
from types import ModuleType
from typing import Any, List, Iterable, Sequence, overload, BinaryIO
from urllib.parse import urlparse

import aiohttp
import discord
import matplotlib as matplotlib
from discord import app_commands, Colour
from discord.ext import commands

from . import fuzzy
from .constants import IgnoreableEntity, COLOUR_DICT, _TContext, URL_REGEX
from ..utils.context import Context, GuildContext


async def aenumerate(asequence, start=0):
    """Asynchronously enumerate an async iterator from a given start value"""
    n = start
    async for elem in asequence:
        yield n, elem
        n += 1


def tail_last_line(f: BinaryIO):
    """Reads the last line of a file"""
    try:  # catch OSError in case of a one line file
        f.seek(-2, os.SEEK_END)
        while f.read(1) != b'\n':
            f.seek(-2, os.SEEK_CUR)
    except OSError:
        f.seek(0)
    last_line = f.readline().decode()
    return last_line


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
            # lines greater than file seeking size
            # seek to start
            f.seek(0, os.SEEK_SET)
            isFileSmall = True
        except IOError:
            print("Some problem reading/seeking the file")
            sys.exit(-1)
        finally:
            lines = f.readlines()
            if isFileSmall:
                break

        pos *= 2

    return lines[-n:]


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


class Snowflake:
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> int:
        try:
            return int(argument)
        except ValueError:
            param = ctx.current_parameter
            if param:
                raise commands.BadArgument(f'<:redTick:1079249771975413910> '
                                           f'{param.name} argument expected a Discord ID not {argument!r}')
            raise commands.BadArgument(f'<:redTick:1079249771975413910> expected a Discord ID not {argument!r}')


class Prefix(commands.Converter):
    async def convert(self, ctx: _TContext, argument: str) -> str:
        user_id = ctx.bot.user.id
        if argument.startswith((f'<@{user_id}>', f'<@!{user_id}>')):
            raise commands.BadArgument('<:redTick:1079249771975413910> That is a reserved prefix already in use.')
        if len(argument) > 150:
            raise commands.BadArgument('<:redTick:1079249771975413910> That prefix is too long.')
        return argument


class ColorTransformer(app_commands.Transformer):
    async def transform(self, interaction, value: str) -> Colour | str:
        """Transform a color HEX to the matching :class:``discord.Color` if possible else return None."""

        try:
            value = value.strip()

            if value.startswith("#"):
                color = value[1:]
            elif value.startswith("0x"):
                color = value[2:]
            else:
                color = value

            color = discord.Colour.from_rgb(*bytes.fromhex(color))
        except ValueError:
            try:
                color = matplotlib.colors.cnames[value.lower().replace(" ", "").replace("_", "")]
                color = discord.Colour.from_rgb(*bytes.fromhex(color[1:]))
            except KeyError:
                try:
                    color = matplotlib.XKCD_COLORS[value.lower().replace("_", "")]
                    color = discord.Colour.from_rgb(*bytes.fromhex(color[1:]))
                except KeyError:
                    color = discord.Colour.blurple()
        return color


async def colour_autocomplete(
        interaction: discord.Interaction, current: str  # noqa
) -> list[app_commands.Choice[discord.Colour]]:
    results = fuzzy.extract(current, COLOUR_DICT, limit=20)
    return [app_commands.Choice(name=f"{result[0]} ({result[2]})", value=result[2]) for result in results]


class URLObject:
    """Represents a URL object.
    This is used for downloading assets from Discord.
    """

    def __init__(self, url: str):
        if not URL_REGEX.match(url):
            raise TypeError(f"Invalid url provided")
        self.url = url
        self.filename = url.split("/")[-1]

    async def read(self, *, session=None) -> bytes:
        """Reads this asset."""
        _session = session or aiohttp.ClientSession()
        try:
            async with _session.get(self.url) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status == 404:
                    raise discord.NotFound(resp, "Asset not found")
                elif resp.status == 403:
                    raise discord.Forbidden(resp, "Cannot retrieve asset")
                else:
                    raise discord.HTTPException(resp, "Failed to get asset")
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

        with open(fp, "wb") as f:
            return f.write(data)

    @property
    def spoiler(self):
        """Wether the file is a spoiler"""
        return self.name.startswith("SPOILER_")

    @spoiler.setter
    def spoiler(self, value: bool):
        if value != self.spoiler:
            if value is True:
                self.name = f"SPOILER_{self.name}"
            else:
                self.name = self.name.split("_", maxsplit=1)[1]

    async def to_file(self, *, session: aiohttp.ClientSession = None):
        return discord.File(
            BytesIO(await self.read(session=session)), self.name, spoiler=False
        )


class URLConverter(app_commands.Transformer):
    """Converts a URL to a URLObject"""

    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        parsed_url = urlparse(value)

        if str(parsed_url.netloc).split(":")[0] in (
                "127.0.0.1",
                "localhost",
                "0.0.0.0",
        ) and not await interaction.client.is_owner(interaction.user):
            raise commands.BadArgument("<:redTick:1079249771975413910> Invalid URL")

        return value


class SpecificUserConverter(commands.Converter):
    """User Converter class that only supports IDs and mentions"""

    async def _get_user(self, bot: commands.Bot, argument: int):
        user = bot.get_user(argument)
        if user:
            return user
        return await bot.fetch_user(argument)

    async def convert(self, ctx: commands.Context, argument: str):
        is_digits = all(char.isdigit() for char in argument)

        if is_digits:
            if user := await self._get_user(ctx.bot, int(argument)):
                return user

        if match := re.match(r"<@!?([0-9]+)>", argument):
            if user := await self._get_user(ctx.bot, int(match.group(1))):
                return user

        raise commands.BadArgument("<:redTick:1079249771975413910> Failed to convert argument to user")


class FileConverter(commands.Converter):
    """Converts a file to a discord.Attachment or URLObject"""

    async def convert(
            self, ctx: commands.Context | discord.Interaction, file: str = None
    ) -> discord.Attachment | URLObject:
        if file is None:
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
            elif ctx.message.reference:
                if ctx.message.reference.resolved.attachments:
                    attachment = ctx.message.reference.resolved.attachments[0]
                else:
                    raise commands.MissingRequiredArgument(
                        inspect.Parameter("file", inspect.Parameter.KEYWORD_ONLY)
                    )
            else:
                raise commands.MissingRequiredArgument(
                    inspect.Parameter("file", inspect.Parameter.KEYWORD_ONLY)
                )
        else:
            attachment = URLObject(await URLConverter().convert(ctx, file))

        return attachment


class IgnoreEntity(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str):  # noqa
        assert ctx.current_parameter is not None
        return await commands.run_converters(ctx, IgnoreableEntity, argument, ctx.current_parameter)


class ModuleConverter(commands.Converter[ModuleType]):
    """A converter interface to resolve imported modules."""

    async def convert(self, ctx: commands.Context, argument: str) -> ModuleType:
        """Converts a name into a :class:`ModuleType` object."""
        argument = argument.lower().strip()
        module = sys.modules.get(argument, None)

        icon = "\N{OUTBOX TRAY}" if ctx.invoked_with == "ml" else "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"

        if not module:
            raise commands.BadArgument(f"{icon}\N{WARNING SIGN} `{argument!r}` is not a valid module.")
        return module


class ChannelOrMember(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str):
        try:
            return await commands.TextChannelConverter().convert(ctx, argument)
        except commands.BadArgument:
            return await commands.MemberConverter().convert(ctx, argument)
