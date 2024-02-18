from __future__ import unicode_literals
import functools
import sys
from contextlib import suppress
from typing import Callable, TypeVar

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from ..utils.context import GuildContext, Context
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
        return s.translate(None, bad_chars)  # type: ignore


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
    """Check if the user has the specified permissions."""
    is_owner = await ctx.bot.is_owner(ctx.author._user)  # noqa
    if is_owner:
        return True

    if ctx.guild is None:
        return False

    resolved = ctx.author.guild_permissions
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def hybrid_user_permissions_check(**perms: bool) -> Callable[[T], T]:
    """Check if the user has the specified permissions."""
    async def pred(ctx: GuildContext):
        return await check_guild_permissions(ctx, perms)

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        app_commands.default_permissions(**perms)(func)
        return func

    return decorator


def hybrid_bot_permissions_check(**perms: bool) -> Callable[[T], T]:
    """Check if the bot has the specified permissions."""
    def decorator(func: T) -> T:
        commands.bot_has_permissions(**perms)(func)
        app_commands.checks.bot_has_permissions(**perms)(func)
        return func

    return decorator


def guilds_check(*guild_ids: int) -> Callable[[T], T]:
    """Check if the guild is in the list of guild_ids."""
    async def pred(ctx: GuildContext):
        return ctx.guild.id in guild_ids

    def decorator(func: T) -> T:
        commands.check(pred)(func)
        return func

    return decorator


def has_manage_roles_overwrite(member: discord.Member, channel: discord.abc.GuildChannel) -> bool:
    """Check if a member has the manage_roles permission in a channel."""
    ow = channel.overwrites
    default = discord.PermissionOverwrite()
    if ow.get(member, default).manage_roles:
        return True

    for role in member.roles:
        if ow.get(role, default).manage_roles:
            return True

    return False


def can_mute():
    """Check if the author can mute someone in the current context and adds support for the :class:`ModGuildContext`."""
    async def predicate(ctx: GuildContext) -> bool:
        is_owner = await ctx.bot.is_owner(ctx.author)  # noqa
        if ctx.guild is None:
            return False

        config = await ctx.cog.get_guild_config(ctx.guild.id)  # type: ignore
        role = config and config.mute_role
        if role is None:
            raise commands.BadArgument('This server does not have a mute role set up.')
        return ctx.author.top_role > role

    return commands.check(predicate)


# -- Music Checks --

def is_player_connected():
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not ctx.voice_client.channel:
            await ctx.stick(False, "I'm not connected to a voice channel right now.", ephemeral=True)
            return False

        return True

    return commands.check(predicate)


def is_player_playing():
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not ctx.voice_client.playing:  # noqa
            await ctx.stick(False, "I'm not playing anything right now.", ephemeral=True)
            return False
        return True

    return commands.check(predicate)


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name="DJ")
    if role in member.roles:
        return True
    return False


def is_listen_together():
    """Checks if a listen together activity is active."""
    async def predicate(ctx):
        if ctx.voice_client:
            if ctx.voice_client.queue.listen_together is not MISSING:
                await ctx.stick(
                    False, f'Please stop the listen-together activity before use this Command.', ephemeral=True)
                return False
        return True

    return commands.check(predicate)


def is_author_connected():
    """Checks if the author is connected to a Voice Channel."""
    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.user, discord.Member)

        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        author_vc = ctx.user.voice and ctx.user.voice.channel
        bot_vc = ctx.guild.me.voice and ctx.guild.me.voice.channel

        if is_dj(ctx.user) and bot_vc and (not author_vc):
            return True
        if (author_vc and bot_vc) and (author_vc == bot_vc):
            if ctx.user.voice.deaf or ctx.user.voice.self_deaf:
                await ctx.stick(
                    False, f'You are deafened, please undeafen yourself to use this command.', ephemeral=True)
                return False
            return True
        if (not author_vc and bot_vc) or (author_vc and bot_vc):
            await ctx.stick(False, f'You must be in {bot_vc.mention} to use this command.', ephemeral=True)
            return False
        if not author_vc:
            await ctx.stick(False, f'You must be in a voice channel to use this command.', ephemeral=True)
            return False
        return True

    return commands.check(predicate)


def isDJorAdmin():
    """Checks if the user has the DJ role or is an Admin."""
    async def predicate(ctx):
        with suppress(AttributeError, discord.Forbidden, discord.NotFound):
            djRole = discord.utils.get(ctx.guild.roles, name="DJ")

            if djRole in ctx.author.roles or ctx.author.guild_permissions.administrator:
                return True
            await ctx.stick(False, f'You need to be an Admin or DJ to use this Command.', ephemeral=True)
            return False

    return commands.check(predicate)
