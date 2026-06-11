import re
from ssl import CertificateError
from textwrap import dedent
from typing import Any, ClassVar, Literal, TypeVar

import discord
from aiohttp import ClientConnectorError
from discord import AppCommandOptionType, Member, User, app_commands
from discord.app_commands import Choice
from discord.ext import commands

from app.core.context import Context
from app.utils import Colour, fuzzy
from app.utils.constants import get_colour_dict

__all__ = (
    "ActionReason",
    "BannedMember",
    "CodeblockConverter",
    "ColorTransformer",
    "IgnoreEntity",
    "IgnoreableEntity",
    "MemberID",
)

T = TypeVar("T")


class ValidURL(commands.Converter):
    """
    Represents a valid webpage URL.

    This converter checks whether the given URL can be reached and requesting it returns a status
    code of 200. If not, `BadArgument` is raised.

    Otherwise, it simply passes through the given URL.
    """

    async def convert(self, ctx: Context, url: str) -> str:  # type: ignore
        """This converter checks whether the given URL can be reached with a status code of 200."""
        try:
            async with ctx.bot.session.get(url) as resp:
                if resp.status != 200:
                    raise commands.BadArgument(f"HTTP GET on `{url}` returned status `{resp.status}`, expected 200")
        except CertificateError:
            if url.startswith("https"):
                raise commands.BadArgument(f"Got a `CertificateError` for URL `{url}`. Does it support HTTPS?")
            raise commands.BadArgument(f"Got a `CertificateError` for URL `{url}`.")
        except ValueError:
            raise commands.BadArgument(f"`{url}` doesn't look like a valid hostname to me.")
        except ClientConnectorError:
            raise commands.BadArgument(f"Cannot connect to host with URL `{url}`.")
        return url


class CodeblockConverter(commands.Converter[list[str]]):
    """Attempts to extract code from a codeblock, if provided."""

    FORMATTED_CODE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"""
            (?P<delim>```|``)
            (?:[a-z]+\n)?          # optional language
            \s*
            (?P<code>.*?)
            \s*
            (?P=delim)
        """,
        flags=re.DOTALL | re.IGNORECASE | re.VERBOSE,
    )

    RAW_CODE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"""
            ^(?:[ \t]*\n)*
            (?P<code>.*?)
            \s*$
        """,
        flags=re.DOTALL | re.VERBOSE,
    )

    @classmethod
    async def convert(cls, ctx: Context, code: str) -> list[str]:  # type: ignore
        """Extract code from the Markdown, format it, and insert it into the code template.

        If there is any code block, ignore text outside the code block.
        Use the first code block, but prefer a fenced code block.
        If there are several fenced code blocks, concatenate only the fenced code blocks.

        Return a list of code blocks if any, otherwise return a list with a single string of code.
        """
        if match := list(cls.FORMATTED_CODE_REGEX.finditer(code)):
            blocks = [block for block in match if block.group("block")]

            if len(blocks) > 1:
                codeblocks = [block.group("code") for block in blocks]
            else:
                match = match[0] if len(blocks) == 0 else blocks[0]
                code, _block, _lang, _delim = match.group("code", "block", "lang", "delim")
                codeblocks = [dedent(code)]
        else:
            codeblocks = [dedent(cls.RAW_CODE_REGEX.fullmatch(code).group("code"))]
        return codeblocks


class ColorTransformer(commands.Converter[Colour | str], app_commands.Transformer):
    """A color converter that will try to match a color HEX or name to a :class:``discord.Color``."""

    def _convert(self, _, argument: str) -> Colour | None:
        """Converts a color HEX to the matching :class:``Colour` if possible else return None."""
        try:
            if isinstance(argument, Colour):
                return argument

            argument = argument.strip()

            if argument.startswith("#"):
                argument = argument[1:]
            elif argument.startswith("0x"):
                argument = argument[2:]
            else:
                argument = argument

            result = Colour.from_rgb(*bytes.fromhex(argument))
        except ValueError:
            results: list[tuple[str, str]] = fuzzy.finder(argument, get_colour_dict().items(), key=lambda x: x[0], limit=1)  # type: ignore
            if results:
                try:
                    result = Colour.from_rgb(*bytes.fromhex(results[0][1]))
                except (ValueError, IndexError):
                    return Colour.blurple()
                else:
                    return result
        else:
            return result

    async def transform(self, interaction: discord.Interaction, value: str) -> Colour | None:
        return self._convert(interaction, value)

    async def convert(self, ctx: Context, argument: str) -> Colour | None:
        return self._convert(ctx, argument)

    async def autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str | int | float]]:
        results = fuzzy.extract(current, get_colour_dict(), limit=20)
        return [app_commands.Choice(name=f"{result[0]} ({result[2]})", value=result[2]) for result in results]


IgnoreableEntity = discord.TextChannel | discord.VoiceChannel | discord.Thread | discord.User | discord.Role


class IgnoreEntity(commands.Converter[str]):
    async def convert(self, ctx: Context, argument: str) -> Any:
        assert ctx.current_parameter is not None
        return await commands.run_converters(ctx, IgnoreableEntity, argument, ctx.current_parameter)


def can_execute_action(ctx: Context, user: discord.Member, target: discord.Member) -> bool:
    return user.id == ctx.bot.owner_id or user == ctx.guild.owner or user.top_role > target.top_role


class MemberID(commands.Converter[discord.Member], app_commands.Transformer):
    """
    A Converter that resolves member ids by checking if the id is a valid (fetchable)
    member or pass a fake member object that takes an id parameter.
    """

    async def convert(self, ctx: Context, argument: str) -> discord.Member:
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                member_id = int(argument, base=10)
            except ValueError:
                raise commands.BadArgument(f"{argument!r} is not a valid member or member ID.") from None
            else:
                m = await ctx.bot.get_or_fetch_member(ctx.guild, member_id)
                if m is None:
                    return type("_Hackban", (), {"id": member_id, "__str__": lambda s: f"Member ID {s.id}"})()

        if not can_execute_action(ctx, ctx.author, m):  # type: ignore[arg-type]
            raise commands.BadArgument("You cannot do this action on this user due to role hierarchy.")
        return m

    @property
    def type(self) -> Literal[AppCommandOptionType.user]:
        return AppCommandOptionType.user


class BannedMember(commands.Converter[discord.BanEntry], app_commands.Transformer):
    """A Converter that resolves a member by either its id or name and checks if the member is banned."""

    async def convert(self, ctx: Context, argument: str) -> discord.BanEntry:
        if argument.isdigit():
            member_id = int(argument, base=10)
            try:
                return await ctx.guild.fetch_ban(discord.Object(id=member_id))
            except discord.NotFound:
                raise commands.BadArgument("This member has not been banned before.") from None

        entity = await discord.utils.find(lambda u: str(u.user) == argument, ctx.guild.bans(limit=None))

        if entity is None:
            raise commands.BadArgument("This member has not been banned before.")
        return entity

    async def transform(self, interaction: discord.Interaction, value: str) -> discord.BanEntry:
        if value.isdigit():
            member_id = int(value, base=10)
            try:
                return await interaction.guild.fetch_ban(discord.Object(id=member_id))
            except discord.NotFound:
                raise commands.BadArgument("This member has not been banned before.") from None

        entity = await discord.utils.find(lambda u: str(u.user) == value, interaction.guild.bans(limit=None))

        if entity is None:
            raise commands.BadArgument("This member has not been banned before.")
        return entity


class ActionReason(commands.Converter[str], app_commands.Transformer):
    """A Hybrid Command Converter that supports App and Text Commands to resolve action reasons."""

    def _convert(self, ctx: Context | discord.Interaction, argument: str) -> str:
        ret = f"{ctx.user} (ID: {ctx.user.id}): {argument}"

        if len(ret) > 512:
            reason_max = 512 - len(ret) + len(argument)
            raise commands.BadArgument(f"Reason is too long ({len(argument)}/{reason_max})")
        return ret

    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        return self._convert(interaction, value)

    async def convert(self, ctx: Context, argument: str) -> str:
        return self._convert(ctx, argument)

    @property
    def max_value(self) -> int:
        return 512


class UserConverter(commands.UserConverter):
    """A UserConverter that allows the use of 'me' to refer to the command invoker."""

    async def convert(self, ctx: Context, argument: str) -> User | Member:
        if argument.lower() == "me":
            return ctx.author
        return await super().convert(ctx, argument)


class MemberConverter(commands.MemberConverter):
    """A MemberConverter that allows the use of 'me' to refer to the command invoker."""

    async def convert(self, ctx: Context, argument: str) -> User | Member:
        if argument.lower() == 'me':
            return ctx.author
        return await super().convert(ctx, argument)
