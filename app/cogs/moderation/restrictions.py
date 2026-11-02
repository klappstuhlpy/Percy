"""Member restrictions: voice kick/ban, nicknames (set and freeze) and per-channel mutes.

Controller only: every command runs through ``apply_member_action`` (the path the dashboard and the
case log share), the Discord work is in :mod:`.restriction_actions`, the rules in :mod:`app.services.restrictions`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

import discord

from app.cogs.moderation.infractions import default_reason, retry_expiry_timer
from app.cogs.moderation.member_actions import MemberActionError, apply_member_action
from app.core import Bot, Cog, Context, command, describe, group
from app.core.converter import ActionReason
from app.core.models import PermissionTemplate
from app.services.restrictions import is_current
from app.utils import timetools  # noqa: TC001 -- discord.py evaluates the converter annotation

from . import restriction_actions as actions

if TYPE_CHECKING:
    from app.core.timer import Timer

log = logging.getLogger(__name__)

__all__ = ('RestrictionsMixin',)


def _seconds(duration: timetools.FutureTime | None) -> int | None:
    return None if duration is None else int((duration.dt - discord.utils.utcnow()).total_seconds())


class RestrictionsMixin:
    """Voice bans, nickname control and channel mutes — mixed into :class:`Moderation`.

    Listener methods carry a ``restrictions_`` prefix with an explicit event name: ``CogMeta`` keys
    listeners by attribute name, so a bare ``on_member_join`` here would shadow (or be shadowed by)
    Moderation's own.
    """

    bot: Bot

    async def _run(self, ctx: Context, member: discord.Member, action: str, done: str, **params: Any) -> None:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        params['reason'] = params.get('reason') or default_reason(ctx.author)
        try:
            await apply_member_action(self.bot, ctx.guild, ctx.author, member.id, action=action, **params)
        except MemberActionError as e:
            await ctx.send_error(e.detail)
            return
        await ctx.send_success(done)

    # -- Voice ------------------------------------------------------------------------------

    @command(
        'voicekick',
        description='Disconnects a member from their voice channel.',
        guild_only=True,
        bot_permissions=[discord.Permissions.move_members],
        user_permissions=[discord.Permissions.move_members],
    )
    @describe(member='The member to disconnect.', reason='Why.')
    async def voicekick(
        self, ctx: Context, member: discord.Member, *, reason: Annotated[str | None, ActionReason] = None
    ) -> None:
        """Disconnects a member from voice."""
        await self._run(ctx, member, 'voicekick', f'Disconnected {member} from voice.', reason=reason)

    @command(
        'voiceban',
        description='Stops a member from joining or speaking in voice channels.',
        guild_only=True,
        bot_permissions=PermissionTemplate.roles | discord.Permissions.move_members,
        user_permissions=PermissionTemplate.roles,
    )
    @describe(member='The member to ban from voice.', duration='How long, e.g. 1h. Blank is permanent.', reason='Why.')
    async def voiceban(
        self,
        ctx: Context,
        member: discord.Member,
        duration: timetools.FutureTime | None = None,
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Gives the member a `Voice Banned` role (created on first use) and disconnects them.
        Re-applied if they rejoin the server."""
        until = f' until {discord.utils.format_dt(duration.dt, "F")}' if duration else ''
        await self._run(
            ctx, member, 'voiceban', f'Voice banned {member}{until}.', reason=reason, duration=_seconds(duration)
        )

    @command(
        'voiceunban',
        description='Lifts a voice ban.',
        guild_only=True,
        bot_permissions=PermissionTemplate.roles,
        user_permissions=PermissionTemplate.roles,
    )
    @describe(member='The member to unban from voice.', reason='Why.')
    async def voiceunban(
        self, ctx: Context, member: discord.Member, *, reason: Annotated[str | None, ActionReason] = None
    ) -> None:
        """Lifts a voice ban."""
        await self._run(ctx, member, 'voiceunban', f'Lifted the voice ban on {member}.', reason=reason)

    # -- Nicknames --------------------------------------------------------------------------

    @command(
        'nick',
        description="Changes a member's nickname; leave the name blank to reset it.",
        guild_only=True,
        bot_permissions=PermissionTemplate.nicknames,
        user_permissions=PermissionTemplate.nicknames,
    )
    @describe(member='The member.', nickname='The new nickname. Blank resets it.')
    async def nick(self, ctx: Context, member: discord.Member, *, nickname: str | None = None) -> None:
        """Changes or resets a member's nickname."""
        done = f'Changed the nickname of {member}.' if nickname and nickname.strip() else f'Reset the nickname of {member}.'
        await self._run(ctx, member, 'nick', done, nickname=nickname)

    @command(
        'nickfreeze',
        description="Sets a member's nickname and reverts any change they make.",
        guild_only=True,
        bot_permissions=PermissionTemplate.nicknames,
        user_permissions=PermissionTemplate.nicknames,
    )
    @describe(member='The member.', nickname='The nickname to lock in.')
    async def nickfreeze(self, ctx: Context, member: discord.Member, *, nickname: str) -> None:
        """Locks a member's nickname."""
        await self._run(ctx, member, 'nickfreeze', f'Froze the nickname of {member}.', nickname=nickname)

    @command(
        'nickunfreeze',
        description="Lets a member change their nickname again.",
        guild_only=True,
        user_permissions=PermissionTemplate.nicknames,
    )
    @describe(member='The member.')
    async def nickunfreeze(self, ctx: Context, member: discord.Member) -> None:
        """Unlocks a frozen nickname."""
        await self._run(ctx, member, 'nickunfreeze', f'Unfroze the nickname of {member}.')

    # -- Channel mutes ----------------------------------------------------------------------

    @command(
        'channelmute',
        description='Mutes a member in one channel only.',
        guild_only=True,
        bot_permissions=PermissionTemplate.roles,
        user_permissions=PermissionTemplate.roles | PermissionTemplate.channels,
    )
    @describe(
        member='The member to mute.',
        channel='The channel (this one by default).',
        duration='How long, e.g. 1h. Blank is permanent.',
        reason='Why.',
    )
    async def channelmute(
        self,
        ctx: Context,
        member: discord.Member,
        channel: discord.abc.GuildChannel | None = None,
        duration: timetools.FutureTime | None = None,
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Denies sending messages, reactions and speaking for one member in one channel."""
        target = channel or ctx.channel
        until = f' until {discord.utils.format_dt(duration.dt, "F")}' if duration else ''
        await self._run(
            ctx, member, 'channelmute', f'Muted {member} in {target.mention}{until}.',
            reason=reason, duration=_seconds(duration), channel_id=target.id,
        )

    @command(
        'channelunmute',
        description='Lifts a channel mute.',
        guild_only=True,
        bot_permissions=PermissionTemplate.roles,
        user_permissions=PermissionTemplate.roles | PermissionTemplate.channels,
    )
    @describe(member='The member.', channel='The channel (this one by default).')
    async def channelunmute(
        self, ctx: Context, member: discord.Member, channel: discord.abc.GuildChannel | None = None
    ) -> None:
        """Lifts a channel mute."""
        target = channel or ctx.channel
        await self._run(
            ctx, member, 'channelunmute', f'Unmuted {member} in {target.mention}.', channel_id=target.id
        )

    # -- Sticky roles -----------------------------------------------------------------------

    @group(
        'stickyrole',
        aliases=['forcerole'],
        description='Roles that are given back when a member rejoins or loses them.',
        guild_only=True,
        hybrid=False,
        user_permissions=PermissionTemplate.roles,
    )
    async def stickyrole(self, ctx: Context) -> None:
        """Sticky roles come back when a member rejoins the server or someone takes them off."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help('stickyrole')

    async def _sticky(self, ctx: Context, member: discord.Member, role: discord.Role, add: bool) -> None:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        try:
            if add:
                await actions.add_stickyrole(self.bot, ctx.guild, member, ctx.author, role)
            else:
                await actions.remove_stickyrole(self.bot, ctx.guild, member, ctx.author, role)
        except MemberActionError as e:
            await ctx.send_error(e.detail)
            return
        if add:
            await ctx.send_success(f'{role.mention} is now sticky for {member}.')
        else:
            await ctx.send_success(f'{role.mention} is no longer sticky for {member} (they keep it for now).')

    @stickyrole.command(
        'add',
        description='Makes a role sticky for a member and gives it to them.',
        bot_permissions=PermissionTemplate.roles,
        user_permissions=PermissionTemplate.roles,
    )
    @describe(member='The member.', role='The role to keep on them.')
    async def stickyrole_add(self, ctx: Context, member: discord.Member, *, role: discord.Role) -> None:
        """Gives the role and re-applies it whenever the member rejoins or loses it."""
        await self._sticky(ctx, member, role, True)

    @stickyrole.command(
        'remove',
        description='Stops re-applying a sticky role to a member.',
        user_permissions=PermissionTemplate.roles,
    )
    @describe(member='The member.', role='The role to release.')
    async def stickyrole_remove(self, ctx: Context, member: discord.Member, *, role: discord.Role) -> None:
        """Stops re-applying the role; the member keeps it until it is removed."""
        await self._sticky(ctx, member, role, False)

    @stickyrole.command(
        'list',
        description="Lists sticky roles, for one member or the whole server.",
        user_permissions=PermissionTemplate.roles,
    )
    @describe(member='Only this member.')
    async def stickyrole_list(self, ctx: Context, member: discord.Member | None = None) -> None:
        """Lists the sticky roles."""
        assert ctx.guild is not None
        rows = await self.bot.db.restrictions.list_kind(ctx.guild.id, 'stickyrole')
        lines = [
            f'<@{r["user_id"]}>: <@&{r["channel_id"]}>' for r in rows if member is None or r['user_id'] == member.id
        ]
        if not lines:
            await ctx.send_info('There are no sticky roles.')
            return
        extra = f'\n...and {len(lines) - 25} more.' if len(lines) > 25 else ''
        await ctx.send_info('\n'.join(lines[:25]) + extra, allowed_mentions=discord.AllowedMentions.none())

    # -- Listeners --------------------------------------------------------------------------

    @Cog.listener('on_member_join')
    async def restrictions_on_member_join(self, member: discord.Member) -> None:
        """Puts a rejoining member's voice ban, frozen nickname and channel mutes back."""
        try:
            await actions.reapply(self.bot, member)
        except discord.Forbidden:
            config = await self.bot.db.get_guild_config(member.guild.id)
            await config.alert_missing_permission('re-apply the restrictions of a member who rejoined')
        except discord.HTTPException:
            log.exception('Could not re-apply restrictions to %s in guild %s', member.id, member.guild.id)

    @Cog.listener('on_member_update')
    async def restrictions_on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Reverts a frozen nickname and restores a sticky role someone took off."""
        if before.nick != after.nick:
            await self._revert_nick(after)
        if len(after.roles) < len(before.roles):
            await self._restore_sticky(after)

    async def _revert_nick(self, member: discord.Member) -> None:
        row = await self.bot.db.restrictions.get(member.guild.id, member.id, 'nickfreeze')
        if row is None or row['value'] == member.nick:
            return
        try:
            await member.edit(nick=row['value'], reason='Nickname is frozen.')
        except discord.Forbidden:
            config = await self.bot.db.get_guild_config(member.guild.id)
            await config.alert_missing_permission(f'revert the frozen nickname of {member}')
        except discord.HTTPException:
            log.exception('Could not revert the frozen nickname of %s in guild %s', member.id, member.guild.id)

    async def _restore_sticky(self, member: discord.Member) -> None:
        rows = await self.bot.db.restrictions.for_member(member.guild.id, member.id)
        try:
            await actions.restore_stickyroles(member, rows, 'Sticky role was removed.')
        except discord.Forbidden:
            config = await self.bot.db.get_guild_config(member.guild.id)
            await config.alert_missing_permission(f'restore the sticky roles of {member}')
        except discord.HTTPException:
            log.exception('Could not restore the sticky roles of %s in guild %s', member.id, member.guild.id)

    @Cog.listener('on_guild_channel_create')
    async def restrictions_on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """New voice channels get the voice-ban role's denials, as the mute role gets its own."""
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return
        role = await actions.voiceban_role(self.bot, channel.guild, None, create=False)
        if role is not None:
            await actions.deny_voice(role, [channel], 'New voice channel.')

    @Cog.listener('on_guild_role_delete')
    async def restrictions_on_guild_role_delete(self, role: discord.Role) -> None:
        config = await self.bot.db.get_guild_config(role.guild.id)
        if role.id == config.voiceban_role_id:
            await config.update(voiceban_role_id=None)  # the next voice ban makes a new one
        await self.bot.db.restrictions.remove_scope(role.guild.id, 'stickyrole', role.id)

    # -- Expiry -----------------------------------------------------------------------------

    async def _expiry_target(self, timer: Timer, kind: str, channel_id: int = 0) -> discord.Guild | None:
        """The guild when ``timer`` still owns the stored restriction; ``None`` when there is nothing to do."""
        guild_id, _, member_id = timer.args[:3]
        row = await self.bot.db.restrictions.get(guild_id, member_id, kind, channel_id)
        # A retry timer was re-armed for later on purpose; only a first-time timer can be superseded.
        if row is None or (not timer.kwargs.get('attempt') and not is_current(row['expires_at'], timer.expires)):
            return None
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            await retry_expiry_timer(self.bot, timer, 'guild unavailable')
            return None
        return guild

    @Cog.listener()
    async def on_voiceban_timer_complete(self, timer: Timer) -> None:
        await self.bot.wait_until_ready()
        if (guild := await self._expiry_target(timer, 'voiceban')) is None:
            return
        member_id = timer.args[2]
        member = guild.get_member(member_id)
        if member is None:
            # Left the server: the role went with them, and a rejoin must not bring the ban back.
            await self.bot.db.restrictions.remove(guild.id, member_id, 'voiceban')
            return
        reason = f'Automatic voice unban from timer made on {timer.created}.'
        try:
            await actions.lift_voiceban(self.bot, guild, member, reason)
        except MemberActionError:
            await self.bot.db.restrictions.remove(guild.id, member_id, 'voiceban')
            return
        except discord.HTTPException as exc:
            if isinstance(exc, discord.Forbidden) and not timer.kwargs.get('attempt'):
                await (await self.bot.db.get_guild_config(guild.id)).alert_missing_permission(
                    f'lift the expired voice ban of {member}'
                )
            await retry_expiry_timer(self.bot, timer, str(exc))
            return
        self.bot.dispatch('mod_action', guild.id, 'voiceunban', member_id, guild.me.id, reason)

    @Cog.listener()
    async def on_channelmute_timer_complete(self, timer: Timer) -> None:
        await self.bot.wait_until_ready()
        channel_id = timer.args[3]
        if (guild := await self._expiry_target(timer, 'channelmute', channel_id)) is None:
            return
        member_id = timer.args[2]
        channel = guild.get_channel(channel_id)
        reason = f'Automatic channel unmute from timer made on {timer.created}.'
        if channel is not None:
            try:
                target = guild.get_member(member_id) or discord.Object(member_id)
                await actions.set_channel_denial(channel, target, False, reason)
            except discord.HTTPException as exc:
                if isinstance(exc, discord.Forbidden) and not timer.kwargs.get('attempt'):
                    await (await self.bot.db.get_guild_config(guild.id)).alert_missing_permission(
                        'lift an expired channel mute', channel=channel
                    )
                await retry_expiry_timer(self.bot, timer, str(exc))
                return
        await self.bot.db.restrictions.remove(guild.id, member_id, 'channelmute', channel_id)
        self.bot.dispatch('mod_action', guild.id, 'channelunmute', member_id, guild.me.id, reason)
