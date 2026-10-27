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

T = TypeVar("T")
PY3: bool = sys.version_info[0] == 3

#: ``DJMode.dj_only`` (app/cogs/music/models.py imports this module, so it cannot be imported here).
DJ_MODE_DJ_ONLY = 1


# Fuzzy matching


def check_for_equivalence[T](func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if two strings are equivalent. If so, return 100."""

    @functools.wraps(func)
    def decorator(*args: str, **kwargs: T) -> T:
        if args[0] == args[1]:
            return 100
        return func(*args, **kwargs)

    return decorator


def check_for_none[T](func: Callable[[str, str], T]) -> Callable[[str], T]:
    """Check if either string is None. If so, return 0."""

    @functools.wraps(func)
    def decorator(*args: str, **kwargs: T) -> T:
        if args[0] is None or args[1] is None:
            return 0
        return func(*args, **kwargs)

    return decorator


def check_empty_string[T](func: Callable[[str, str], T]) -> Callable[[str], T]:
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
    if (isinstance(s1, str) and isinstance(s2, str)) or (isinstance(s1, unicode) and isinstance(s2, unicode)):
        return s1, s2

    else:
        return unicode(s1), unicode(s2)


def intr(n: float) -> int:
    """Returns a correctly rounded integer"""
    return round(n)


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
            await ctx.send_error("The timer functionality is not available.")
            return False
        return True

    return commands.check(predicate)


def hierarchy_error(
    actor: discord.Member | None,
    target: discord.Member | discord.abc.Snowflake,
    me: discord.Member,
    *,
    action: str,
    check_bot: bool = True,
) -> str | None:
    """Return why ``actor`` may not ``action`` ``target``, or ``None`` when it is allowed.

    The single hierarchy rule for every moderation path (commands, the sentinel massban
    button, automatic bans, the dashboard). ``me`` is Percy's own member in the guild.

    - Nobody may target themselves, Percy, or the server owner.
    - ``actor`` must strictly outrank ``target`` (equal top roles are refused), unless
      ``actor`` is the server owner. Pass ``actor=None`` for an automatic action.
    - Percy must strictly outrank ``target`` (skip with ``check_bot=False`` for actions
      that never touch the target's roles or membership, such as a warning).

    A ``target`` without a ``top_role`` (a bare ID, a user who is not in the server) only
    gets the identity checks: there is no hierarchy to compare.
    """
    owner_id = me.guild.owner_id
    if actor is not None and actor.id == target.id:
        return f"You cannot {action} yourself."
    if target.id == me.id:
        return f"I cannot {action} myself."
    if target.id == owner_id:
        return f"You cannot {action} the server owner."

    top_role = getattr(target, "top_role", None)
    if top_role is None:
        return None
    if actor is not None and actor.id != owner_id and actor.top_role <= top_role:
        return f"You cannot {action} a member with a role equal to or higher than yours."
    if check_bot and me.top_role <= top_role:
        return f"I cannot {action} a member with a role equal to or higher than mine."
    return None


def can_mute() -> Callable[[T], T]:
    """Check if the author can mute someone in the current context and adds support for the :class:`ModGuildContext`."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            return False

        config = await ctx.bot.db.get_guild_config(guild_id=ctx.guild.id)
        if config.mute_role is None:
            await ctx.send_error(
                "The mute role no longer exists. Set up a new one with `muterole`."
                if config.mute_role_id
                else "The mute role has not been set up."
            )
            return False
        if ctx.author.id != ctx.guild.owner_id and ctx.author.top_role <= config.mute_role:
            await ctx.send_error("Your highest role must be above the mute role to use this command.")
            return False
        return True

    return commands.check(predicate)


# -- Music Checks --


def is_player_connected() -> Callable[[T], T]:
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not ctx.voice_client.channel:
            await ctx.send_error("I'm not connected to a voice channel right now.")
            return False

        return True

    return commands.check(predicate)


def is_player_playing() -> Callable[[T], T]:
    async def predicate(ctx: Context) -> bool:
        if not ctx.guild or (ctx.guild and not ctx.guild.me.voice):
            return True

        if not ctx.voice_client or not getattr(ctx.voice_client, "playing", False):
            await ctx.send_error("I'm not playing anything right now.")
            return False
        return True

    return commands.check(predicate)


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name="DJ")
    return role in member.roles


def is_listen_together() -> Callable[[T], T]:
    """Checks if a listen together activity is active."""

    async def predicate(ctx: Context) -> bool:
        queue = getattr(ctx.voice_client, "queue", None)
        if ctx.voice_client and (queue and queue.listen_together is not MISSING):
            await ctx.send_error("Please stop the listen-together activity before use this Command.")
            return False
        return True

    return commands.check(predicate)


def is_author_connected() -> Callable[[T], T]:
    """Checks if the author is connected to a Voice Channel."""

    async def predicate(ctx: Context) -> bool:
        assert isinstance(ctx.user, discord.Member)

        if ctx.guild is None:
            return False

        has_dj = is_dj(ctx.user) or ctx.user.guild_permissions.manage_guild
        # DJ-only mode is otherwise enforced only by the player panel and the dashboard, so any
        # listener could still `skip`/`stop` through a command.
        config = await ctx.bot.db.get_guild_config(guild_id=ctx.guild.id)
        if not has_dj and getattr(config, "music_dj_mode", 0) == DJ_MODE_DJ_ONLY:
            await ctx.send_error("Only members with the **DJ** role can control the player.")
            return False

        if ctx.guild.me.voice is None:
            return True

        author_vc = ctx.user.voice and ctx.user.voice.channel
        bot_vc = ctx.guild.me.voice and ctx.guild.me.voice.channel

        if has_dj and bot_vc and (not author_vc):
            return True
        if (author_vc and bot_vc) and (author_vc == bot_vc):
            if ctx.user.voice.deaf or ctx.user.voice.self_deaf:
                await ctx.send_error("You are deafened, please undeafen yourself to use this command.")
                return False
            return True
        if (not author_vc and bot_vc) or (author_vc and bot_vc):
            await ctx.send_error(f"You must be in {bot_vc.mention} to use this command.")
            return False
        if not author_vc:
            await ctx.send_error('You must be in a voice channel to use this command.')
            return False
        return True

    return commands.check(predicate)
