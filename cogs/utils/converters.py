import calendar
import datetime
import inspect
import os
import re
import sys
import time
from io import BufferedIOBase, BytesIO
from typing import Any, List, Iterable, Sequence, overload, Union, BinaryIO
from urllib.parse import urlparse

import aiohttp
import discord
import matplotlib as matplotlib
from discord import app_commands, Colour
from discord.ext import commands

from . import fuzzy
from ..utils.context import Context, GuildContext

MENTION_REGEX = re.compile(r"<@(!?)([0-9]*)>")
COLOUR_DICT = matplotlib.colors.CSS4_COLORS | matplotlib.colors.XKCD_COLORS

GUILD_FEATURES = {
    'ANIMATED_BANNER': ('🖼️', 'Server can upload and use an animated banner.'),
    'ANIMATED_ICON': ('🌟', 'Server can upload an animated icon.'),
    'APPLICATION_COMMAND_PERMISSIONS_V2': ('🔒', 'Server is using the new command permissions system.'),
    'AUTO_MODERATION': ('🛡️', 'Server has set up Auto Moderation.'),
    'BANNER': ('🖼️', 'Server can upload and use a banner.'),
    'COMMUNITY': ('👥', 'Server is a community server.'),
    'CREATOR_MONETIZABLE_PROVISIONAL': ('💰', 'Server is a creator server.'),
    'CREATOR_STORE_PAGE': ('🏪', 'Server has a store page.'),
    'DEVELOPER_SUPPORT_SERVER': ('👨‍💻', 'Server is a dev support server.'),
    'DISCOVERABLE': ('🔍', 'Server is discoverable.'),
    'FEATURABLE': ('🌟', 'Server is featurable.'),
    'INVITE_SPLASH': ('🌊', 'Server can upload an invite splash.'),
    'INVITES_DISABLED': ('🚫', 'Server has disabled invites.'),
    'MEMBER_VERIFICATION_GATE_ENABLED': ('✅', 'Server has enabled Membership Screening.'),
    'MONETIZATION_ENABLED': ('💰', 'Server has enabled monetization.'),
    'MORE_EMOJI': ('🔢', 'Server can upload more emojis.'),
    'MORE_STICKERS': ('🔖', 'Server can upload more stickers.'),
    'NEWS': ('📰', 'Server has set up news channels.'),
    'PARTNERED': ('🤝', 'Server is partnered.'),
    'PREVIEW_ENABLED': ('👀', 'Server has enabled preview.'),
    'ROLE_ICONS': ('👑', 'Server can set role icons.'),
    'ROLE_SUBSCRIPTIONS_AVAILABLE_FOR_PURCHASE': ('💎', 'Server has purchasable role subscriptions.'),
    'ROLE_SUBSCRIPTIONS_ENABLED': ('🔑', 'Server has enabled role subscriptions.'),
    'TICKETED_EVENTS_ENABLED': ('🎟️', 'Server has enabled ticketed events.'),
    'VANITY_URL': ('🌐', 'Server has a vanity URL.'),
    'VERIFIED': ('✔️', 'Server is verified.'),
    'VIP_REGIONS': ('🎤', 'Server has VIP voice regions.'),
    'WELCOME_SCREEN_ENABLED': ('🚪', 'Server has enabled the welcome screen.')
}

PERMISSIONS = [
    {'origin': 'connect', 'name': 'Connect', 'value': 0x100000},
    {'origin': 'mute_members', 'name': 'Mute Members', 'value': 0x400000},
    {'origin': 'move_members', 'name': 'Move Members', 'value': 0x1000000},
    {'origin': 'speak', 'name': 'Speak', 'value': 0x200000},
    {'origin': 'deafen_members', 'name': 'Deafen Members', 'value': 0x800000},
    {'origin': 'use_voice_activity', 'name': 'Use Voice Activity', 'value': 0x2000000},
    {'origin': 'go_live', 'name': 'Go Live', 'value': 0x200},
    {'origin': 'priority_speaker', 'name': 'Priority Speaker', 'value': 0x100},
    {'origin': 'request_to_speak', 'name': 'Request to Speak', 'value': 0x100000000},
    {'origin': 'administrator', 'name': 'Administrator', 'value': 0x8},
    {'origin': 'manage_roles', 'name': 'Manage Roles', 'value': 0x10000000},
    {'origin': 'kick_members', 'name': 'Kick Members', 'value': 0x2},
    {'origin': 'instant_invite', 'name': 'Create Instant Invite', 'value': 0x1},
    {'origin': 'manage_nicknames', 'name': 'Manage Nicknames', 'value': 0x8000000},
    {'origin': 'manage_server', 'name': 'Manage Server', 'value': 0x20},
    {'origin': 'manage_channels', 'name': 'Manage Channels', 'value': 0x10},
    {'origin': 'ban_members', 'name': 'Ban Members', 'value': 0x4},
    {'origin': 'change_nickname', 'name': 'Change Nickname', 'value': 0x4000000},
    {'origin': 'manage_webhooks', 'name': 'Manage Webhooks', 'value': 0x20000000},
    {'origin': 'manage_emojis', 'name': 'Manage Emojis', 'value': 0x40000000},
    {'origin': 'view_audit_log', 'name': 'View Audit Log', 'value': 0x80},
    {'origin': 'view_guild_insights', 'name': 'View Server Insights', 'value': 0x80000},
    {'origin': 'view_channel', 'name': 'View Channel', 'value': 0x400},
    {'origin': 'send_tts_messages', 'name': 'Send TTS Messages', 'value': 0x1000},
    {'origin': 'embed_links', 'name': 'Embed Links', 'value': 0x4000},
    {'origin': 'read_message_history', 'name': 'Read Message History', 'value': 0x10000},
    {'origin': 'use_external_emojis', 'name': 'Use External Emojis', 'value': 0x40000},
    {'origin': 'send_messages', 'name': 'Send Messages', 'value': 0x800},
    {'origin': 'manage_messaes', 'name': 'Manage Messages', 'value': 0x2000},
    {'origin': 'attach_files', 'name': 'Attach Files', 'value': 0x8000},
    {'origin': 'mention_everyone', 'name': 'Mention Everyone', 'value': 0x20000},
    {'origin': 'add_reactions', 'name': 'Add Reactions', 'value': 0x40},
    {'origin': 'use_slash_commands', 'name': 'Use Slash Commands', 'value': 0x80000000}
]


class NamedDict:
    def __init__(self, name: str = 'NamedDict', layer: dict = {}) -> None:  # noqa
        self.__name__ = name
        self.__dict__.update(layer)
        self.__dict__['__shape_set__'] = 'shape' in layer

    def __len__(self):
        return len(self.__dict__)

    def __repr__(self):
        return f'{self.__name__}(%s)' % ', '.join(
            ('%s=%r' % (k, v) for k, v in self.__dict__.items() if not k.startswith('_')))

    def __getattr__(self, attr):
        if attr == 'shape':
            if not self.__dict__['__shape_set__']:
                return None
        try:
            return self.__dict__[attr]
        except KeyError:
            setattr(self, attr, NamedDict())
            return self.__dict__[attr]

    def __setattr__(self, key, value):
        self.__dict__[key] = value

    def _to_dict(self, include_names: bool = False) -> dict:
        data = {}
        for k, v in self.__dict__.items():
            if isinstance(v, NamedDict):
                data[k] = v._to_dict(include_names=include_names)
            else:
                if k != '__shape_set__':
                    if k == '__name__' and not include_names:
                        continue
                    data[k] = v
        return data

    @classmethod
    def _from_dict(cls, data: dict) -> 'NamedDict':
        named = cls(name=data.pop('__name__', 'NamedDict'))
        _dict = named.__dict__
        for k, v in data.items():
            if isinstance(v, dict):
                _dict[k] = cls._from_dict(v)
            else:
                _dict[k] = v
        return named


def next_path(path: str | os.PathLike, pattern: str) -> str:
    """
    Usage:
    -------
    Finds the next free path in an sequentially named list of files
    e.g. path_pattern = `file-%s.txt`

    Examples:
    --------
    - `file-1.txt`
    - `file-2.txt`
    - `file-3.txt`
    """
    while os.path.exists(path + pattern % i):
        i = i * 2

    a, b = (i // 2, i)
    while a + 1 < b:
        c = (a + b) // 2
        a, b = (c, b) if os.path.exists(path + pattern % c) else (a, c)

    return path + pattern % b


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

    # set file pointer to end

    f.seek(0, os.SEEK_END)

    isFileSmall = False

    while len(lines) <= n:
        try:
            f.seek(f.tell() - pos, os.SEEK_SET)
        except ValueError as e:
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


def format_list(items: Iterable, seperator: str = "or", brackets: str = ""):
    new_items = []
    for i in items:
        if not re.match(MENTION_REGEX, i):
            new_items.append(f"{brackets}{i}{brackets}")
        else:
            new_items.append(str(i))

    msg = ", ".join(list(new_items)[:-1]) + f" {seperator} " + list(new_items)[-1]
    return msg


def ascii_list(items: List[str]) -> Iterable[str]:
    texts = []
    for item in items:
        if item == items[-1]:
            text = f"└─ {item}"
        else:
            text = f"├─ {item}"
        texts.append(text)

    return texts


URL_REGEX = re.compile(r"https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+")


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


_TContext = Union[Context, GuildContext]


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


async def colour_autocomplete(interaction: discord.Interaction, current: str) -> list[
    app_commands.Choice[discord.Colour]]:
    results = fuzzy.extract(current, COLOUR_DICT, limit=20)
    return [app_commands.Choice(name=f"{result[0]} ({result[2]})", value=result[2]) for result in results]


def convert_time(seconds: float):
    return datetime.timedelta(seconds=round(seconds))


def month_name_to_int(month: str) -> int:
    return {'name': num for num, name in enumerate(calendar.month_abbr) if num}.get(month[:3].title())


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


def convert_duration(seconds) -> time.time:
    return time.strftime("%H:%M:%S", time.gmtime(seconds))
