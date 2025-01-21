import functools
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

import discord
from discord.ext import commands
from discord.utils import MISSING

if TYPE_CHECKING:
    from app.core import Context
else:
    Context = commands.Context

T = TypeVar('T')
PY3: bool = sys.version_info[0] == 3


# Fuzzy matching


def check_for_equivalence(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if two strings are equivalent. If so, return 100."""
    @functools.wraps(func)
    def decorator(*args: str, **kwargs: T) -> T:
        if args[0] == args[1]:
            return 100
        return func(*args, **kwargs)
    return decorator


def check_for_none(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if either string is None. If so, return 0."""
    @functools.wraps(func)
    def decorator(*args: str, **kwargs: T) -> T:
        if args[0] is None or args[1] is None:
            return 0
        return func(*args, **kwargs)
    return decorator


def check_empty_string(func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if either string is empty. If so, return 0."""
    @functools.wraps(func)
    def decorator(*args: str, **kwargs: T) -> T:
        if len(args[0]) == 0 or len(args[1]) == 0:
            return 0
        return func(*args, **kwargs)
    return decorator


bad_chars = "".join([chr(i) for i in range(128, 256)])
if PY3:
    translation_table = {ord(c): None for c in bad_chars}
    unicode = str


def make_type_consistent(s1: str, s2: str) -> tuple[str, str]:
    """If both objects aren't, either both string or unicode instances force them to unicode"""
    if isinstance(s1, str) and isinstance(s2, str) or isinstance(s1, unicode) and isinstance(s2, unicode):
        return s1, s2

    else:
        return unicode(s1), unicode(s2)


def intr(n: float) -> int:
    """Returns a correctly rounded integer"""
    return int(round(n))


def has_manage_roles_overwrite(member: discord.Member, channel: discord.abc.GuildChannel) -> bool:
    """Check if a member has the manage_roles permission in a channel."""
    ow = channel.overwrites
    default = discord.PermissionOverwrite()
    if ow.get(member, default).manage_roles:
        return True

    return any(ow.get(role, default).manage_roles for role in member.roles)


def requires_timer() -> Callable[[T], T]:
    """Checks if the timer functionality is available."""
    async def predicate(ctx: Context) -> bool:
        if not ctx.bot.timers:
            await ctx.send_error('The timer functionality is not available.')
            return False
        return True

    return commands.check(predicate)


def can_mute() -> Callable[[T], T]:
    """Check if the author can mute someone in the current context and adds support for the :class:`ModGuildContext`."""
    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            return False

        config = await ctx.bot.db.get_guild_config(ctx.guild.id)
        if config.mute_role is None:
            await ctx.send_error('The mute role has not been set up.')
            return False
        return ctx.author.top_role > config.mute_role

    return commands.check(predicate)


# -- Music Checks --

def is_player_connected() -> Callable[[T], T]:
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not ctx.voice_client.channel:
            await ctx.send_error('I\'m not connected to a voice channel right now.')
            return False

        return True

    return commands.check(predicate)


def is_player_playing() -> Callable[[T], T]:
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not getattr(ctx.voice_client, 'playing', False):
            await ctx.send_error('I\'m not playing anything right now.')
            return False
        return True

    return commands.check(predicate)


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name="DJ")
    if role in member.roles:
        return True
    return False


def is_listen_together() -> Callable[[T], T]:
    """Checks if a listen together activity is active."""
    async def predicate(ctx: Context) -> bool:
        queue = getattr(ctx.voice_client, 'queue', None)
        if ctx.voice_client and (queue and queue.listen_together is not MISSING):
            await ctx.send_error('Please stop the listen-together activity before use this Command.')
            return False
        return True

    return commands.check(predicate)


def is_author_connected() -> Callable[[T], T]:
    """Checks if the author is connected to a Voice Channel."""
    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.user, discord.Member)

        if ctx.guild is None:
            return False

        if ctx.guild.me.voice is None:
            return True

        author_vc = ctx.user.voice and ctx.user.voice.channel
        bot_vc = ctx.guild.me.voice and ctx.guild.me.voice.channel

        if is_dj(ctx.user) and bot_vc and (not author_vc):
            return True
        if (author_vc and bot_vc) and (author_vc == bot_vc):
            if ctx.user.voice.deaf or ctx.user.voice.self_deaf:
                await ctx.send_error('You are deafened, please undeafen yourself to use this command.')
                return False
            return True
        if (not author_vc and bot_vc) or (author_vc and bot_vc):
            await ctx.send_error(f'You must be in {bot_vc.mention} to use this command.')
            return False
        if not author_vc:
            await ctx.send_error('You must be in a voice channel to use this command.')
            return False
        return True

    return commands.check(predicate)
