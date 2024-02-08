from __future__ import unicode_literals
import functools
import sys
from typing import Callable, TypeVar

from discord import app_commands
from discord.ext import commands

from ..utils.context import GuildContext
from fuzzywuzzy.string_processing import StringProcessor

T = TypeVar('T')
PY3 = sys.version_info[0] == 3


# Fuzzy matching

def validate_string(s: str) -> bool:
    """Check input has length and that length > 0."""
    try:
        return len(s) > 0
    except TypeError:
        return False


def check_for_equivalence(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if two strings are equivalent. If so, return 100."""
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if args[0] == args[1]:
            return 100
        return func(*args, **kwargs)
    return decorator


def check_for_none(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if either string is None. If so, return 0."""
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if args[0] is None or args[1] is None:
            return 0
        return func(*args, **kwargs)
    return decorator


def check_empty_string(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if either string is empty. If so, return 0."""
    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if len(args[0]) == 0 or len(args[1]) == 0:
            return 0
        return func(*args, **kwargs)
    return decorator


bad_chars = str("").join([chr(i) for i in range(128, 256)])
if PY3:
    translation_table = dict((ord(c), None) for c in bad_chars)
    unicode = str


def asciionly(s: str | bytes) -> str:
    """Return an ASCII-only representation of a string"""
    if PY3:
        return s.translate(translation_table)
    else:
        return s.translate(None, bad_chars)


def asciidammit(s: str) -> str | bytes:
    """Return an ASCII-only representation of a string"""
    if type(s) is str:
        return asciionly(s)
    elif type(s) is unicode:
        return asciionly(s.encode('ascii', 'ignore'))
    else:
        return asciidammit(unicode(s))


def make_type_consistent(s1: str, s2: str) -> tuple[str, str]:
    """If both objects aren't either both string or unicode instances force them to unicode"""
    if isinstance(s1, str) and isinstance(s2, str):
        return s1, s2

    elif isinstance(s1, unicode) and isinstance(s2, unicode):
        return s1, s2

    else:
        return unicode(s1), unicode(s2)


def full_process(s: str, force_ascii=False) -> str:
    """Process string by
        -- removing all but letters and numbers
        -- trim whitespace
        -- force to lower case
        if force_ascii == True, force convert to ascii"""

    if force_ascii:
        s = asciidammit(s)
    string_out = StringProcessor.replace_non_letters_non_numbers_with_whitespace(s)
    string_out = StringProcessor.to_lower_case(string_out)
    string_out = StringProcessor.strip(string_out)
    return string_out


def intr(n: float) -> int:
    """Returns a correctly rounded integer"""
    return int(round(n))


# Discord Permission Checks


async def check_guild_permissions(ctx: GuildContext, perms: dict[str, bool], *, check=all):
    is_owner = await ctx.bot.is_owner(ctx.author._user)  # noqa
    if is_owner:
        return True

    if ctx.guild is None:
        return False

    resolved = ctx.author.guild_permissions
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def hybrid_user_permissions_check(**perms: bool) -> Callable[[T], T]:
    async def pred(ctx: GuildContext):
        return await check_guild_permissions(ctx, perms)

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        app_commands.default_permissions(**perms)(func)
        return func

    return decorator


def hybrid_bot_permissions_check(**perms: bool) -> Callable[[T], T]:

    def decorator(func: T) -> T:
        commands.bot_has_permissions(**perms)(func)
        app_commands.checks.bot_has_permissions(**perms)(func)
        return func

    return decorator


def guilds_check(*guild_ids: int) -> Callable[[T], T]:
    async def pred(ctx: GuildContext):
        return ctx.guild.id in guild_ids

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        return func

    return decorator
