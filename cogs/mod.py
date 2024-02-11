from __future__ import annotations

import asyncio
import datetime
import enum
import io
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional, Callable, Any, Union, Literal, List, TYPE_CHECKING, MutableMapping, Dict, Generic, TypeVar, \
    Sequence
from collections.abc import Hashable

import asyncpg
import discord
from PIL import Image
from captcha.image import ImageCaptcha
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from lru import LRU
from typing_extensions import Annotated

from bot import Percy
from cogs.reminder import Timer
from cogs.utils.paginator import BasePaginator
from launcher import get_logger
from cogs.utils.commands import PermissionTemplate
from .utils import timetools, cache, helpers, commands, fuzzy, checks
from .utils.context import GuildContext, ConfirmationView, tick
from .utils.converters import Snowflake, IgnoreEntity, get_asset_url, ActionReason, MemberID, BannedMember, \
    can_execute_action, combine_permissions
from .utils.formats import plural, human_join
from .utils.helpers import BaseFlags, flag_value, PostgresItem
from .utils.constants import IgnoreableEntity, Coro
from .utils.lock import lock
from .utils.queue import CancellableQueue
from .utils.timetools import ShortTime

if TYPE_CHECKING:
    class ModGuildContext(GuildContext):
        cog: Mod
        guild_config: GuildConfig

log = get_logger(__name__)

HashableT = TypeVar('HashableT', bound=Hashable)
K = TypeVar('K')
V = TypeVar('V')


def safe_reason_append(base: str, to_append: str) -> str:
    appended = f'{base} ({to_append})'
    if len(appended) > 512:
        return base
    return appended


class AutoModFlags(BaseFlags):
    @flag_value
    def audit_log(self) -> int:
        """Whether the server is broadcasting audit logs."""
        return 1

    @flag_value
    def raid(self) -> int:
        """Whether the server is auto banning spammers."""
        return 2

    @flag_value
    def leveling(self) -> int:
        """Whether to enable leveling."""
        return 4

    @flag_value
    def alerts(self) -> int:
        """Whether the server has alerts enabled."""
        return 8

    @flag_value
    def gatekeeper(self) -> int:
        """Whether the server has gatekeeper enabled."""
        return 16


# GUILD CONFIG


class GuildConfig(PostgresItem):
    """The configuration for a guild."""

    id: int
    flags: AutoModFlags

    audit_log_channel_id: Optional[int]
    audit_log_flags: Dict[str, bool]
    audit_log_webhook_url: Optional[str]

    poll_channel_id: Optional[int]
    poll_ping_role_id: Optional[int]
    poll_reason_channel_id: Optional[int]

    mention_count: Optional[int]
    safe_automod_entity_ids: set[int]
    muted_members: set[int]
    mute_role_id: Optional[int]

    alert_webhook_url: Optional[str]
    alert_channel_id: Optional[int]

    __slots__ = (
        'flags',
        'id',
        'bot',
        'audit_log_channel_id',
        'audit_log_flags',
        'audit_log_webhook_url',
        'poll_channel_id',
        'poll_ping_role_id',
        'poll_reason_channel_id',
        'mention_count',
        'safe_automod_entity_ids',
        'mute_role_id',
        'muted_members',
        'alert_webhook_url',
        'alert_channel_id',
        '_cs_audit_log_webhook',
        '_cs_alert_webhook',
    )

    def __init__(self, bot: Percy, **kwargs):
        self.bot = bot
        super().__init__(**kwargs)

        self.flags = AutoModFlags(self.flags or 0)
        self.safe_automod_entity_ids = set(self.safe_automod_entity_ids or [])
        self.muted_members = set(self.muted_members or [])

    @property
    def poll_channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(self.id)
        if guild:
            return guild.get_channel(self.poll_channel_id)

    @property
    def poll_reason_channel(self) -> Optional[discord.TextChannel]:
        guild = self.bot.get_guild(self.id)
        if guild:
            return guild.get_channel(self.poll_reason_channel_id)

    @discord.utils.cached_slot_property('_cs_audit_log_webhook')
    def audit_log_webhook(self) -> Optional[discord.Webhook]:
        if self.audit_log_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.audit_log_webhook_url, session=self.bot.session, client=self.bot)

    @discord.utils.cached_slot_property('_cs_alert_webhook')
    def alert_webhook(self) -> Optional[discord.Webhook]:
        if self.alert_webhook_url is None:
            return None
        return discord.Webhook.from_url(self.alert_webhook_url, session=self.bot.session, client=self.bot)

    @property
    def mute_role(self) -> Optional[discord.Role]:
        guild = self.bot.get_guild(self.id)
        return guild and self.mute_role_id and guild.get_role(self.mute_role_id)

    def is_muted(self, member: discord.abc.Snowflake) -> bool:
        return member.id in self.muted_members

    async def apply_mute(self, member: discord.Member, reason: Optional[str]):
        if self.mute_role_id:
            await member.add_roles(discord.Object(id=self.mute_role_id), reason=reason)

    if TYPE_CHECKING:
        send_alert = discord.Webhook.send
    else:
        async def send_alert(self, content: str = MISSING, **kwargs):
            if not self.flags.alerts or not self.alert_webhook:
                return

            if content is not MISSING:
                content = '<:discord_info:1113421814132117545> ' + content

            try:
                return await self.alert_webhook.send(content, **kwargs)
            except discord.HTTPException:
                return None


# GATEKEEPER


class GatekeeperRoleState(enum.Enum):
    """The state of a member in the gatekeeper."""
    added = 'added'
    pending_add = 'pending_add'
    pending_remove = 'pending_remove'


@dataclass
class Captcha:
    """A captcha image with the letters and the image."""
    text: str
    image: Image.Image


class Gatekeeper(PostgresItem):
    """A gatekeeper (Captcha-Verify-System) that prevents users from participating
    in the server until certain conditions are met.
    
    This is currently implemented as the user must solve a generated captcha image of six random characters.

    Attributes
    ----------
    id : int
        The ID of the guild.
    started_at : Optional[datetime.datetime]
        The time when the gatekeeper was started.
    role_id : Optional[int]
        The role ID to add to members.
    channel_id : Optional[int]
        The channel ID where the gatekeeper is active.
    message_id : Optional[int]
        The message ID that the gatekeeper is using.
    bypass_action : Literal['ban', 'kick']
        The action to take when someone bypasses the gatekeeper.
    rate : Optional[tuple[int, int]]
        The rate limit for joining the server.
    members : set[int]
        The members that have the role and are pending to be verified.
    task : asyncio.Task
        The task that adds and removes the role from members.
    queue : CancellableQueue[int, tuple[int, GatekeeperRoleState]]
        The queue that is being processed in the background.

    Behavior Overview
    ------------------
    - Gatekeeper.members
        This is a set of members that have the role and are pending to
        receive the role. Anyone in this set is technically being gatekept.
        If they talk in any channel while technically gatekept then they
        should get autobanned/autokicked.

        If the gatekeeper is disabled, then this list should be cleared,
        probably one by one during clean-up.
    - Gatekeeper.started_at is None
        This signals that the gatekeeper is fully disabled.
        If this is true, then all members should lose their role
        and the table **should not** be cleared.

        There is a special case where this is true, but there
        are still members. In this case, clean up should resume.
    - Gatekeeper.started_at is not None
        This one's simple, the gatekeeper is fully operational
        and serving captchas and adding roles.
    """
    CAPTCHA_CHARS: str = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'

    id: int
    started_at: Optional[datetime.datetime]
    channel_id: Optional[int]
    role_id: Optional[int]
    message_id: Optional[int]
    bypass_action: Literal['ban', 'kick']
    rate: Optional[tuple[int, int] | str]

    __slots__ = (
        'bot', 'cog', 'id', 'members', 'queue', 'task',
        'started_at', 'role_id', 'channel_id', 'message_id', 'bypass_action', 'rate',
    )

    def __init__(self, members: list[Any], cog: Mod, **kwargs) -> None:
        self.bot: Percy = cog.bot
        self.cog: Mod = cog
        self.members: set[int] = {r['user_id'] for r in members if r['state'] == 'added'}
        super().__init__(**kwargs)

        if self.rate is not None:
            rate, per = self.rate.split('/')
            self.rate = (int(rate), int(per))

        self.task: asyncio.Task = asyncio.create_task(self.role_loop())
        if self.started_at is not None:
            self.started_at = self.started_at.replace(tzinfo=datetime.timezone.utc)

        self.queue: CancellableQueue[int, tuple[int, GatekeeperRoleState]] = CancellableQueue()
        for member in members:
            state = GatekeeperRoleState(member['state'])
            member_id = member['user_id']
            if state is not GatekeeperRoleState.added:
                self.queue.put(member_id, (member_id, state))

    def __repr__(self) -> str:
        attrs = [
            ('id', self.id),
            ('members', len(self.members)),
            ('started_at', self.started_at),
            ('role_id', self.role_id),
            ('channel_id', self.channel_id),
            ('message_id', self.message_id),
            ('bypass_action', self.bypass_action),
            ('rate', self.rate),
        ]
        joined = ' '.join('%s=%r' % t for t in attrs)
        return f'<{self.__class__.__name__} {joined}>'

    @property
    def status(self) -> str:
        """The status of the gatekeeper."""
        headers = [
            ('Blocked Members', len(self.members)),
            ('Enabled', self.started_at is not None),
            ('Role', self.role.mention if self.role is not None else 'Not set up'),
            ('Channel', self.channel.mention if self.channel is not None else 'Not set up'),
            ('Message', self.message.jump_url if self.message is not None else 'Not set up'),
            ('Bypass Action', self.bypass_action.title()),
            ('Auto Trigger', f'{self.rate[0]}/{self.rate[1]}s' if self.rate is not None else 'Not set up'),
        ]
        return '\n'.join(f'{header}: {value}' for header, value in headers)

    def generate_captcha(self) -> Captcha:
        """Creates a new random captacha image."""
        chars: str = ''.join(random.choices(self.CAPTCHA_CHARS, k=6))
        return Captcha(
            text=chars, image=ImageCaptcha(width=300, height=100).generate_image(chars)
        )

    async def edit(
            self,
            *,
            started_at: Optional[datetime.datetime] = MISSING,
            role_id: Optional[int] = MISSING,
            channel_id: Optional[int] = MISSING,
            message_id: Optional[int] = MISSING,
            bypass_action: Literal['ban', 'kick'] = MISSING,
            rate: Optional[tuple[int, int]] = MISSING,
    ) -> None:
        """|coro|

        Edits the gatekeeper.

        Parameters
        ----------
        started_at : Optional[datetime.datetime]
            The time when the gatekeeper was started.
        role_id : Optional[int]
            The role ID to add to members.
        channel_id : Optional[int]
            The channel ID where the gatekeeper is active.
        message_id : Optional[int]
            The message ID that the gatekeeper is using.
        bypass_action : Literal['ban', 'kick']
            The action to take when someone bypasses the gatekeeper.
        rate : Optional[tuple[int, int]]
            The rate limit for joining the server.
        """
        form: dict[str, Any] = {}

        if role_id is None or channel_id is None or message_id is None:
            started_at = None
        if started_at is not MISSING:
            form['started_at'] = started_at
        if role_id is not MISSING:
            form['role_id'] = role_id
        if channel_id is not MISSING:
            form['channel_id'] = channel_id
        if message_id is not MISSING:
            form['message_id'] = message_id
        if bypass_action is not MISSING:
            form['bypass_action'] = bypass_action
        if rate is not MISSING:
            form['rate'] = '/'.join(map(str, rate)) if rate is not None else None

        async with self.bot.pool.acquire(timeout=300.0) as conn:
            async with conn.transaction():
                table_columns = ', '.join(form)
                set_values = ', '.join(f'{key} = ${index}' for index, key in enumerate(form, start=2))
                values = [self.id, *form.values()]
                values_as_str = ', '.join(f'${i}' for i in range(1, len(values) + 1))
                query = f"""
                    INSERT INTO guild_gatekeeper(id, {table_columns}) VALUES ({values_as_str})
                    ON CONFLICT(id) 
                        DO UPDATE SET {set_values};
                """
                await conn.execute(query, *values)
                if role_id is not MISSING:
                    await conn.execute("DELETE FROM guild_gatekeeper_members WHERE guild_id = $1;", self.id)

        if role_id is not MISSING:
            self.members.clear()
            self.queue.cancel_all()
            self.task.cancel()
            self.role_id = role_id
            self.task = asyncio.create_task(self.role_loop())

        if started_at is not MISSING:
            self.started_at = started_at
        if role_id is not MISSING:
            self.role_id = role_id
        if channel_id is not MISSING:
            self.channel_id = channel_id
        if message_id is not MISSING:
            self.message_id = message_id
        if bypass_action is not MISSING:
            self.bypass_action = bypass_action
        if rate is not MISSING:
            self.rate = rate

    async def role_loop(self) -> None:
        """|coro|

        The main loop that adds and removes the role from members.

        This is a bit of a weird loop because it's not really a loop.
        It's more of a queue that's being processed in the background.
        """

        while self.role_id is not None:
            member_id, action = await self.queue.get()

            try:
                if action is GatekeeperRoleState.pending_remove:
                    await self.bot.http.remove_role(
                        self.id, member_id, self.role_id, reason='Completed Gatekeeper verification')
                    query = "DELETE FROM guild_gatekeeper_members WHERE guild_id = $1 AND user_id = $2;"
                    await self.bot.pool.execute(query, self.id, member_id)
                elif action is GatekeeperRoleState.pending_add:
                    await self.bot.http.add_role(
                        self.id, member_id, self.role_id, reason='Started Gatekeeper verification')
                    query = "UPDATE guild_gatekeeper_members SET state = 'added' WHERE guild_id = $1 AND user_id = $2;"
                    await self.bot.pool.execute(query, self.id, member_id)
            except discord.DiscordServerError:
                self.queue.put(member_id, (member_id, action))
            except discord.NotFound as e:
                if e.code not in (10011, 10013):
                    break
            except Exception:  # noqa
                log.exception('[Gatekeeper] An exception happened in the role loop of guild ID %d', self.id)
                continue

    async def cleanup_loop(self, members: set[int]) -> None:
        """|coro|

        A loop that cleans up the members that are no longer in the guild.
        Potentially this could be a bit of a performance hog, but it is what it is.

        Parameters
        ----------
        members : set[int]
            The members that are currently in the guild.
        """
        if self.role_id is None:
            return

        for member_id in members:
            try:
                await self.bot.http.remove_role(self.id, member_id, self.role_id)
            except discord.HTTPException as e:
                if e.code == 10011:
                    await self.edit(role_id=None)
                    break
                elif e.code == 10013:
                    continue
                else:
                    break
            except Exception:  # noqa
                log.exception('[Gatekeeper] An exception happened in the role cleanup loop of guild ID %d', self.id)

    @property
    def pending_members(self) -> int:
        """The number of members that are pending to receive the role."""
        return len(self.members)

    async def enable(self) -> None:
        """|coro|

        Enables the gatekeeper.
        This will set the started_at field to the current time.
        """
        now = discord.utils.utcnow().replace(tzinfo=None)
        query = "UPDATE guild_gatekeeper SET started_at = $2 WHERE id = $1;"
        await self.bot.pool.execute(query, self.id, now)
        self.started_at = now

    async def disable(self) -> None:
        """|coro|

        Disables the gatekeeper.
        This will remove the role from all members and clear the queue.
        """
        self.started_at = None
        async with self.bot.pool.acquire(timeout=300.0) as conn:
            async with conn.transaction():
                query = "UPDATE guild_gatekeeper SET started_at = NULL WHERE id = $1"
                await conn.execute(query, self.id)
                query = (
                    "UPDATE guild_gatekeeper_members SET state = 'pending_remove' WHERE guild_id = $1 AND state = 'added';"
                )
                await conn.execute(query, self.id)
                for member_id in self.members:
                    self.queue.put(member_id, (member_id, GatekeeperRoleState.pending_remove))
                self.members.clear()

    @property
    def role(self) -> Optional[discord.Role]:
        """The role that is being added to members."""
        guild = self.bot.get_guild(self.id)
        return guild and self.role_id and guild.get_role(self.role_id)

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        """The channel where the gatekeeper is active."""
        guild = self.bot.get_guild(self.id)
        return guild and guild.get_channel(self.channel_id)

    @property
    def message(self) -> Optional[discord.PartialMessage]:
        """The message that the gatekeeper is using."""
        if self.channel_id is None or self.message_id is None:
            return None

        channel = self.bot.get_partial_messageable(self.channel_id)
        return channel.get_partial_message(self.message_id)

    @property
    def requires_setup(self) -> bool:
        """Whether the gatekeeper requires setup."""
        return self.role_id is None or self.channel_id is None or self.message_id is None

    def is_blocked(self, user_id: int, /) -> bool:
        """Whether the user is blocked from participating in the server."""
        return user_id in self.members

    def is_bypassing(self, member: discord.Member) -> bool:
        """Whether the member is bypassing the gatekeeper."""
        if self.started_at is None:
            return False
        if member.joined_at is None:
            return False

        return member.joined_at >= self.started_at and self.is_blocked(member.id)

    async def block(self, member: discord.Member) -> None:
        """|coro|

        Blocks the member from participating in the server.
        This will add the member to the queue and the members set.

        Parameters
        ----------
        member : discord.Member
            The member to block.
        """
        self.members.add(member.id)
        query = "INSERT INTO guild_gatekeeper_members(guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
        await self.bot.pool.execute(query, self.id, member.id)
        self.queue.put(member.id, (member.id, GatekeeperRoleState.pending_add))

    async def force_enable_with(self, members: Sequence[discord.Member]) -> None:
        """|coro|

        Forces the gatekeeper to enable with the given members.
        This will add the members to the queue and the members set.

        Parameters
        ----------
        members : Sequence[discord.Member]
            The members to block.
        """
        self.members.update(m.id for m in members)
        now = discord.utils.utcnow().replace(tzinfo=None)
        async with self.bot.pool.acquire(timeout=300.0) as conn:
            async with conn.transaction():
                query = "UPDATE guild_gatekeeper SET started_at = $2 WHERE id = $1;"
                await conn.execute(query, self.id, now)
                query = "INSERT INTO guild_gatekeeper_members(guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;"
                await conn.executemany(query, [(self.id, m.id) for m in members])

        self.started_at = now
        for member in members:
            self.queue.put(member.id, (member.id, GatekeeperRoleState.pending_add))

    async def unblock(self, member: discord.Member) -> None:
        """|coro|

        Unblocks the member from participating in the server.
        This will remove the member from the queue and the members set.

        Parameters
        ----------
        member : discord.Member
            The member to unblock.
        """
        self.members.discard(member.id)
        if self.queue.is_pending(member.id):
            query = "DELETE FROM guild_gatekeeper_members WHERE guild_id = $1 AND user_id = $2;"
            await self.bot.pool.execute(query, self.id, member.id)
            self.queue.cancel(member.id)
        else:
            query = "UPDATE guild_gatekeeper_members SET state = 'pending_remove' WHERE guild_id = $1 AND user_id = $2;"
            await self.bot.pool.execute(query, self.id, member.id)
            self.queue.put(member.id, (member.id, GatekeeperRoleState.pending_remove))


# noinspection PyUnresolvedReferences
class GatekeeperSetupRoleView(discord.ui.View):
    message: discord.Message

    def __init__(
            self, parent: GatekeeperSetUpView, selected_role: Optional[discord.Role],
            created_role: Optional[discord.Role]
    ) -> None:
        super().__init__(timeout=300.0)
        self.selected_role: Optional[discord.Role] = selected_role
        self.created_role = created_role
        self.parent = parent
        if selected_role is not None:
            self.role_select.default_values = [discord.SelectDefaultValue.from_role(selected_role)]

        if self.created_role is not None:
            self.create_role.disabled = True

    @discord.ui.select(
        cls=discord.ui.RoleSelect, min_values=1, max_values=1, placeholder='Choose the automatically assigned role'
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        assert interaction.message is not None
        assert interaction.guild is not None
        assert isinstance(interaction.channel, discord.abc.Messageable)
        assert isinstance(interaction.user, discord.Member)

        role = select.values[0]
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                f'{tick(False)} Cannot use this role as it is higher than my role in the hierarchy.', ephemeral=True)

        if role >= interaction.user.top_role:
            return await interaction.response.send_message(
                f'{tick(False)} Cannot use this role as it is higher than your role in the hierarchy.', ephemeral=True)

        channels = [ch for ch in interaction.guild.channels
                    if isinstance(ch, discord.abc.Messageable) and not ch.permissions_for(role).read_messages]

        if channels:
            embed = discord.Embed(
                title='Gatekeeper Configuration - Role',
                description=(
                    'In order for this role to work, it requires editing the permissions in every applicable channel.\n'
                    f'Would you like to edit the permissions of potentially {plural(len(channels)):channel}?'
                ),
                colour=helpers.Colour.light_grey()
            )
            confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=True)
            await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                embed = discord.Embed(
                    title='Gatekeeper Configuration - Role',
                    description=(
                        f'{tick(True)} Successfully set the automatically assigned role to {role.mention}.\n\n'
                        '\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n'
                        'Please edit the permissions of applicable channels to block the user from accessing it when possible.'
                    ),
                    colour=helpers.Colour.lime_green()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                async with interaction.channel.typing():
                    success, failure, skipped = await Mod.update_role_permissions(
                        role, self.parent.guild, interaction.user, update_read_permissions=True, channels=channels
                    )
                    total = success + failure + skipped
                    embed = discord.Embed(
                        title='Gatekeeper Configuration - Role',
                        description=(
                            f'{tick(True)} Successfully set the automatically assigned role to {role.mention}.\n\n'
                            f'Attempted to update {total} channel permissions: '
                            f'[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]'
                        ),
                        colour=helpers.Colour.lime_green()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                f'{tick(True)} Successfully set the automatically assigned role to {role.mention}', ephemeral=True)

        self.selected_role = role
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(label='Create New Role', style=discord.ButtonStyle.blurple)
    async def create_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        assert interaction.message is not None
        assert isinstance(interaction.channel, discord.abc.Messageable)

        try:
            role = await self.parent.guild.create_role(name='Unverified')
        except discord.HTTPException as e:
            return await interaction.response.send_message(f'{tick(False)} Could not create role: {e}', ephemeral=True)

        self.created_role = role
        self.selected_role = role
        channels = [ch for ch in self.parent.guild.channels if isinstance(ch, discord.abc.Messageable)]
        embed = discord.Embed(
            title='Gatekeeper Configuration - Role',
            description=(
                'In order for this role to work, it requires editing the permissions in every applicable channel.\n'
                f'Would you like to edit the permissions of potentially {plural(len(channels)):channel}?'
            ),
            colour=helpers.Colour.light_grey()
        )
        confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=False)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            embed = discord.Embed(
                title='Gatekeeper Configuration - Role',
                description=(
                    f'{tick(True)} Successfully set the automatically assigned role to {role.mention}.\n\n'
                    '\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n'
                    'Please edit the permissions of applicable channels to block the user from accessing it when possible.'
                ),
                colour=helpers.Colour.lime_green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.stop()
            await interaction.message.delete()
            return

        async with interaction.channel.typing():
            success, failure, skipped = await Mod.update_role_permissions(
                role, self.parent.guild, interaction.user, update_read_permissions=True, channels=channels
            )
            total = success + failure + skipped
            embed = discord.Embed(
                title='Gatekeeper Configuration - Role',
                description=(
                    f'{tick(True)} Successfully set the automatically assigned role to {role.mention}.\n\n'
                    f'Attempted to update {total} channel permissions: '
                    f'[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]'
                ),
                colour=helpers.Colour.lime_green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        self.stop()
        await interaction.message.delete()


class GatekeeperRateLimitModal(discord.ui.Modal, title='Join Rate Trigger'):
    rate = discord.ui.TextInput(label='Number of Joins', placeholder='5', min_length=1, max_length=3)
    per = discord.ui.TextInput(label='Number of seconds', placeholder='5', min_length=1, max_length=2)

    def __init__(self) -> None:
        super().__init__(custom_id='gatekeeper-rate-limit-modal')
        self.final_rate: Optional[tuple[int, int]] = None

    async def on_submit(self, interaction: discord.Interaction[Percy], /) -> None:
        try:
            rate = int(self.rate.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{tick(False)} Invalid number of joins given, must be a number.', ephemeral=True)

        try:
            per = int(self.per.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{tick(False)} Invalid number of seconds given, must be a number.', ephemeral=True)

        if rate <= 0 or per <= 0:
            return await interaction.response.send_message(
                f'{tick(False)} Joins and seconds cannot be negative or zero', ephemeral=True)

        self.final_rate = (rate, per)
        await interaction.response.send_message(
            f'{tick(True)} Successfully set auto trigger join rate to more than {plural(rate):member join} in {per} seconds',
            ephemeral=True,
        )


class GatekeeperMessageModal(discord.ui.Modal, title='Starter Message'):
    header = discord.ui.TextInput(
        label='Title', style=discord.TextStyle.short, max_length=256, default='Verification Required'
    )
    message = discord.ui.TextInput(label='Content', style=discord.TextStyle.long, max_length=2000)

    def __init__(self, default: str) -> None:
        super().__init__()
        self.message.default = default

    async def on_submit(self, interaction: discord.Interaction[Percy], /) -> None:
        await interaction.response.defer()
        self.stop()


class GatekeeperRateLimitConfirmationView(discord.ui.View):
    def __init__(self, *, existing_rate: tuple[int, int], author_id: int) -> None:
        super().__init__()
        self.author_id: int = author_id
        self.message: Optional[discord.Message] = None
        self.existing_rate: tuple[int, int] = existing_rate
        self.value: Optional[tuple[int, int]] = existing_rate

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        else:
            await interaction.response.send_message(
                f'{tick(False)} This confirmation dialog is not for you.', ephemeral=True)
            return False

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.delete()

    @discord.ui.button(label='Update', style=discord.ButtonStyle.green)
    async def update(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        modal = GatekeeperRateLimitModal()
        rate, per = self.existing_rate
        modal.rate.default = str(rate)
        modal.per.default = str(per)
        await interaction.response.send_modal(modal)
        await interaction.delete_original_response()
        await modal.wait()
        if modal.final_rate:
            self.value = modal.final_rate

        self.stop()

    @discord.ui.button(label='Remove', style=discord.ButtonStyle.red)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        self.value = None
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()


class GatekeeperChannelSelect(discord.ui.ChannelSelect['GatekeeperSetUpView']):
    def __init__(self, gatekeeper: Gatekeeper) -> None:
        channel = gatekeeper.channel_id
        default_values = [
            discord.SelectDefaultValue(id=channel, type=discord.SelectDefaultValueType.channel)
        ] if channel else []

        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=default_values,
            placeholder='Select a channel to force members to see when joining',
            row=0,
        )
        self.bot: Percy = gatekeeper.bot
        self.gatekeeper: Gatekeeper = gatekeeper
        self.selected_channel: Optional[discord.TextChannel] = None

    @staticmethod
    async def request_permission_sync(
            channel: discord.TextChannel, role: discord.Role, interaction: discord.Interaction):
        assert interaction.guild is not None

        role_perms = channel.permissions_for(role)
        everyone_perms = channel.permissions_for(interaction.guild.default_role)
        if not everyone_perms.read_messages and role_perms.read_messages:
            return

        embed = discord.Embed(
            title='Gatekeeper Configuration - Permission Sync',
            description=(
                f'The permissions for {channel.mention} seem to not be properly set up, would you like the bot to set it up for you?\n'
                f'The channel requires the {role.mention} role to have access to it but the @everyone role should not.'
            ),
            colour=helpers.Colour.lime_green()
        )
        confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=True)
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none(), ephemeral=True, view=confirm)
        await confirm.wait()
        if not confirm.value:
            return

        reason = f'Gatekeeper permission sync requested by {interaction.user} (ID: {interaction.user.id})'
        try:
            if everyone_perms.read_messages:
                overwrite = channel.overwrites_for(interaction.guild.default_role)
                overwrite.read_messages = False  # noqa
                await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
            if not role_perms.read_messages:
                overwrite = channel.overwrites_for(role)
                guild_perms = interaction.guild.me.guild_permissions
                combine_permissions(
                    overwrite,
                    guild_perms,
                    read_messages=True,
                    send_messages=False,
                    add_reactions=False,
                    use_application_commands=False,
                    create_private_threads=False,
                    create_public_threads=False,
                    send_messages_in_threads=False,
                )
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
        except discord.HTTPException as e:
            await interaction.followup.send(f'{tick(False)} Could not edit permissions: {e}', ephemeral=True)

    async def callback(self, interaction: discord.Interaction[Percy]) -> Any:
        assert self.view is not None
        assert interaction.message is not None
        assert interaction.guild is not None

        channel = self.values[0].resolve()
        if channel is None:
            return await interaction.response.send_message(
                f'{tick(False)} Sorry, somehow this channel did not resolve on my end.', ephemeral=True)

        assert isinstance(channel, discord.TextChannel)
        perms = channel.permissions_for(self.view.guild.me)
        if not perms.send_messages or not perms.embed_links:
            return await interaction.response.send_message(
                f'{tick(False)} Cannot send messages or embeds to this channel, please select another channel or provide those permissions',
                ephemeral=True)

        manage_roles = checks.has_manage_roles_overwrite(self.view.guild.me, channel)
        if not perms.administrator and not manage_roles:
            return await interaction.response.send_message(
                f'{tick(False)} Since I do not have Administrator permission, I require Manage Permissions permission in that channel.',
                ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        role = self.gatekeeper.role
        if role is not None:
            await self.request_permission_sync(channel, role, interaction)

        message = self.gatekeeper.message
        if message is not None:
            await message.delete()

        await self.gatekeeper.edit(channel_id=channel.id, message_id=None)
        await interaction.followup.send(
            f'{tick(True)} Successfully changed channel to {channel.mention}', ephemeral=True)
        self.view.update_state()
        await interaction.edit_original_response(view=self.view)


class GatekeeperSetUpView(discord.ui.View):
    message: discord.Message

    def __init__(self, cog: Mod, user: discord.abc.User, config: GuildConfig, gatekeeper: Gatekeeper) -> None:
        super().__init__(timeout=900.0)
        self.user = user
        self.cog = cog
        self.config = config
        self.gatekeeper = gatekeeper
        self.created_role: Optional[discord.Role] = None
        self.selected_role: Optional[discord.Role] = gatekeeper.role
        self.selected_message_id: Optional[int] = gatekeeper.message_id

        guild = gatekeeper.bot.get_guild(gatekeeper.id)
        assert guild is not None
        self.guild: discord.Guild = guild

        self.channel_select = GatekeeperChannelSelect(gatekeeper)
        self.add_item(self.channel_select)
        self.setup_bypass_action.options = [
            discord.SelectOption(
                label='Kick User',
                value='kick',
                emoji=discord.PartialEmoji(name='leave', id=1076911375026237480),
                description='Kick the member if they talk before verifying.',
            ),
            discord.SelectOption(
                label='Ban User',
                value='ban',
                emoji=discord.PartialEmoji(name='banhammer', id=1205250907160182905),
                description='Ban the member if they talk before verifying.',
            ),
        ]
        self.update_state(invalidate=False)

    def update_state(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self.cog.invalidate_gatekeeper(self.gatekeeper.id)

        role = self.gatekeeper.role
        if role is not None:
            label = f'Change Role: "{role.name}"'
            self.setup_role.label = 'Change Role' if len(label) > 80 else label
            self.setup_role.style = discord.ButtonStyle.grey
        else:
            self.setup_role.label = 'Set up Role'
            self.setup_role.style = discord.ButtonStyle.blurple

        rate = self.gatekeeper.rate
        if rate is not None:
            rate, per = rate
            self.setup_auto.label = f'Auto: {rate}/{per} seconds'
            self.setup_auto.style = discord.ButtonStyle.grey
        else:
            self.setup_auto.label = 'Auto'
            self.setup_auto.style = discord.ButtonStyle.blurple

        enabled = self.config.flags.gatekeeper and self.gatekeeper.started_at is not None
        if enabled:
            self.toggle_flag.label = 'Disable'
            self.toggle_flag.style = discord.ButtonStyle.red
        else:
            self.toggle_flag.label = 'Enable'
            self.toggle_flag.style = discord.ButtonStyle.green

        for option in self.setup_bypass_action.options:
            option.default = option.value == self.gatekeeper.bypass_action

        # Initial state before editing it
        self.setup_message.disabled = False
        self.channel_select.disabled = False
        self.setup_role.disabled = False

        channel_id = self.gatekeeper.channel_id
        if channel_id is None:
            self.setup_message.disabled = True

        if self.gatekeeper.message_id is not None:
            self.setup_message.disabled = True

        # Can't update channel/role information if it's started
        if self.gatekeeper.started_at is not None:
            self.channel_select.disabled = True
            self.setup_role.disabled = True
            self.setup_message.disabled = True

        if not enabled:
            self.toggle_flag.disabled = self.gatekeeper.requires_setup

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.user.id != interaction.user.id:
            await interaction.response.send_message(f'{tick(False)} This set up form is not for you.', ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        try:
            await self.message.delete()
        except:  # noqa
            pass

    def stop(self) -> None:
        super().stop()
        self.cog._gatekeeper_menus.pop(self.gatekeeper.id, None)  # noqa

    @discord.ui.button(label='Set up Role', style=discord.ButtonStyle.blurple, row=2)
    async def setup_role(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        assert interaction.message is not None

        if not interaction.app_permissions.manage_roles:
            return await interaction.response.send_message(
                f'{tick(False)} Bot requires Manage Roles permission for this to work.')

        view = GatekeeperSetupRoleView(self, self.selected_role, self.created_role)

        embed = discord.Embed(
            title='Gatekeeper Configuration - Role',
            description=(
                'Please either select a pre-existing role or create a new role to automatically assign to new members.'
            ),
            colour=helpers.Colour.light_grey()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await view.wait()
        self.created_role = view.created_role
        self.selected_role = view.selected_role
        if self.selected_role is not None:
            await self.gatekeeper.edit(role_id=self.selected_role.id)

            channel = self.gatekeeper.channel
            if channel is not None:
                await GatekeeperChannelSelect.request_permission_sync(channel, self.selected_role, interaction)

        self.update_state()
        await interaction.message.edit(view=self)

    @discord.ui.button(label='Send Starter Message', style=discord.ButtonStyle.blurple, row=2)
    async def setup_message(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        assert interaction.message is not None

        channel = self.gatekeeper.channel
        if self.gatekeeper.role is None:
            return await interaction.response.send_message(
                f'{tick(None)} Somehow you managed to press this while no role is set up.', ephemeral=True)

        if self.gatekeeper.message is not None:
            return await interaction.response.send_message(
                f'{tick(None)} Somehow you managed to press this while a message is already set up.', ephemeral=True)

        if channel is None:
            return await interaction.response.send_message(
                f'{tick(None)} Somehow you managed to press this while no channel is set up.', ephemeral=True)

        modal = GatekeeperMessageModal(
            'This server requires verification in order to continue participating.\n'
            '**Press the button below to verify your account.**'
        )
        await interaction.response.send_modal(modal)
        await modal.wait()

        embed = discord.Embed(
            title=modal.header.value,
            description=modal.message.value,
            colour=helpers.Colour.lime_green()
        )
        embed.set_footer(
            text='\u26a0\ufe0f This message was set up by the moderators of this server. '
                 'This bot will never ask for your personal information, nor is it related to Discord'
        )

        view = discord.ui.View(timeout=None)
        view.add_item(GatekeeperVerifyButton(self.config, self.gatekeeper))
        try:
            message = await channel.send(view=view, embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(f'{tick(False)} The message could not be sent: {e}', ephemeral=True)
        else:
            await self.gatekeeper.edit(message_id=message.id)
            await interaction.followup.send(f'{tick(True)} Starter message successfully sent', ephemeral=True)

        self.update_state()
        await interaction.message.edit(view=self)

    @discord.ui.select(placeholder='Select a bypass action...', row=1, min_values=1, max_values=1, options=[])
    async def setup_bypass_action(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.defer(ephemeral=True)
        value: Literal['ban', 'kick'] = select.values[0]  # type: ignore
        await self.gatekeeper.edit(bypass_action=value)
        await interaction.followup.send(f'{tick(True)} Successfully set bypass action to {value}', ephemeral=True)

    @discord.ui.button(label='Auto', style=discord.ButtonStyle.blurple, row=3)
    async def setup_auto(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        assert interaction.message is not None

        rate = self.gatekeeper.rate
        if rate is not None:
            view = GatekeeperRateLimitConfirmationView(existing_rate=rate, author_id=interaction.user.id)
            await interaction.response.send_message(
                f'{tick(None)} You already have auto gatekeeper set up, what would you like to do?', view=view,
                ephemeral=True
            )
            view.message = await interaction.original_response()
            await view.wait()
            await self.gatekeeper.edit(rate=view.value)
        else:
            modal = GatekeeperRateLimitModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            if modal.final_rate is not None:
                await self.gatekeeper.edit(rate=modal.final_rate)

        self.update_state()
        await interaction.message.edit(view=self)

    @discord.ui.button(label='Enable', style=discord.ButtonStyle.green, row=3)
    async def toggle_flag(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        assert interaction.message is not None

        enabled = self.gatekeeper.started_at is not None
        if enabled:
            newest = await self.cog.get_guild_gatekeeper(self.gatekeeper.id)
            if newest is not None:
                self.gatekeeper = newest

            members = self.gatekeeper.pending_members
            if members:
                confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=True)
                embed = discord.Embed(
                    title='Gatekeeper Configuration - Toggle',
                    description=(
                        f'There {plural(members):is|are!} still {plural(members):member} either waiting for their role '
                        'or still solving captcha.\n\n'
                        'Are you sure you want to remove the role from all of them? '
                        '**This has potential to be very slow and will be done in the background**'
                    ),
                    colour=helpers.Colour.light_grey()
                )
                await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
                await confirm.wait()
                if not confirm.value:
                    await interaction.followup.send('Aborting', ephemeral=True)
                    return
            else:
                await interaction.response.defer()

            await self.gatekeeper.disable()
            await interaction.followup.send(f'{tick(True)} Successfully disabled gatekeeper.')
        else:
            try:
                await self.gatekeeper.enable()
            except asyncpg.IntegrityConstraintViolationError:
                await interaction.response.send_message(
                    f'{tick(False)} Could not enable gatekeeper due to either a role or channel being unset or the message failing to send'
                )
            except Exception as e:
                await interaction.response.send_message(f'{tick(False)} Could not enable gatekeeper: {e}')
            else:
                await interaction.response.send_message(f'{tick(True)} Successfully enabled gatekeeper.')

        self.update_state()
        await interaction.message.edit(view=self)


class GatekeeperVerifyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template='gatekeeper:verify:captcha'
):
    """A dynamic button that is used to verify a user in the gatekeeper."""

    def __init__(self, config: Optional[GuildConfig], gatekeeper: Optional[Gatekeeper]) -> None:
        super().__init__(
            discord.ui.Button(label='Verify', style=discord.ButtonStyle.blurple, custom_id='gatekeeper:verify:captcha')
        )
        self.config = config
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Mod] = interaction.client.get_cog('Mod')  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Mod cog is not loaded')

        config = await cog.get_guild_config(interaction.guild_id)
        if config is None:
            return cls(None, None)
        gatekeeper = await cog.get_guild_gatekeeper(interaction.guild_id)
        return cls(config, gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.config is None or not self.config.flags.gatekeeper:
            await interaction.response.send_message(f'{tick(False)} Gatekeeper is not enabled.', ephemeral=True)
            return False

        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f'{tick(False)} Gatekeeper is not enabled.', ephemeral=True)
            return False

        if not self.gatekeeper.queue.is_pending(interaction.user.id):
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Percy]) -> Any:
        assert self.gatekeeper is not None
        assert isinstance(interaction.user, discord.Member)

        await interaction.response.defer(ephemeral=True)
        
        captcha = self.gatekeeper.generate_captcha()
        
        await interaction.channel.set_permissions(
            interaction.user,
            reason=f'Gaktekeeper User Verification (ID: {interaction.user.id})',
            send_messages=True
        )

        embed = discord.Embed(
            title='Enter the captcha',
            description='Please enter the captcha to verify yourself.',
            color=discord.Color.blurple()
        )
        embed.set_footer(text='You have 90 seconds to enter the captcha.')

        buffer = io.BytesIO()
        captcha.image.save(buffer, format='PNG')
        buffer.seek(0)
        file = discord.File(buffer, filename='captcha.png')
        embed.set_image(url='attachment://captcha.png')

        message = await interaction.followup.send(embed=embed, file=file, ephemeral=True)

        # Wait for message input from user
        try:
            msg = await interaction.client.wait_for(
                'message', check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id,
                timeout=90.0
            )
        except asyncio.TimeoutError:
            return await message.edit(
                content=f'{tick(False)} You took too long to enter the captcha, please try again.', embed=None, attachments=[])
        else:
            await msg.delete()
        finally:
            await interaction.channel.set_permissions(
                interaction.user,
                reason=f'Gaktekeeper User Verification (ID: {interaction.user.id})',
                send_messages=False
            )

        if msg.content != captcha.text:
            return await message.edit(
                content=f'{tick(False)} The captcha you entered is incorrect, please try again.', embed=None, attachments=[])

        await self.gatekeeper.unblock(interaction.user)

        await interaction.followup.send(f'{tick(True)} You have successfully verified yourself.', ephemeral=True)


class GatekeeperAlertResolveButton(discord.ui.DynamicItem[discord.ui.Button], template='gatekeeper:alert:resolve'):
    def __init__(self, gatekeeper: Optional[Gatekeeper]) -> None:
        super().__init__(
            discord.ui.Button(label='Resolve', style=discord.ButtonStyle.blurple, custom_id='gatekeeper:alert:resolve')
        )
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Mod] = interaction.client.get_cog('Mod')  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Mod cog is not loaded')

        gatekeeper = await cog.get_guild_gatekeeper(interaction.guild_id)
        return cls(gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f'{tick(False)} Gatekeeper is not enabled anymore.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Percy]) -> Any:
        assert self.gatekeeper is not None
        assert interaction.message is not None
        assert self.view is not None

        members = self.gatekeeper.pending_members
        if members:
            confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=True)
            embed = discord.Embed(
                title='Gatekeeper Configuration - Alert Resolve',
                description=(
                    f'There {plural(members):is|are!} still {plural(members):member} either waiting for their role '
                    'or still solving captcha.\n\n'
                    'Are you sure you want to remove the role from all of them? '
                    '**This has potential to be very slow and will be done in the background**'
                ),
                colour=helpers.Colour.light_grey()
            )
            await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                await interaction.followup.send('Aborting', ephemeral=True)
                return
        else:
            await interaction.response.defer()

        await self.gatekeeper.disable()
        await interaction.followup.send(f'{tick(True)} Successfully disabled gatekeeper.', ephemeral=True)
        await interaction.message.edit(view=None)


class GatekeeperAlertMassbanButton(discord.ui.DynamicItem[discord.ui.Button], template='gatekeeper:alert:massban'):
    def __init__(self, cog: Mod) -> None:
        super().__init__(
            discord.ui.Button(
                label='Ban Raiders', style=discord.ButtonStyle.red, custom_id='gatekeeper:alert:massban'
            )
        )
        self.cog: Mod = cog

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Mod] = interaction.client.get_cog('Mod')  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Mod cog is not loaded')
        return cls(cog)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:  # noqa
        if interaction.guild_id is None:
            return False

        if not interaction.app_permissions.ban_members:
            await interaction.response.send_message(f'{tick(False)} I do not have permissions to ban these members.')
            return False

        if not interaction.permissions.ban_members:
            await interaction.response.send_message(f'{tick(False)} You do not have permissions to ban these members.')
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Percy]):
        assert interaction.guild_id is not None
        assert interaction.guild is not None
        assert interaction.message is not None

        members = self.cog._spam_check[interaction.guild_id].flagged_users  # noqa
        if not members:
            return await interaction.response.send_message(f'{tick(None)} No detected raiders found at the moment.')

        now = interaction.created_at
        members = sorted(members.values(), key=lambda m: m.joined_at or now)
        fmt = "\n".join(f'{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}' for m in members)
        content = f'Current Time: {discord.utils.utcnow()}\nTotal members: {len(members)}\n{fmt}'
        file = discord.File(io.BytesIO(content.encode('utf-8')), filename='members.txt')
        confirm = ConfirmationView(timeout=180.0, author_id=interaction.user.id, delete_after=True)
        await interaction.response.send_message(
            f'This will ban the following **{plural(len(members)):member}**. Are you sure?', view=confirm, file=file
        )
        await confirm.wait()
        if not confirm.value:
            return await interaction.followup.send('Aborting.')

        count = 0
        total = len(members)
        reason = f'{interaction.user} (ID: {interaction.user.id}): Raid detected'
        guild = interaction.guild
        for member in members:
            try:
                await guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await interaction.followup.send(f'{tick(True)} Banned {count}/{total}')


# FLAGS


class MassbanFlags(commands.FlagConverter, delimiter=' ', prefix='--'):
    channel: Optional[Union[discord.TextChannel, discord.Thread, discord.VoiceChannel]] = commands.flag(
        description='The channel to search for message history', default=None
    )
    reason: Optional[str] = commands.flag(description='The reason to ban the members for', default=None)
    username: Optional[str] = commands.flag(description='The regex that usernames must match', default=None)
    created: Optional[int] = commands.flag(
        description='Matches users whose accounts were created less than specified minutes ago.', default=None
    )
    joined: Optional[int] = commands.flag(
        description='Matches users that joined less than specified minutes ago.', default=None
    )
    joined_before: Optional[discord.Member] = commands.flag(
        description='Matches users who joined before this member', default=None, name='joined_before'
    )
    joined_after: Optional[discord.Member] = commands.flag(
        description='Matches users who joined after this member', default=None, name='joined_after'
    )
    avatar: Optional[bool] = commands.flag(
        description='Matches users depending on whether they have avatars or not', default=None
    )
    roles: Optional[bool] = commands.flag(
        description='Matches users depending on whether they have roles or not', default=None
    )
    raid: bool = commands.flag(description='Matches users that are internally flagged as potential raiders',
                               default=False)
    show: bool = commands.flag(description='Show members instead of banning them', default=False)

    # Message history related flags
    contains: Optional[str] = commands.flag(description='The substring to search for in the message.', default=None)
    starts: Optional[str] = commands.flag(description='The substring to search if the message starts with.',
                                          default=None)
    ends: Optional[str] = commands.flag(description='The substring to search if the message ends with.', default=None)
    match: Optional[str] = commands.flag(description='The regex to match the message content to.', default=None)
    search: commands.Range[int, 1, 2000] = commands.flag(description='How many messages to search for', default=100)
    after: Annotated[Optional[int], Snowflake] = commands.flag(
        description='Messages must come after this message ID.', default=None
    )
    before: Annotated[Optional[int], Snowflake] = commands.flag(
        description='Messages must come before this message ID.', default=None
    )
    files: Optional[bool] = commands.flag(description='Whether the message should have attachments.', default=None)
    embeds: Optional[bool] = commands.flag(description='Whether the message should have embeds.', default=None)


class PurgeFlags(commands.FlagConverter, delimiter=' ', prefix='--'):
    user: Optional[discord.User] = commands.flag(description='Remove messages from this user', default=None)
    contains: Optional[str] = commands.flag(
        description='Remove messages that contains this string (case sensitive)', default=None
    )
    prefix: Optional[str] = commands.flag(
        description='Remove messages that start with this string (case sensitive)', default=None
    )
    suffix: Optional[str] = commands.flag(
        description='Remove messages that end with this string (case sensitive)', default=None
    )
    after: Annotated[Optional[int], Snowflake] = commands.flag(
        description='Search for messages that come after this message ID', default=None
    )
    before: Annotated[Optional[int], Snowflake] = commands.flag(
        description='Search for messages that come before this message ID', default=None
    )
    delete_pinned: bool = commands.flag(
        description='Whether to delete messages that are pinned. Defaults to True.', default=True
    )
    bot: bool = commands.flag(description='Remove messages from bots (not webhooks!)', default=False)
    webhooks: bool = commands.flag(description='Remove messages from webhooks', default=False)
    embeds: bool = commands.flag(description='Remove messages that have embeds', default=False)
    files: bool = commands.flag(description='Remove messages that have attachments', default=False)
    emoji: bool = commands.flag(description='Remove messages that have custom emoji', default=False)
    reactions: bool = commands.flag(description='Remove messages that have reactions', default=False)
    require: Literal['any', 'all'] = commands.flag(
        description='Whether any or all of the flags should be met before deleting messages. Defaults to "all"',
        default='all',
    )


# VIEWS


class PreExistingMuteRoleView(discord.ui.View):
    message: discord.Message

    def __init__(self, user: discord.abc.User):
        super().__init__(timeout=120.0)
        self.user: discord.abc.User = user
        self.merge: Optional[bool] = None

    async def on_timeout(self) -> None:
        try:
            await self.message.delete()
        except discord.HTTPException:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message('Sorry, these buttons aren\'t for you', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Merge', style=discord.ButtonStyle.blurple)
    async def merge_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = True

    @discord.ui.button(label='Replace', style=discord.ButtonStyle.grey)
    async def replace_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = False

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def abort_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        self.merge = None
        await self.message.delete()


class LockdownPermissionIssueView(discord.ui.View):
    message: discord.Message

    def __init__(self, me: discord.Member, channel: discord.abc.GuildChannel):
        super().__init__()
        self.channel: discord.abc.GuildChannel = channel
        self.me: discord.Member = me
        self.abort: bool = False

    async def on_timeout(self) -> None:
        self.abort = True
        try:
            await self.message.delete()
        except discord.HTTPException:
            pass

    @discord.ui.button(label='Resolve Permission Issue', style=discord.ButtonStyle.green)
    async def resolve_permissions(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        overwrites = self.channel.overwrites
        ow = overwrites.setdefault(self.me, discord.PermissionOverwrite())
        ow.update(send_messages=True, send_messages_in_threads=True)

        try:
            await self.channel.set_permissions(self.me, overwrite=ow)
        except discord.HTTPException:
            await interaction.response.send_message(
                f'Could not successfully edit permissions, please give the bot Send Messages '
                f'and Send Messages in Threads in {self.channel.mention}'
            )
        else:
            await self.message.delete(delay=3)
            await interaction.response.send_message('Percy permissions have been updated... continuing',
                                                    ephemeral=True)
        finally:
            self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def abort_button(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        self.abort = True
        await interaction.response.send_message(
            'Success. You can edit the permissions for the bot manually.'
        )
        self.stop()


class FlaggedMember:
    __slots__ = ('id', 'joined_at', 'display_name', 'messages')

    def __init__(self, user: discord.abc.User | discord.Member, joined_at: datetime.datetime):
        self.id = user.id
        self.display_name = str(user)
        self.joined_at = joined_at
        self.messages: int = 0

    @property
    def created_at(self) -> datetime.datetime:
        return discord.utils.snowflake_time(self.id)

    def __str__(self) -> str:
        return self.display_name


class SpamCheckerResult:
    def __init__(self, reason: str) -> None:
        self.reason: str = reason

    def __str__(self) -> str:
        return self.reason

    @classmethod
    def spammer(cls) -> SpamCheckerResult:
        return cls('Auto-ban for spamming')

    @classmethod
    def flagged_mention(cls) -> SpamCheckerResult:
        return cls('Auto-ban for suspicious mentions')


class SpammerSequence(SpamCheckerResult):
    """A sequence of spammers."""

    def __init__(self, members: Sequence[discord.abc.Snowflake], *, reason: str = 'Auto-ban for spamming') -> None:
        super().__init__(reason)
        self.members: Sequence[discord.abc.Snowflake] = members


class RateLimit(Generic[V]):
    """A rate limit implementation.

    This is a simple rate limit implementation that uses a LRU cache to store
    the last time a key was used. This is useful for things like command
    cooldowns.

    Parameters
    ----------
    rate: :class:`int`
        The number of times a key can be used.
    per: :class:`float`
        The number of seconds before the rate limit resets.
    key: :class:`Callable[[discord.Message], V]`
        A function that takes a message and returns a key.
    maxsize: :class:`int`
        The maximum size of the LRU cache.
    """

    def __init__(self, rate: int, per: float, *, key: Callable[[discord.Message], V], maxsize: int = 256) -> None:
        self.lookup = LRU(maxsize)
        self.rate = rate
        self.per = per
        self.key = key

    @property
    def ratio(self) -> float:
        return self.per / self.rate

    def is_ratelimited(self, message: discord.Message) -> bool:
        now = message.created_at
        key = self.key(message)
        tat = max(self.lookup.get(key) or now, now)
        diff = (tat - now).total_seconds()
        max_interval = self.per - self.ratio
        if diff > max_interval:
            return True

        new_tat = max(tat, now) + datetime.timedelta(seconds=self.ratio)
        self.lookup[key] = new_tat
        return False


class GatekeeperRateLimit:
    def __init__(self, rate: int, per: float) -> None:
        self.rate = rate
        self.per = per
        self.tat = discord.utils.utcnow()
        self.members: set[discord.Member] = set()

    @property
    def ratio(self) -> float:
        return self.per / self.rate

    def is_ratelimited(self, member: discord.Member) -> list[discord.Member]:
        now = member.joined_at or discord.utils.utcnow()
        tat = max(self.tat, now)
        diff = (tat - now).total_seconds()
        max_interval = self.per - self.ratio

        if self.tat < now:
            self.members.clear()

        self.members.add(member)

        if diff > max_interval:
            copy = list(self.members)
            self.members.clear()
            return copy

        new_tat = max(tat, now) + datetime.timedelta(seconds=self.ratio)
        self.tat = new_tat
        return []


class TaggedRateLimit(Generic[V, HashableT]):
    """A rate limit implementation that tags keys."""

    def __init__(
            self,
            rate: int,
            per: float,
            *,
            key: Callable[[discord.Message], V],
            tagger: Callable[[discord.Message], HashableT],
            maxsize: int = 256,
    ) -> None:
        self.lookup: MutableMapping[V, tuple[datetime.datetime, set[HashableT]]] = LRU(maxsize)  # noqa
        self.rate = rate
        self.per = per
        self.key = key
        self.tagger = tagger

    @property
    def ratio(self) -> float:
        return self.per / self.rate

    def is_ratelimited(self, message: discord.Message) -> Optional[list[HashableT]]:
        now = message.created_at
        key = self.key(message)
        value = self.lookup.get(key)
        if value is None:
            tat = now
            tagged = set()
        else:
            tat = max(value[0], now)
            tagged = value[1]

            if value[0] < now:
                tagged.clear()

        tag = self.tagger(message)
        tagged.add(tag)

        diff = (tat - now).total_seconds()
        max_interval = self.per - self.ratio
        if diff > max_interval:
            copy = list(tagged)
            tagged.clear()
            return copy

        new_tat = max(tat, now) + datetime.timedelta(seconds=self.ratio)
        self.lookup[key] = (new_tat, tagged)
        return None


class CooldownByContent(commands.CooldownMapping):
    """A cooldown mapping that uses the message content as a key."""

    def _bucket_key(self, message: discord.Message) -> tuple[int, str]:
        return message.channel.id, message.content


class MemberJoinType(enum.Enum):
    FAST = 1
    SUSPICOUS = 2


class SpamChecker:
    """This spam checker does a few things.

    1) It checks if a user has spammed more than 10 times in 12 seconds
    2) It checks if the content has been spammed 15 times in 17 seconds.
    3) It checks if new users have spammed 30 times in 35 seconds.
    4) It checks if 'fast joiners' have spammed 10 times in 12 seconds.
    5) It checks if a member spammed `config.mention_count * 2` mentions in 12 seconds.
    6) It checks if a member hits and runs 10 times in 12 seconds.

    The second case is meant to catch alternating spambots while the first one
    just catches regular singular spambots.
    From experience, these values aren't reached unless someone is actively spamming.
    """

    def __init__(self):
        self.by_content = CooldownByContent.from_cooldown(15, 17.0, commands.BucketType.member)
        self.by_user = commands.CooldownMapping.from_cooldown(10, 12.0, commands.BucketType.user)
        self.new_user = commands.CooldownMapping.from_cooldown(30, 35.0, commands.BucketType.channel)

        self.last_join: Optional[datetime.datetime] = None
        self.last_member: Optional[discord.Member] = None

        self._by_mentions: Optional[commands.CooldownMapping] = None
        self._by_mentions_rate: Optional[int] = None

        self._join_rate: Optional[tuple[int, int]] = None
        self.auto_gatekeeper: Optional[GatekeeperRateLimit] = None
        # Enabled if alerts are on but gatekeeper isn't
        self._default_join_spam = GatekeeperRateLimit(10, 5)

        self.last_created: Optional[datetime.datetime] = None

        self.flagged_users: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        self.hit_and_run = commands.CooldownMapping.from_cooldown(10, 12, commands.BucketType.channel)

    def get_flagged_member(self, user_id: int, /) -> Optional[FlaggedMember]:
        """Get a flagged member."""
        return self.flagged_users.get(user_id)

    def is_flagged(self, user_id: int, /) -> bool:
        """Check if a user is flagged."""
        return user_id in self.flagged_users

    def flag_member(self, member: discord.Member, /) -> None:
        """Flag a member."""
        self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())

    def by_mentions(self, config: GuildConfig) -> Optional[commands.CooldownMapping]:
        """Get the cooldown mapping for mentions.

        This will return a cooldown mapping for mentions if the mention count is set, otherwise None.

        Parameters
        ----------
        config: :class:`GuildConfig`
            The guild configuration to check.
        """
        if not config.mention_count:
            return None

        mention_threshold = config.mention_count
        if self._by_mentions_rate != mention_threshold:
            self._by_mentions = commands.CooldownMapping.from_cooldown(
                mention_threshold, 15, commands.BucketType.member)
            self._by_mentions_rate = mention_threshold
        return self._by_mentions

    @staticmethod
    def is_new(member: discord.Member) -> bool:
        """Check if a member is new.

        This checks if a member is new by checking if they were created less than 90 days ago and joined less than 7 days ago.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        now = discord.utils.utcnow()
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago and member.joined_at is not None and member.joined_at > seven_days_ago

    def is_spamming(self, message: discord.Message) -> Optional[SpamCheckerResult]:
        """Check if a message is spamming.

        This will return a :class:`SpamCheckerResult` if the message is spamming, otherwise None.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message to check.
        """
        if message.guild is None:
            return None

        current = message.created_at.timestamp()

        flagged = self.flagged_users.get(message.author.id)
        if flagged is not None:
            flagged.messages += 1
            bucket = self.hit_and_run.get_bucket(message)
            if bucket and bucket.update_rate_limit(current):
                return SpammerSequence(list(bucket.tagged))

            if flagged.messages <= 10 and message.raw_mentions:
                return SpamCheckerResult.flagged_mention()

        if self.is_new(message.author):
            new_bucket = self.new_user.get_bucket(message)
            if new_bucket and new_bucket.update_rate_limit(current):
                return SpamCheckerResult.spammer()

        user_bucket = self.by_user.get_bucket(message)
        if user_bucket and user_bucket.update_rate_limit(current):
            return SpamCheckerResult.spammer()

        content_bucket = self.by_content.get_bucket(message)
        if content_bucket and content_bucket.update_rate_limit(current):
            return SpamCheckerResult.spammer()

        return None

    def is_fast_join(self, member: discord.Member) -> bool:
        """Check if a member is a fast joiner.

        This will return True if the member is a fast joiner, False otherwise.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        joined = member.joined_at or discord.utils.utcnow()
        if self.last_join is None:
            self.last_join = joined
            return False
        is_fast = (joined - self.last_join).total_seconds() <= 2.0
        self.last_join = joined
        if is_fast:
            self.flagged_users[member.id] = FlaggedMember(member, joined)
        return is_fast

    def is_suspicious_join(self, member: discord.Member) -> bool:
        """Check if a member is suspicious.

        This will return True if the member is suspicious, False otherwise.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        created = member.created_at
        if self.last_created is None:
            self.last_created = created
            return False

        is_suspicious = abs((created - self.last_created).total_seconds()) <= 86400.0
        self.last_created = created
        if is_suspicious:
            self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())
        return is_suspicious

    def get_join_type(self, member: discord.Member) -> Optional[MemberJoinType]:
        """Get the join type of a member.

        This will return the join type of a member, if any.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        """
        joined = member.joined_at or discord.utils.utcnow()

        if self.last_member is None:
            self.last_member = member
            self.last_join = joined
            return None

        if self.last_join is not None:
            is_fast = (joined - self.last_join).total_seconds() <= 2.0
            self.last_join = joined
            if is_fast:
                self.flagged_users[member.id] = FlaggedMember(member, joined)
                if self.last_member.id not in self.flagged_users:
                    self.flag_member(self.last_member)
                return MemberJoinType.FAST

        is_suspicious = abs((member.created_at - self.last_member.created_at).total_seconds()) <= 86400.0
        if is_suspicious:
            self.flagged_users[member.id] = FlaggedMember(member, joined)
            if self.last_member.id not in self.flagged_users:
                self.flag_member(self.last_member)
            return MemberJoinType.SUSPICOUS

        return None

    def is_mention_spam(self, message: discord.Message, config: GuildConfig) -> bool:
        """Check if a message is mention spam.

        This will return True if the message is mention spam, False otherwise.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message to check.
        config: :class:`GuildConfig`
            The guild configuration to check against.
        """
        mapping = self.by_mentions(config)
        if mapping is None:
            return False

        current = message.created_at.timestamp()
        mention_bucket = mapping.get_bucket(message, current)
        mention_count = sum(not m.bot and m.id != message.author.id for m in message.mentions)
        return mention_bucket is not None and mention_bucket.update_rate_limit(
            current, tokens=mention_count) is not None

    def check_gatekeeper(self, member: discord.Member, gatekeeper: Gatekeeper) -> list[discord.Member]:
        """Check if a member is ratelimited by the gatekeeper.

        This will return a list of members that are ratelimited.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member to check.
        gatekeeper: :class:`Gatekeeper
            The gatekeeper to check against.
        """
        if gatekeeper.started_at is not None:
            return []

        rate = gatekeeper.rate
        if rate is None:
            self._join_rate = None
            return []

        if rate != self._join_rate:
            # Might be worth considering swapping over the tat/member list? Probably complicated though
            self.auto_gatekeeper = GatekeeperRateLimit(rate[0], rate[1])
            self._join_rate = rate

        if self.auto_gatekeeper is not None:
            return self.auto_gatekeeper.is_ratelimited(member)

        return []

    def is_alertable_join_spam(self, member: discord.Member) -> list[discord.Member]:
        """Check if a member is ratelimited by the join spam checker."""
        if self.auto_gatekeeper is not None:
            return []

        return self._default_join_spam.is_ratelimited(member)

    def remove_member(self, user: discord.abc.User) -> None:
        """Remove a member from the spam checker."""
        self.flagged_users.pop(user.id, None)


class LockdownTimer(Timer):
    """A timer for a lockdown event."""
    pass


class Mod(commands.Cog):
    """Utility commands for moderation."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self._spam_check: defaultdict[int, SpamChecker] = defaultdict(SpamChecker)

        self._mute_data_batch: defaultdict[int, list[tuple[int, Any]]] = defaultdict(list)
        self.batch_updates.add_exception_type(asyncpg.PostgresConnectionError)
        self.batch_updates.start()

        self.message_batches: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
        self.bulk_send_messages.start()

        self._gatekeeper_menus: dict[int, GatekeeperSetUpView] = {}
        self._gatekeepers: dict[int, Gatekeeper] = {}

        bot.add_dynamic_items(GatekeeperVerifyButton, GatekeeperAlertMassbanButton, GatekeeperAlertResolveButton)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='alumni_mod_animated', id=1076913120599080970, animated=True)

    def cog_unload(self) -> None:
        self.batch_updates.stop()
        self.bulk_send_messages.stop()

    async def bot_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True

        full_bypass = ctx.permissions.manage_guild or await self.bot.is_owner(ctx.author)
        if full_bypass:
            return True

        guild_id = ctx.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None or not config.flags.value:
            return True

        checker = self._spam_check[guild_id]
        return not checker.is_flagged(ctx.author.id)

    @lock('Moderation', 'mute_batch', wait=True)
    async def bulk_insert(self):
        query = """
            UPDATE guild_config
                SET muted_members = x.result_array
            FROM jsonb_to_recordset($1::jsonb) AS x(guild_id BIGINT, result_array BIGINT[])
            WHERE guild_config.id = x.guild_id;
        """

        if not self._mute_data_batch:
            return

        final_data = []
        for guild_id, data in self._mute_data_batch.items():
            config = await self.get_guild_config(guild_id)

            if config is None:
                continue

            as_set: set[int] = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({'guild_id': guild_id, 'result_array': list(as_set)})
            self.get_guild_config.invalidate(self, guild_id)

        await self.bot.pool.execute(query, final_data)
        self._mute_data_batch.clear()

    @tasks.loop(seconds=15.0)
    async def batch_updates(self):
        await self.bulk_insert()

    @tasks.loop(seconds=10.0)
    @lock('Moderation', 'message_batch', wait=True)
    async def bulk_send_messages(self):
        """|coro|

        Bulk send messages to the guilds used for broadcasting.
        This is done to avoid rate limits.
        """
        for ((guild_id, channel_id), messages) in self.message_batches.items():
            guild = self.bot.get_guild(guild_id)
            channel: Optional[discord.abc.Messageable] = guild and guild.get_channel(channel_id)  # type: ignore
            if channel is None:
                continue

            paginator = commands.Paginator(suffix='', prefix='')
            for message in messages:
                paginator.add_line(message)

            for page in paginator.pages:
                try:
                    await channel.send(page)
                except discord.HTTPException:
                    pass

            self.message_batches.clear()

    @cache.cache()
    async def get_guild_config(self, guild_id: int) -> Optional[GuildConfig]:
        """|coro| @cached

        Get the guild config from the database.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the config from.

        Returns
        -------
        Optional[:class:`GuildConfig`]
            The guild config if it exists, else ``None``.
        """
        query = "SELECT * FROM guild_config WHERE id=$1;"
        async with self.bot.pool.acquire(timeout=300.0) as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                return GuildConfig(self.bot, record=record)
            return None

    async def get_guild_gatekeeper(self, guild_id: Optional[int]) -> Optional[Gatekeeper]:
        """|coro|

        Get the gatekeeper for the guild.

        Parameters
        ----------
        guild_id: Optional[:class:`int`]
            The guild ID to get the gatekeeper from.

        Returns
        -------
        Optional[:class:`Gatekeeper`]
            The gatekeeper if it exists, else ``None``.
        """
        if guild_id is None:
            return None

        cached = self._gatekeepers.get(guild_id)
        if cached is not None:
            return cached

        query = """SELECT * FROM guild_gatekeeper WHERE id=$1;"""
        async with self.bot.pool.acquire(timeout=300.0) as con:
            record = await con.fetchrow(query, guild_id)
            if record is not None:
                query = """SELECT * FROM guild_gatekeeper_members WHERE guild_id=$1"""
                members = await con.fetch(query, guild_id)
                self._gatekeepers[guild_id] = gatekeeper = Gatekeeper(members, self, record=record)
                return gatekeeper
            return None

    def invalidate_gatekeeper(self, guild_id: int) -> None:
        previous = self._gatekeepers.pop(guild_id, None)
        if previous is not None:
            previous.task.cancel()

    async def check_raid(
            self, config: GuildConfig, guild: discord.Guild, member: discord.Member, message: discord.Message
    ) -> None:
        if not config.flags.raid:
            return

        guild_id = guild.id
        checker = self._spam_check[guild_id]
        result = checker.is_spamming(message)
        if result is None:
            return

        if isinstance(result, SpammerSequence):
            members = result.members
        else:
            members = [member]

        for user in members:
            try:
                await guild.ban(user, reason=result.reason)
            except discord.HTTPException:
                log.info('[Moderation] Failed to ban %s (ID: %s) from server %s.', member, member.id, member.guild)
            else:
                log.info('[Moderation] Banned %s (ID: %s) from server %s.', member, member.id, member.guild)

    @lock('Moderation', 'message_batch', wait=True)
    async def send_message_patch(self, guild_id: int, channel_id: int, to_send: str):
        self.message_batches[(guild_id, channel_id)].append(to_send)

    async def mention_spam_ban(
            self,
            mention_count: int,
            guild_id: int,
            message: discord.Message,
            member: discord.Member,
            multiple: bool = False,
    ) -> None:

        if multiple:
            reason = f'Spamming mentions over multiple messages ({mention_count} mentions)'
        else:
            reason = f'Spamming mentions ({mention_count} mentions)'

        try:
            await member.ban(reason=reason)
        except Exception:  # noqa
            log.info('[Mention Spam] Failed to ban member %s (ID: %s) in guild ID %s', member, member.id, guild_id)
        else:
            to_send = f'<:discord_info:1113421814132117545> Banned **{member}** (ID: `{member.id}`) for spamming `{mention_count}` mentions.'
            await self.send_message_patch(guild_id, message.channel.id, to_send)

            log.info('[Mention Spam] Member %s (ID: %s) has been banned from guild ID %s', member, member.id, guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if (
                author.id in (self.bot.user.id, self.bot.owner_id)
                or message.guild is None
                or not isinstance(author, discord.Member)
                or author.bot
                or author.guild_permissions.manage_messages
        ):
            return

        guild_id = message.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if (
                message.channel.id in config.safe_automod_entity_ids
                or author.id in config.safe_automod_entity_ids
                or any(i in config.safe_automod_entity_ids for i in author._roles)  # noqa
        ):
            return

        await self.check_raid(config, message.guild, author, message)

        if config.flags.gatekeeper:
            gatekeeper = await self.get_guild_gatekeeper(guild_id)
            if gatekeeper is not None and gatekeeper.is_bypassing(author):
                reason = 'Bypassing gatekeeper by messaging early'
                coro = author.ban if gatekeeper.bypass_action == 'ban' else author.kick
                try:
                    await coro(reason=reason)
                except discord.HTTPException:
                    pass
                else:
                    return

        if not config.mention_count:
            return

        checker = self._spam_check[guild_id]
        if checker.is_mention_spam(message, config):
            await self.mention_spam_ban(config.mention_count, guild_id, message, author, multiple=True)
            return

        if len(message.mentions) <= 3:
            return

        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        await self.mention_spam_ban(mention_count, guild_id, message, author)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """|coro|

        This listener is used to check if a member is a fast joiner or a suspicious joiner.
        If a member is a fast joiner, they are flagged and if they are a suspicious joiner, they are flagged as well.
        If the guild has the `gatekeeper` flag enabled, the gatekeeper is used to check if the member is a spammer.
        """
        guild_id = member.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if config.is_muted(member):
            return await config.apply_mute(member, 'Member was previously muted.')

        if not config.flags.gatekeeper:
            return

        checker = self._spam_check[guild_id]

        if config.flags.gatekeeper:
            gatekeeper = await self.get_guild_gatekeeper(guild_id)
            if gatekeeper is not None:
                if gatekeeper.started_at is not None:
                    await gatekeeper.block(member)
                elif not gatekeeper.requires_setup:
                    spammers = checker.check_gatekeeper(member, gatekeeper)
                    if spammers:
                        await gatekeeper.force_enable_with(spammers)
                        for member in spammers:
                            checker.flag_member(member)

                        if config.flags.alerts:
                            embed = discord.Embed(
                                title='Gatekeeper - Rapid Join',
                                description=(
                                    f'Detected {plural(len(spammers)):member} joining in rapid succession. '
                                    'The following actions have been automatically taken:\n'
                                    '- Enabled Gatekeeper to block them from participating.\n'
                                    # '- Disabled invites for an hour to prevent any more users from joining\n'
                                ),
                                colour=helpers.Colour.light_orange()
                            )
                            view = discord.ui.View(timeout=None)
                            view.add_item(GatekeeperAlertMassbanButton(self))
                            view.add_item(GatekeeperAlertResolveButton(gatekeeper))
                            await config.send_alert(embed=embed, view=view)

        if config.flags.alerts:
            spammers = checker.is_alertable_join_spam(member)
            if spammers:
                msg = (
                    f'Detected {plural(len(spammers)):member} joining in rapid succession. **Please review.**'
                )
                view = discord.ui.View(timeout=None)
                view.add_item(GatekeeperAlertMassbanButton(self))
                await config.send_alert(msg, view=view)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        """|coro|

        This listener is used to remove members from the spam checker when they leave the guild.
        """
        checker = self._spam_check.get(payload.guild_id)
        if checker is None:
            return

        checker.remove_member(payload.user)

    @lock('Moderation', 'mute_batch', wait=True)
    async def send_mute_patch(self, guild_id: int, member_id: int, value: Any):
        """|coro|

        Send a mute patch to the database.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to send the patch to.
        member_id: :class:`int`
            The member ID to send the patch to.
        value: Any
            The value to send the patch to.
        """
        self._mute_data_batch[guild_id].append((member_id, value))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """|coro|

        This listener is used to check if a member has been muted or unmuted.
        If a member has been muted or unmuted, the mute patch is sent to the database.

        Parameters
        ----------
        before: :class:`discord.Member`
            The member before the update.
        after: :class:`discord.Member`
            The member after the update.
        """
        if before.roles == after.roles:
            return

        guild_id = after.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if config.mute_role_id is None:
            return

        before_has = before.get_role(config.mute_role_id)
        after_has = after.get_role(config.mute_role_id)

        if before_has == after_has:
            return

        await self.send_mute_patch(guild_id, after.id, after_has)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """|coro|

        This listener is used to check if a role has been deleted.
        If a role has been deleted, the mute role is checked and if the role is the mute role, the mute role is removed.

        Parameters
        ----------
        role: :class:`discord.Role`
            The role that has been deleted.
        """
        guild_id = role.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if role.id == config.mute_role_id:
            query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
            await self.bot.pool.execute(query, guild_id)
            self.get_guild_config.invalidate(self, guild_id)

        if config.flags.gatekeeper:
            gatekeeper = await self.get_guild_gatekeeper(guild_id)
            if gatekeeper is not None and gatekeeper.role_id == role.id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        'Gatekeeper **role** has been deleted while it\'s active, '
                        'therefore it\'s been automatically disabled.'
                    )

                await gatekeeper.edit(started_at=None, role_id=None)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """|coro|

        This listener is used to check if a channel has been deleted.
        If a channel has been deleted, the gatekeeper channel is checked and if the channel is the gatekeeper channel,
        the gatekeeper channel is removed.

        Parameters
        ----------
        channel: :class:`discord.abc.GuildChannel`
            The channel that has been deleted.
        """
        guild_id = channel.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.get_guild_gatekeeper(guild_id)
        if gatekeeper is not None and gatekeeper.channel_id == channel.id:
            if gatekeeper.started_at is not None:
                await config.send_alert(
                    'Gatekeeper **channel** has been deleted while it\'s active, '
                    'therefore it\'s been automatically disabled.'
                )
            await gatekeeper.edit(started_at=None, channel_id=None)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """|coro|

        This listener is used to check if a message has been deleted.
        If a message has been deleted, the gatekeeper starter message is checked and if the message is the gatekeeper
        starter message, the gatekeeper starter message is removed.

        Parameters
        ----------
        payload: :class:`discord.RawMessageDeleteEvent`
            The message that has been deleted.
        """
        config = await self.get_guild_config(payload.guild_id)
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.get_guild_gatekeeper(payload.guild_id)
        if gatekeeper is not None and gatekeeper.message_id == payload.message_id:
            if gatekeeper.started_at is not None:
                await config.send_alert(
                    'Gatekeeper **starter message** has been deleted while it\'s active, '
                    'therefore it\'s been automatically disabled.'
                )
            await gatekeeper.edit(started_at=None, message_id=None)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        """|coro|

        This listener is used to check if a message has been deleted in bulk.
        If a message has been deleted in bulk, the gatekeeper starter message is checked and if the message is the gatekeeper
        starter message, the gatekeeper starter message is removed.

        Parameters
        ----------
        payload: :class:`discord.RawBulkMessageDeleteEvent`
            The message that has been deleted in bulk.
        """
        config = await self.get_guild_config(payload.guild_id)
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.get_guild_gatekeeper(payload.guild_id)
        if gatekeeper is not None and gatekeeper.message_id in payload.message_ids:
            if gatekeeper.started_at is not None:
                await config.send_alert(
                    'Gatekeeper starter message has been deleted while it\'s active, therefore it\'s been automatically disabled.'
                )
            await gatekeeper.edit(started_at=None, message_id=None)

    @commands.Cog.listener()
    async def on_voice_state_update(
            self,
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState
    ):
        """|coro|

        This listener is used to check if a member has joined a voice channel.
        If a member has joined a voice channel, the gatekeeper is checked and if the member is bypassing the gatekeeper,
        the member is banned or kicked.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member that has joined a voice channel.
        before: :class:`discord.VoiceState`
            The voice state before the member joined the voice channel.
        after: :class:`discord.VoiceState`
            The voice state after the member joined the voice channel.
        """
        joined_voice = before.channel is None and after.channel is not None
        if not joined_voice:
            return

        config = await self.get_guild_config(member.guild.id)
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.get_guild_gatekeeper(member.guild.id)
        # Joined VC and is bypassing gatekeeper
        if gatekeeper is not None and gatekeeper.is_bypassing(member):
            reason = 'Bypassing gatekeeper by joining a voice channel early'
            coro: Coro = member.ban if gatekeeper.bypass_action == 'ban' else member.kick
            try:
                await coro(reason=reason)
            except discord.HTTPException:
                pass

    @commands.command(
        name='slowmode',
        aliases=['sm'],
        description='Applies slowmode to this channel.',
        guild_only=True
    )
    @commands.permissions(bot=['manage_channels'], user=['manage_channels'])
    @app_commands.describe(duration='The slowmode duration or 0s to disable')
    async def slowmode(self, ctx: GuildContext, duration: ShortTime):
        """Applies slowmode to this channel"""

        delta = duration.dt - ctx.message.created_at
        slowmode_delay = int(delta.total_seconds())

        if slowmode_delay > 21600:
            await ctx.send('Provided slowmode duration is too long!', ephemeral=True)
        else:
            reason = f'Slowmode changed by {ctx.author} (ID: {ctx.author.id})'
            await ctx.channel.edit(slowmode_delay=slowmode_delay, reason=reason)
            if slowmode_delay > 0:
                fmt = timetools.human_timedelta(duration.dt, source=ctx.message.created_at, accuracy=2)
                await ctx.send(f'Configured slowmode to {fmt}', ephemeral=True)
            else:
                await ctx.send(f'Disabled slowmode', ephemeral=True)

    @commands.command(
        commands.hybrid_group,
        name='moderation',
        aliases=['mod'],
        fallback='info',
        description='Show current Moderation (automatic moderation) behaviour on the server.',
        guild_only=True
    )
    @commands.permissions(user=PermissionTemplate.mod)
    async def moderation(self, ctx: GuildContext):
        """Show current Moderation (Automatic Moderation) behavior on the server.
        You must have Ban Members and Manage Messages permissions to use this
        command or its subcommands.
        """

        config: GuildConfig = await self.get_guild_config(ctx.guild.id)
        if config is None:
            return await ctx.stick(False, 'This server does not have moderation enabled.')

        embed = discord.Embed(
            title=f'{ctx.guild.name} Moderation',
            timestamp=discord.utils.utcnow(),
            color=helpers.Colour.darker_red())
        embed.set_thumbnail(url=get_asset_url(ctx.guild))

        if config.flags.audit_log:
            channel = f'<#{config.audit_log_channel_id}>'
            audit_log_broadcast = f'Bound to {channel}'
        else:
            audit_log_broadcast = '*Disabled*'

        if config.flags.alerts:
            alerts = f'Enabled on <#{config.alert_channel_id}>'
        else:
            alerts = 'Disabled'

        embed.add_field(name='Audit Log', value=audit_log_broadcast)
        embed.add_field(name='Mod Alerts', value=alerts)
        embed.add_field(name='Raid Protection', value='Enabled' if config.flags.raid else '*Disabled*')

        mention_spam = f'{config.mention_count} mentions' if config.mention_count else '*Disabled*'
        embed.add_field(name='Mention Spam Protection', value=mention_spam)

        if config.flags.gatekeeper:
            gatekeeper = await self.get_guild_gatekeeper(ctx.guild.id)
            if gatekeeper is not None:
                gatekeeper_status = gatekeeper.status
            else:
                gatekeeper_status = 'Partially Disabled'
        else:
            gatekeeper_status = 'Completely Disabled'

        embed.add_field(name='Gatekeeper', value=gatekeeper_status, inline=len(gatekeeper_status) <= 25)

        if config.safe_automod_entity_ids:
            def resolve_entity_id(x: int):
                if ctx.guild.get_role(x):
                    return f'<@&{x}>'
                if ctx.guild.get_channel_or_thread(x):
                    return f'<#{x}>'
                return f'<@{x}>'

            if len(config.safe_automod_entity_ids) <= 5:
                ignored = '\n'.join(resolve_entity_id(c) for c in config.safe_automod_entity_ids)
            else:
                sliced = list(config.safe_automod_entity_ids)[:5]
                entities = '\n'.join(resolve_entity_id(c) for c in sliced)
                ignored = f'{entities}\n(*{len(config.safe_automod_entity_ids) - 5} more...*)'
        else:
            ignored = '*N/A*'

        embed.add_field(name='Ignored Entities', value=ignored, inline=False)

        await ctx.send(embed=embed)

    @commands.command(
        moderation.command,
        name='alerts',
        description='Toggles alert message logging on the server.'
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @app_commands.describe(
        channel='The channel to send alert messages to. The bot must be able to create webhooks in it.')
    async def moderation_alerts(self, ctx: GuildContext, *, channel: discord.TextChannel):
        """Toggles alert message logging on the server.

        The bot must have the ability to create webhooks in the given channel.
        """
        await ctx.defer()
        config = await self.get_guild_config(ctx.guild.id)
        if config and config.flags.alerts:
            return await ctx.stick(
                None,
                f'You already have alert message logging enabled. To disable, use "{ctx.prefix}moderation disable alerts"'
            )

        channel_id = channel.id

        reason = f'{ctx.author} (ID: {ctx.author.id}) enabled RoboMod alert message logging'

        try:
            webhook = await channel.create_webhook(
                name='Moderation Alerts', avatar=await self.bot.user.avatar.read(), reason=reason)
        except discord.Forbidden:
            return await ctx.stick(
                False, f'The bot does not have permissions to create webhooks in {channel.mention}.')
        except discord.HTTPException:
            return await ctx.stick(
                False, 'An error occurred while creating the webhook. Note you can only have 10 webhooks per channel.')

        query = """
            INSERT INTO guild_config (id, flags, alert_channel_id, alert_webhook_url)
            VALUES ($1, $2, $3, $4) ON CONFLICT (id)
            DO UPDATE SET
                flags = guild_config.flags | EXCLUDED.flags,
                alert_channel_id = EXCLUDED.alert_channel_id,
                alert_webhook_url = EXCLUDED.alert_webhook_url;
        """

        flags = AutoModFlags()
        flags.alerts = True
        await ctx.db.execute(query, ctx.guild.id, flags.value, channel_id, webhook.url)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, f'Alert messages enabled. Sending alerts to <#{channel_id}>.')

    async def disable_automod_alerts(self, guild_id: int):
        """Disables alert message logging in the given guild."""
        query = """
            UPDATE guild_config SET
                alert_channel_id = NULL,
                alert_webhook_url = NULL,
                flags = guild_config.flags & ~$2::SMALLINT
            WHERE id = $1;
        """

        await self.bot.pool.execute(query, guild_id, AutoModFlags.alerts.flag)
        self.get_guild_config.invalidate(self, guild_id)

    @commands.command(
        moderation.group,
        name='auditlog',
        fallback='set',
        description='Toggles audit text log on the server.'
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @app_commands.describe(
        channel='The channel to broadcast audit log messages to. The bot must be able to create webhooks in it.'
    )
    async def moderation_auditlog(self, ctx: GuildContext, *, channel: discord.TextChannel):
        """Toggles audit text log on the server.
        Audit Log sends a message to the log channel whenever a certain event is triggered.
        """
        await ctx.defer()

        reason = f'{ctx.author} (ID: {ctx.author.id}) enabled Moderation audit log'

        query = "SELECT audit_log_webhook_url FROM guild_config WHERE id = $1;"
        wh_url: Optional[str] = await self.bot.pool.fetchval(query, ctx.guild.id)
        if wh_url is not None:
            # Delete the old webhook, if it exists
            try:
                webhook = discord.Webhook.from_url(wh_url, session=self.bot.session)
                await webhook.delete(reason=reason)
            except discord.HTTPException:
                pass

        try:
            webhook = await channel.create_webhook(
                name='Moderation Audit Log', avatar=await self.bot.user.display_avatar.read(), reason=reason)
        except discord.Forbidden:
            return await ctx.stick(False, 'I do not have permissions to create a webhook in that channel.')
        except discord.HTTPException:
            return await ctx.stick(
                False, 'Failed to create a webhook in that channel. '
                       'Note that the limit for webhooks in each channel is **10**.')

        query = """
            INSERT INTO guild_config (id, flags, audit_log_channel_id, audit_log_webhook_url)
                VALUES ($1, $2, $3, $4) ON CONFLICT (id)
                DO UPDATE SET
                    flags = guild_config.flags | $2,
                    audit_log_channel_id = $3,
                    audit_log_webhook_url = $4;
        """

        await ctx.db.execute(query, ctx.guild.id, AutoModFlags.audit_log.flag, channel.id, webhook.url)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, f'Audit log enabled. Broadcasting log events to <#{channel.id}>.')

    @commands.command(
        moderation_auditlog.command,
        name='alter',
        description='Configures the audit log events.',
    )
    @app_commands.describe(
        flag='The flag you want to set.',
        value='The value you want to set the flag to.'
    )
    async def moderation_auditlog_alter(self, ctx: GuildContext, flag: str, value: bool):
        """Configures the audit log events.
        You can set the Events you want to get notified about via the Audit Log Channel.
        """
        config: GuildConfig = await self.get_guild_config(ctx.guild.id)
        if config is None:
            return await ctx.stick(False, 'This server does not have moderation enabled.')

        if not config.flags.audit_log:
            return await ctx.stick(False, 'Audit log is not enabled on this server.')

        if flag == 'all':
            for key in config.audit_log_flags:
                config.audit_log_flags[key] = value
            content = f'Set all Audit Log Events to `{value}`.'
        else:
            if flag in config.audit_log_flags:
                config.audit_log_flags[flag] = value
                content = f'Set Audit Log Event **{flag}** to `{value}`.'
            else:
                raise commands.BadArgument(f'Unknown flag **{flag}**')

        query = "UPDATE guild_config SET audit_log_flags = $2 WHERE id = $1;"
        await ctx.db.execute(query, ctx.guild.id, config.audit_log_flags)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, content)

    @moderation_auditlog_alter.autocomplete('flag')
    async def moderation_auditlog_alter_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT audit_log_flags FROM guild_config WHERE id = $1;"
        flags = [
            (k, v) for k, v in (await self.bot.pool.fetchval(query, interaction.guild_id)).items()
        ]

        results = fuzzy.finder(current, flags, key=lambda x: x[0])
        return [
            app_commands.Choice(name='All', value='all'),
            *[app_commands.Choice(name=f'{flag} - {value}', value=flag) for (flag, value) in results]
        ]

    @commands.command(
        moderation.command,
        name='disable',
        description='Disables Moderation on the server.',
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @app_commands.describe(protection='The protection to disable')
    @app_commands.choices(
        protection=[
            app_commands.Choice(name='Everything', value='all'),
            app_commands.Choice(name='Alerts', value='alerts'),
            app_commands.Choice(name='Raid protection', value='raid'),
            app_commands.Choice(name='Mention spam protection', value='mentions'),
            app_commands.Choice(name='Audit Logging', value='auditlog'),
            app_commands.Choice(name='Gatekeeper', value='gatekeeper'),
        ]
    )
    async def moderation_disable(
            self,
            ctx: GuildContext,
            *,
            protection: Literal['all', 'raid', 'mentions', 'auditlog', 'alerts', 'gatekeeper'] = 'all'
    ):
        """Disables Moderation on the server.
        This can be one of these settings:
        - 'all' to disable everything
        - 'alerts' to disable alert messages
        - 'raid' to disable raid protection
        - 'mentions' to disable mention spam protection
        - 'auditlog' to disable audit logging
        - 'gatekeeper' to disable gatekeeper
        If not given then it defaults to 'all'.
        """

        if protection == 'all':
            updates = "flags = 0, mention_count = 0, broadcast_channel = NULL, audit_log_channel = NULL"
            message = 'Moderation has been disabled.'
        elif protection == 'raid':
            updates = f"flags = guild_config.flags & ~{AutoModFlags.raid.flag}"
            message = 'Raid protection has been disabled.'
        elif protection == 'alerts':
            updates = f"flags = guild_config.flags & ~{AutoModFlags.alerts.flag}, alert_channel = NULL"
            message = 'Alert messages have been disabled.'
        elif protection == 'mentions':
            updates = "mention_count = NULL"
            message = 'Mention spam protection has been disabled'
        elif protection == 'auditlog':
            updates = f"flags = guild_config.flags & ~{AutoModFlags.audit_log.flag}, audit_log_channel = NULL, audit_log_flags = NULL"
            message = 'Audit logging has been disabled.'
        elif protection == 'gatekeeper':
            updates = f"flags = guild_config.flags & ~{AutoModFlags.gatekeeper.flag}"
            message = 'Gatekeeper has been disabled.'
        else:
            raise commands.BadArgument(f'Unknown protection {protection}')

        query = f'UPDATE guild_config SET {updates} WHERE id=$1 RETURNING audit_log_webhook_url, alert_webhook_url;'

        guild_id = ctx.guild.id
        records = await self.bot.pool.fetchrow(query, guild_id)
        self._spam_check.pop(guild_id, None)
        self.get_guild_config.invalidate(self, guild_id)

        hooks = [
            [records.get('audit_log_webhook_url', None), 'Audit Log'],
            [records.get('alert_webhook_url', None), 'Alerts']
        ] if protection in ('auditlog', 'all') else []

        warnings = []

        for record in hooks:
            if record[0]:
                wh = discord.Webhook.from_url(record[0], session=self.bot.session)
                try:
                    await wh.delete(reason=message)
                except discord.HTTPException:
                    warnings.append(
                        f'The webhook `{record[1]}` could not be deleted for some reason.'
                    )

        if protection in ('all', 'gatekeeper'):
            gatekeeper = await self.get_guild_gatekeeper(guild_id)
            if gatekeeper is not None and gatekeeper.started_at is not None:
                await gatekeeper.disable()
                warnings.append('Gatekeeper was previously running and has been forcibly disabled.')
                members = gatekeeper.pending_members
                if members:
                    warnings.append(
                        f'There {plural(members):is|are!} still {plural(members):member} waiting in the role queue.'
                        ' **The queue will be paused until gatekeeper is re-enabled**'
                    )

        if warnings:
            warning = '<:warning:1113421726861238363> **Warnings:**\n' + '\n'.join(warnings)
            message = f'{message}\n\n{warning}'

        await ctx.stick(True, message)

    @commands.command(
        moderation.command,
        name='gatekeeper',
        description='Enables and shows the gatekeeper settings menu for the server.'
    )
    @commands.permissions(user=PermissionTemplate.mod)
    async def moderation_gatekeeper(self, ctx: GuildContext):
        """Enables and shows the gatekeeper settings menu for the server.

        Gatekeeper automatically assigns a role to members who join to prevent
        them from participating in the server until they verify themselves by
        pressing a button.
        """
        guild_id = ctx.guild.id
        if not ctx.me.guild_permissions.ban_members:
            return await ctx.send('\N{NO ENTRY SIGN} I do not have permissions to ban members.')

        previous = self._gatekeeper_menus.pop(guild_id, None)
        if previous is not None:
            await previous.on_timeout()
            previous.stop()

        gatekeeper = await self.get_guild_gatekeeper(guild_id)
        async with self.bot.pool.acquire(timeout=300.0) as conn:
            async with conn.transaction():
                if gatekeeper is None:
                    query = "INSERT INTO guild_gatekeeper(id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING *;"
                    record = await conn.fetchrow(query, guild_id)
                    gatekeeper = Gatekeeper([], self, record=record)

                query = """
                    INSERT INTO guild_config (id, flags)
                    VALUES ($1, $2) ON CONFLICT (id)
                    DO UPDATE SET 
                        flags = guild_config.flags | $2
                    RETURNING *;
                """
                record = await conn.fetchrow(query, guild_id, AutoModFlags.gatekeeper.flag)
                config = GuildConfig(self.bot, record=record)

        self.get_guild_config.invalidate(self, guild_id)

        embed = discord.Embed(
            title='Gatekeeper Configuration - Information',
            description=(
                'Gatekeeper is a feature that automatically assigns a role to a member when they join, '
                'for the sole purpose of blocking them from accessing the server.\n'
                'The user must press a button in order to verify themselves and have their role removed.\n\n'
                'In order to set up gatekeeper, a few things are required:\n'
                '- A channel that locked users will see but regular users will not.\n'
                '- A role that is assigned when users join.\n'
                '- A message that the bot sends in the channel with the verify button.\n\n'
                'There are also settings to help configure some aspects of it:\n'
                '- "Auto" automatically triggers the gatekeeper if N members join in a span of M seconds\n'
                '- "Bypass Action" configures what action is taken when a user talks or joins voice before verifying\n\n'
                'Note that once gatekeeper is enabled, even by auto, it must be manually disabled.'
            ),
            colour=helpers.Colour.light_grey()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))

        self._gatekeeper_menus[guild_id] = view = GatekeeperSetUpView(self, ctx.author, config, gatekeeper)  # noqa
        view.message = await ctx.send(embed=embed, view=view)

    @commands.command(
        moderation.command,
        name='raid',
        description='Toggles raid protection on the server.',
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @app_commands.describe(enabled='Whether raid protection should be enabled or not, toggles if not given.')
    async def moderation_raid(self, ctx: GuildContext, enabled: Optional[bool] = None):
        """Toggles raid protection on the server.
        Raid protection automatically bans members that spam messages in your server.
        """

        perms = ctx.me.guild_permissions
        if not perms.ban_members:
            return await ctx.stick(False, 'I do not have permissions to ban members.')

        query = """
            INSERT INTO guild_config (id, flags)
            VALUES ($1, $2) ON CONFLICT (id)
            DO UPDATE SET
                flags = CASE COALESCE($3, NOT (guild_config.flags & $2 = $2))
                                WHEN TRUE THEN guild_config.flags | $2
                                WHEN FALSE THEN guild_config.flags & ~$2
                        END
            RETURNING COALESCE($3, (flags & $2 = $2));
        """

        enabled = await self.bot.pool.fetchval(query, ctx.guild.id, AutoModFlags.raid.flag, enabled)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        fmt = '*enabled*' if enabled else '*disabled*'
        await ctx.stick(True, f'Raid protection {fmt}.')

    @commands.command(
        moderation.command,
        name='mentions',
        description='Enables auto-banning accounts that spam more than \'count\' mentions.'
    )
    @commands.permissions(user=PermissionTemplate.mod)
    @app_commands.describe(count='The maximum amount of mentions before banning.')
    async def moderation_mentions(self, ctx: GuildContext, count: commands.Range[int, 3]):
        """
        Enables auto-banning accounts that spam more than 'count' mentions.
        To use this command, you must have the Ban Members permission.

        The count must be greater than 3.
        The bot will automatically ban members that spam more than the specified amount of mentions.

        Note: This applies to only for user mentions, role mentions are not counted.
        """

        query = """
            INSERT INTO guild_config (id, mention_count, safe_automod_entity_ids)
            VALUES ($1, $2, '{}')
            ON CONFLICT (id) DO UPDATE SET
               mention_count = $2;
        """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, f'Mention spam protection threshold set to `{count}`.')

    @moderation_mentions.error
    async def moderation_mentions_error(self, ctx: GuildContext, error: commands.CommandError):
        if isinstance(error, commands.RangeError):
            await ctx.stick(False, 'Mention spam protection threshold must be greater than **3**.')

    @commands.command(
        moderation.command,
        name='ignore',
        description='Specifies what roles, members, or channels ignore Moderation Inspections.'
    )
    @commands.permissions(user=['ban_members'])
    @app_commands.describe(entities='Space separated list of roles, members, or channels to ignore')
    async def moderation_ignore(
            self,
            ctx: GuildContext,
            entities: Annotated[List[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ):
        """Adds roles, members, or channels to the ignore list for Moderation auto-bans."""

        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
               ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_automod_entity_ids, '{}') || $2::bigint[]))
            WHERE id = $1;
        """

        if len(entities) == 0:
            raise commands.BadArgument('Missing entities to ignore.')

        ids = [c.id for c in entities]
        await ctx.db.execute(query, ctx.guild.id, ids)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(
            f'<:discord_info:1113421814132117545> Updated ignore list to ignore {', '.join(c.mention for c in entities)}',
            allowed_mentions=discord.AllowedMentions.none())

    @commands.command(
        moderation.command,
        name='unignore',
        description='Specifies what roles, members, or channels to take off the ignore list.'
    )
    @commands.permissions(user=['ban_members'])
    @app_commands.describe(entities='Space separated list of roles, members, or channels to take off the ignore list')
    async def moderation_unignore(
            self,
            ctx: GuildContext,
            entities: Annotated[List[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ):
        """Remove roles, members, or channels from the ignore list for Moderation auto-bans."""
        if len(entities) == 0:
            raise commands.BadArgument('Missing entities to unignore.')

        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
               ARRAY(SELECT element FROM unnest(safe_automod_entity_ids) AS element
                     WHERE NOT(element = ANY($2::bigint[])))
            WHERE id = $1;
        """

        await ctx.db.execute(query, ctx.guild.id, [c.id for c in entities])
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(
            f'<:discord_info:1113421814132117545> Updated ignore list to no longer ignore {', '.join(c.mention for c in entities)}',
            allowed_mentions=discord.AllowedMentions.none())

    @commands.command(
        moderation.command,
        name='ignored',
        description='Lists what channels, roles, and members are in the Moderation ignore list.'
    )
    async def moderation_ignored(self, ctx: GuildContext):
        """List all the channels, roles, and members that are in the Moderation ignore list."""

        config = await self.get_guild_config(ctx.guild.id)
        if config is None or not config.safe_automod_entity_ids:
            return await ctx.stick(False, 'This server does not have any ignored entities.')

        def resolve_entity_id(x: int, *, guild=ctx.guild):
            if guild.get_role(x):
                return f'<@&{x}>'
            if guild.get_channel_or_thread(x):
                return f'<#{x}>'
            return f'<@{x}>'

        entities = [resolve_entity_id(x) for x in config.safe_automod_entity_ids]

        class EmbedPaginator(BasePaginator[str]):
            colour = self.bot.colour.darker_red()

            async def format_page(self, entries: List[str], /) -> discord.Embed:
                embed = discord.Embed(timestamp=discord.utils.utcnow(), color=self.colour)
                embed.set_author(name=f'Ignored Entities', icon_url=get_asset_url(ctx.guild))
                embed.set_footer(text=f'{plural(len(entities)):entity|entities}')
                embed.description = '\n'.join(entries)
                return embed

        await EmbedPaginator.start(ctx, entries=entities, per_page=15)

    @commands.command(
        commands.hybrid_command,
        name='purge',
        description='Removes messages that meet a criteria.',
        aliases=['clear'],
        usage='[search]',
        guild_only=True
    )
    @commands.permissions(user=['manage_messages'], bot=['manage_messages'])
    @app_commands.describe(search='How many messages to search for')
    async def purge(
            self,
            ctx: GuildContext,
            search: Optional[commands.Range[int, 1, 2000]] = None,
            *,
            flags: PurgeFlags
    ):
        """Removes messages that meet a criteria.
        This command uses a syntax similar to Discord's search bar.
        The messages are only deleted if all options are met unless
        the `--require` flag is passed to override the behaviour.

        When the command is done doing its work, you will get a message
        detailing which users got removed and how many messages got removed.
        """
        if ctx.interaction:
            await ctx.defer()

        predicates: list[Callable[[discord.Message], Any]] = []
        if flags.bot:
            if flags.webhooks:
                predicates.append(lambda m: m.author.bot)
            else:
                predicates.append(lambda m: (m.webhook_id is None or m.interaction is not None) and m.author.bot)
        elif flags.webhooks:
            predicates.append(lambda m: m.webhook_id is not None)

        if flags.embeds:
            predicates.append(lambda m: len(m.embeds))

        if flags.files:
            predicates.append(lambda m: len(m.attachments))

        if flags.reactions:
            predicates.append(lambda m: len(m.reactions))

        if flags.emoji:
            custom_emoji = re.compile(r'<:(\w+):(\d+)>')
            predicates.append(lambda m: custom_emoji.search(m.content))

        if flags.user:
            predicates.append(lambda m: m.author == flags.user)

        if flags.contains:
            predicates.append(lambda m: flags.contains in m.content)  # type: ignore

        if flags.prefix:
            predicates.append(lambda m: m.content.startswith(flags.prefix))  # type: ignore

        if flags.suffix:
            predicates.append(lambda m: m.content.endswith(flags.suffix))  # type: ignore

        if not flags.delete_pinned:
            predicates.append(lambda m: not m.pinned)

        require_prompt = False
        if not predicates:
            require_prompt = True
            predicates.append(lambda m: True)

        op = all if flags.require == 'all' else any

        def predicate(m: discord.Message) -> bool:
            r = op(p(m) for p in predicates)
            return r

        if flags.after:
            if search is None:
                search = 2000

        if search is None:
            search = 100

        if require_prompt:
            confirm = await ctx.prompt(
                f'<:warning:1113421726861238363> Are you sure you want to delete `{plural(search):message}`?',
                ephemeral=True,
                timeout=30)
            if not confirm:
                return

        async with ctx.channel.typing():
            before = discord.Object(id=flags.before) if flags.before else None
            after = discord.Object(id=flags.after) if flags.after else None

            try:
                deleted = await asyncio.wait_for(
                    ctx.channel.purge(limit=search, before=before, after=after, check=predicate),
                    timeout=100,
                )
            except discord.Forbidden:
                return await ctx.stick(False, 'I do not have permissions to delete messages.')
            except discord.HTTPException as e:
                return await ctx.stick(False, f'Failed to delete messages: {e}')

            spammers = Counter(m.author.display_name for m in deleted)
            deleted = len(deleted)
            messages = [f'`{deleted}` message{' was' if deleted == 1 else 's were'} removed.']
            if deleted:
                messages.append('')
                spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
                messages.extend(f'**{name}**: `{count}`' for name, count in spammers)

            to_send = '\n'.join(messages)

            if len(to_send) > 4000:
                to_send = f'Successfully removed `{deleted}` messages.'

            embed = discord.Embed(title='Channel Purge', description=to_send)
            await ctx.send(embed=embed, delete_after=15)

    async def get_lockdown_information(
            self, guild_id: int, channel_ids: Optional[list[int]] = None
    ) -> dict[int, discord.PermissionOverwrite]:
        rows: list[tuple[int, int, int]]
        if channel_ids is None:
            query = "SELECT channel_id, allow, deny FROM guild_lockdowns WHERE guild_id=$1;"
            rows = await self.bot.pool.fetch(query, guild_id)
        else:
            query = """
                SELECT channel_id, allow, deny
                FROM guild_lockdowns
                WHERE guild_id=$1 AND channel_id = ANY($2::bigint[]);
            """

            rows = await self.bot.pool.fetch(query, guild_id, channel_ids)

        return {
            channel_id: discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
            for channel_id, allow, deny in rows
        }

    async def start_lockdown(
            self, ctx: GuildContext, channels: list[discord.TextChannel | discord.VoiceChannel]
    ) -> tuple[list[discord.TextChannel | discord.VoiceChannel], list[discord.TextChannel | discord.VoiceChannel]]:
        guild_id = ctx.guild.id

        records = []
        success, failures = [], []
        reason = f'Lockdown request by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            for channel in channels:
                allow, deny, ow = await self._prepare_overwrites(channel)

                try:
                    await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=reason)
                except discord.HTTPException:
                    failures.append(channel)
                else:
                    success.append(channel)
                    records.append(
                        {
                            'guild_id': guild_id,
                            'channel_id': channel.id,
                            'allow': allow.value,
                            'deny': deny.value,
                        }
                    )

        query = """
            INSERT INTO guild_lockdowns(guild_id, channel_id, allow, deny)
            SELECT d.guild_id, d.channel_id, d.allow, d.deny
            FROM jsonb_to_recordset($1::jsonb) 
            AS d(
                guild_id BIGINT, 
                channel_id BIGINT, 
                allow BIGINT, 
                deny BIGINT
            )
            ON CONFLICT (guild_id, channel_id) DO NOTHING
        """
        await self.bot.pool.execute(query, records)
        return success, failures

    async def end_lockdown(
            self,
            guild: discord.Guild,
            *,
            channel_ids: Optional[list[int]] = None,
            reason: Optional[str] = None,
    ) -> list[discord.abc.GuildChannel]:
        channel_fallback: Optional[dict[int, discord.abc.GuildChannel]] = None
        default_role = guild.default_role
        failures = []

        lockdowns = await self.get_lockdown_information(guild.id, channel_ids=channel_ids)
        for channel_id, permissions in lockdowns.items():
            channel = guild.get_channel(channel_id)
            if channel is None:
                if channel_fallback is None:
                    channel_fallback = {c.id: c for c in await guild.fetch_channels()}
                    channel = channel_fallback.get(channel_id)
                    if channel is None:
                        continue
                continue

            try:
                await channel.set_permissions(default_role, overwrite=permissions, reason=reason)
            except discord.HTTPException:
                failures.append(channel)

        return failures

    async def is_cooldown_active(self, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        query = "SELECT * FROM guild_lockdowns WHERE guild_id=$1 AND channel_id=$2;"
        record = await self.bot.pool.fetchrow(query, guild.id, channel.id)
        if record:
            return True
        return False

    @staticmethod
    def is_potential_lockout(
            me: discord.Member, channel: Union[discord.Thread, discord.VoiceChannel, discord.TextChannel]
    ) -> bool:
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent is None:
                return True

            overwrites = parent.overwrites
            for role in me.roles:
                ow = overwrites.get(role)
                if ow is None:
                    continue
                if ow.send_messages_in_threads:
                    return False
            return True

        overwrites = channel.overwrites
        for role in me.roles:
            ow = overwrites.get(role)
            if ow is None:
                continue
            if ow.send_messages:
                return False
        return True

    @commands.command(
        commands.hybrid_group,
        name='lockdown',
        fallback='start',
        description='Locks down specific channels.',
        guild_only=True,
        cooldown=commands.CooldownMap(rate=1, per=30.0, type=commands.BucketType.guild)
    )
    @commands.permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    @app_commands.describe(channels='A space-separated list of text or voice channels to lock down')
    async def lockdown(
            self,
            ctx: GuildContext,
            channels: commands.Greedy[Union[discord.TextChannel, discord.VoiceChannel]]
    ):
        """Locks down channels by denying the default role to send messages or connect to voice channels."""
        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                embed = self._build_lockdown_error_embed()
                await ctx.send(embed=embed)
                return

            view = await self._handle_lockdown_permission_issue(ctx, parent)
            if view.abort:
                return
            ctx = await self.bot.get_context(view.message, cls=GuildContext)

        success, failures = await self.start_lockdown(ctx, channels)
        message = self._build_lockdown_message(success, failures)
        embed = discord.Embed(title='Locked down', description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands.command(
        lockdown.command,
        name='for',
        description='Locks down specific channels for a specified amount of time.',
        guild_only=True,
        cooldown=commands.CooldownMap(rate=1, per=30.0, type=commands.BucketType.guild)
    )
    @commands.permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    @app_commands.describe(
        duration='A duration on how long to lock down for, e.g. 30m',
        channels='A space-separated list of text or voice channels to lock down',
    )
    async def lockdown_for(
            self,
            ctx: GuildContext,
            duration: timetools.ShortTime,
            channels: commands.Greedy[Union[discord.TextChannel, discord.VoiceChannel]]
    ):
        """Locks down specific channels for a specified amount of time."""
        reminder = self.bot.reminder
        if reminder is None:
            return await ctx.stick(False, 'This functionality is currently not available.')

        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                embed = self._build_lockdown_error_embed()
                await ctx.send(embed=embed)
                return

            view = await self._handle_lockdown_permission_issue(ctx, parent)
            if view.abort:
                return
            ctx = await self.bot.get_context(view.message, cls=GuildContext)

        success, failures = await self.start_lockdown(ctx, channels)
        timer = await self._create_lockdown_timer(ctx, success, duration)
        message = self._create_lockdown_result_message_with_timer(success, failures, timer)
        embed = discord.Embed(title='Locked down', description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    async def _create_lockdown_timer(
            self, ctx: GuildContext, success: list[discord.TextChannel], duration: datetime) -> Optional[Timer]:
        reminder = self.bot.reminder
        if reminder is None:
            return None

        return await reminder.create_timer(
            duration.dt,
            'lockdown',
            ctx.guild.id,
            ctx.author.id,
            ctx.channel.id,
            [c.id for c in success],
            created=ctx.message.created_at,
        )

    @staticmethod
    def _create_lockdown_result_message_with_timer(
            success: list[discord.TextChannel], failures: list[discord.TextChannel], timer: Timer):
        long = timer.expires >= timer.created + datetime.timedelta(days=1)
        formatted_time = discord.utils.format_dt(timer.expires, 'f' if long else 'T')  # type: ignore

        if failures:
            return (
                f'Successfully locked down `{len(success)}`/`{len(failures)}` channels until {formatted_time}.\n'
                f'Failed channels: {", ".join(c.mention for c in failures)}\n'
                f'Give the bot Manage Roles permissions in {plural(len(failures)):channel|those channels} and try '
                f'the lockdown command on the failed **{plural(len(failures)):channel}** again.'
            )
        else:
            return f'**{plural(len(success)):Channel}** were successfully locked down until {formatted_time}.'

    @staticmethod
    def _build_lockdown_error_embed():
        return discord.Embed(
            title='Error',
            description='For some reason, I could not find an appropriate channel to edit overwrites for.'
                        'Note that this lockdown will potentially lock the bot from sending messages. '
                        'Please explicitly give the bot permissions to send messages in threads and channels.',
            color=discord.Color.red(),
        )

    @staticmethod
    async def _handle_lockdown_permission_issue(ctx: GuildContext, parent: discord.TextChannel):
        view = LockdownPermissionIssueView(ctx.me, parent)
        embed = discord.Embed(
            title='Warning',
            description='<:warning:1113421726861238363> This will potentially lock the bot from sending messages.\n'
                        'Would you like to resolve the permission issue?',
            color=discord.Color.yellow(),
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        return view

    @staticmethod
    def _build_lockdown_message(success: list[discord.TextChannel], failures: list[discord.TextChannel]):
        if failures:
            return (
                f'Successfully locked down `{len(success)}`/`{len(failures)}` channels.\n'
                f'Failed channels: {", ".join(c.mention for c in failures)}\n\n'
                f'Give the bot Manage Roles permissions in those channels and try again.'
            )
        else:
            return f'**{plural(len(success)):channel}** were successfully locked down.'

    @staticmethod
    async def _prepare_overwrites(
            channel: discord.TextChannel
    ) -> tuple[discord.Permissions, discord.Permissions, discord.PermissionOverwrite]:
        overwrites = channel.overwrites_for(channel.guild.default_role)
        allow, deny = overwrites.pair()
        overwrites.update(
            send_messages=False,
            add_reactions=False,
            use_slash_commands=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False
        )
        return allow, deny, overwrites

    @commands.command(
        lockdown.command,
        name='end',
        description='Ends all lockdowns set.',
        guild_only=True,
    )
    @commands.permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    async def lockdown_end(self, ctx: GuildContext):
        """Ends all set lockdowns.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """

        if not await self.is_cooldown_active(ctx.guild, ctx.channel):
            return await ctx.stick(False, 'There is no active lockdown.')

        reason = f'Lockdown ended by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            failures = await self.end_lockdown(ctx.guild, reason=reason)

        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        if failures:
            formatted = [c.mention for c in failures]
            await ctx.stick(None, f'Lockdown ended. Failed to edit {human_join(formatted, final='and')}')
        else:
            await ctx.stick(True, 'Lockdown successfully ended.')

    @commands.Cog.listener()
    async def on_lockdown_timer_complete(self, timer: LockdownTimer):
        guild_id, mod_id, channel_id, channel_ids = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            return

        member = await self.bot.get_or_fetch_member(guild, mod_id)
        if member is None:
            moderator = f'Mod ID {mod_id}'
        else:
            moderator = f'{member} (ID: {mod_id})'

        reason = f'Automatic lockdown ended from timer made on {timer.created} by {moderator}'
        failures = await self.end_lockdown(guild, channel_ids=channel_ids, reason=reason)

        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1 AND channel_id = ANY($2::bigint[]);"
        await self.bot.pool.execute(query, guild_id, channel_ids)

        channel = guild.get_channel_or_thread(channel_id)
        if channel is not None:
            assert isinstance(channel, discord.abc.Messageable)
            if failures:
                formatted = [c.mention for c in failures]
                await channel.send(
                    f'<:discord_info:1113421814132117545> Lockdown ended. However, '
                    f'I failed to properly edit {human_join(formatted, final='and')}'
                )
            else:
                valid = [f'<#{c}>' for c in channel_ids]
                await channel.send(
                    f'<:discord_info:1113421814132117545> Lockdown successfully ended for {human_join(valid, final='and')}')

    @staticmethod
    async def _basic_cleanup_strategy(ctx: GuildContext, search: int):
        count = 0
        async for msg in ctx.history(limit=search, before=ctx.message):
            if msg.author == ctx.me and not (msg.mentions or msg.role_mentions):
                await msg.delete()
                count += 1
        return {'Bot': count}

    @staticmethod
    async def _complex_cleanup_strategy(ctx: GuildContext, search: int):
        def check(m):
            return m.author == ctx.me or m.content.startswith('?')

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        return Counter(m.author.display_name for m in deleted)

    @staticmethod
    async def _regular_user_cleanup_strategy(ctx: GuildContext, search: int):
        def check(m):
            return (m.author == ctx.me or m.content.startswith('?')) and not (m.mentions or m.role_mentions)

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        return Counter(m.author.display_name for m in deleted)

    @commands.command(
        commands.core_command,
        name='cleanup',
        description='Cleans up only the bot\'s messages from the channel.',
        guild_only=True,
    )
    async def cleanup(self, ctx: GuildContext, search: int = 100):
        """Cleans up the bot's messages from the channel.
        If a search number is specified, it searches that many messages to delete.
        If the bot has Manage Messages permissions then it will try to delete
        messages that look like they invoked the bot as well.
        After the cleanup is completed, the bot will send you a message with
        which people got their messages deleted and their count. This is useful
        to see which users are spammers.
        Members with Manage Messages can search up to 1000 messages.
        Members without can search up to 25 messages.
        """
        strategy = self._basic_cleanup_strategy
        is_mod = ctx.channel.permissions_for(ctx.author).manage_messages
        if ctx.channel.permissions_for(ctx.me).manage_messages:
            if is_mod:
                strategy = self._complex_cleanup_strategy
            else:
                strategy = self._regular_user_cleanup_strategy

        if is_mod:
            search = min(max(2, search), 1000)
        else:
            search = min(max(2, search), 25)

        spammers = await strategy(ctx, search)
        deleted = sum(spammers.values())
        messages = [f'{plural(deleted):message was|messages were} removed.']
        if deleted:
            messages.append('')
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f'- **{author}**: {count}' for author, count in spammers)

        to_send = '\n'.join(messages)

        if len(to_send) > 4000:
            to_send = f'Successfully removed `{deleted}` messages.'

        embed = discord.Embed(title='Channel Cleanup', description=to_send)
        await ctx.send(embed=embed, delete_after=15)

    @commands.command(
        commands.core_command,
        name='kick',
        description='Kicks a member from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['kick_members'], bot=['kick_members'])
    async def kick(
            self,
            ctx: GuildContext,
            member: Annotated[discord.abc.Snowflake, MemberID],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Kicks a member from the server.
        In order for this to work, the bot must have Kick Member permissions.
        To use this command, you must have Kick Members permission.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        await ctx.guild.kick(member, reason=reason)
        await ctx.stick(True, f'Kicked {member}.')

    @commands.command(
        commands.core_command,
        name='ban',
        description='Bans a member from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['ban_members'], bot=['ban_members'])
    async def ban(
            self,
            ctx: GuildContext,
            member: Annotated[discord.abc.Snowflake, MemberID],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Bans a member from the server.
        You can also ban from ID to ban regardless of whether they're
        in the server or not.
        In order for this to work, the bot must have Ban Member permissions.
        To use this command, you must have Ban Members' permission.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        await ctx.guild.ban(member, reason=reason)
        await ctx.stick(True, f'Banned `{member}`.')

    @commands.command(
        commands.core_command,
        name='multiban',
        description='Bans multiple members from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['ban_members'], bot=['ban_members'])
    async def multiban(
            self,
            ctx: GuildContext,
            members: Annotated[List[discord.abc.Snowflake], commands.Greedy[MemberID]],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Bans multiple members from the server.
        This only works through banning via ID.
        In order for this to work, the bot must have Ban Member permissions.
        To use this command, you must have Ban Members' permission.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        total_members = len(members)
        if total_members == 0:
            raise commands.BadArgument('No members were passed to ban.')

        confirm = await ctx.prompt(
            f'<:warning:1113421726861238363> This will ban **{plural(total_members):member}**. Are you sure?')
        if not confirm:
            return

        failed = 0
        for member in members:
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.stick(True, f'Banned [`{total_members - failed}`/`{total_members}`] members.')

    @commands.command(
        commands.hybrid_command,
        name='massban',
        description='Mass bans multiple members from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['ban_members'], bot=['ban_members'])
    async def massban(self, ctx: GuildContext, *, flags: MassbanFlags):
        """Mass bans multiple members from the server.
        This command uses a syntax similar to Discord's search bar. To use this command,
        you and the bot must both have Ban Members' permission. **Every option is optional.**
        Users are only banned **if and only if** all conditions are met.
        """

        await ctx.defer()
        author = ctx.author
        members = []

        if flags.channel:
            before = discord.Object(id=flags.before) if flags.before else None
            after = discord.Object(id=flags.after) if flags.after else None
            predicates = []
            if flags.contains:
                predicates.append(lambda m: flags.contains in m.content)
            if flags.starts:
                predicates.append(lambda m: m.content.startswith(flags.starts))
            if flags.ends:
                predicates.append(lambda m: m.content.endswith(flags.ends))
            if flags.match:
                try:
                    _match = re.compile(flags.match)
                except re.error as e:
                    raise commands.BadArgument(f'Invalid regex passed to `match:` flag: {e}') from None
                else:
                    predicates.append(lambda m, x=_match: x.match(m.content))
            if flags.embeds:
                predicates.append(flags.embeds)
            if flags.files:
                predicates.append(flags.files)

            async for message in flags.channel.history(limit=flags.search, before=before, after=after):
                if all(p(message) for p in predicates):
                    members.append(message.author)
        else:
            if ctx.guild.chunked:
                members = ctx.guild.members
            else:
                async with ctx.typing():
                    await ctx.guild.chunk(cache=True)
                members = ctx.guild.members

        predicates = [
            lambda m: isinstance(m, discord.Member) and can_execute_action(ctx, author, m),
            lambda m: not m.bot,
            lambda m: m.discriminator != '0000',
        ]

        if flags.username:
            try:
                _regex = re.compile(flags.username)
            except re.error as e:
                raise commands.BadArgument(f'Invalid regex passed to `username:` flag: {e}') from None
            else:
                predicates.append(lambda m, x=_regex: x.match(m.name))

        if flags.avatar is False:
            predicates.append(lambda m: m.avatar is None)
        if flags.roles is False:
            predicates.append(lambda m: len(getattr(m, 'roles', [])) <= 1)

        now = discord.utils.utcnow()
        if flags.created:
            def created(_member, *, offset=now - datetime.timedelta(minutes=flags.created)):
                return _member.created_at > offset

            predicates.append(created)
        if flags.joined:
            def joined(_member, *, offset=now - datetime.timedelta(minutes=flags.joined)):
                if isinstance(_member, discord.User):
                    return True
                return _member.joined_at and _member.joined_at > offset

            predicates.append(joined)
        if flags.joined_after:
            def joined_after(_member, *, _other=flags.joined_after):
                return _member.joined_at and _other.joined_at and _member.joined_at > _other.joined_at

            predicates.append(joined_after)
        if flags.joined_before:
            def joined_before(_member, *, _other=flags.joined_before):
                return _member.joined_at and _other.joined_at and _member.joined_at < _other.joined_at

            predicates.append(joined_before)

        is_only_raid = flags.raid and len(predicates) == 3
        if len(predicates) == 3 and not flags.raid:
            raise commands.BadArgument('You must specify at least one flag to search for.')

        checker = self._spam_check[ctx.guild.id]
        if is_only_raid:
            members = checker.flagged_users
        else:
            members = {m.id: m for m in members if all(p(m) for p in predicates)}
            if flags.raid:
                members.update(checker.flagged_users)  # type: ignore

        if flags.reason is None and flags.raid:
            flags.reason = await ActionReason().convert(ctx, 'Raid detected')

        if len(members) == 0:
            raise commands.BadArgument('No members were found matching criterias.')

        if flags.show:
            members = sorted(members.values(), key=lambda m: m.joined_at or now)
            fmt = '\n'.join(f'ID: {m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\tMember: {m}' for m in members)
            content = f'- Current Time: {discord.utils.utcnow()}\n- Total members: {len(members)}\n\n{fmt}'
            file = discord.File(io.BytesIO(content.encode('utf-8')), filename='members.txt')
            return await ctx.send(file=file)

        if flags.reason is None:
            raise commands.BadArgument('You must specify a reason for banning.')
        else:
            reason = await ActionReason().convert(ctx, flags.reason)

        confirm = await ctx.prompt(f'This will ban **{plural(len(members)):member}**. Are you sure?')
        if not confirm:
            return

        count = 0
        for member in list(members.values()):
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await ctx.stick(True, f'Banned `{count}`/`{len(members)}` members.')

    @commands.command(
        commands.core_command,
        name='softban',
        description='Soft bans a member from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['kick_members'], bot=['kick_members'])
    async def softban(
            self,
            ctx: GuildContext,
            member: Annotated[discord.abc.Snowflake, MemberID],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Soft bans a member from the server.
        A softban is basically banning the member from the server but
        then unbanning the member as well. This allows you to essentially
        kick the member while removing their messages.
        In order for this to work, the bot must have Ban Member permissions.
        To use this command, you must have Kick Members permissions.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        await ctx.guild.ban(member, reason=reason)
        await ctx.guild.unban(member, reason=reason)
        await ctx.stick(True, f'Successfully soft-banned **{member}**.')

    @commands.command(
        commands.core_command,
        name='unban',
        description='Unbans a member from the server.',
        guild_only=True,
    )
    @commands.permissions(user=['ban_members'], bot=['ban_members'])
    async def unban(
            self,
            ctx: GuildContext,
            member: Annotated[discord.BanEntry, BannedMember],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Unbans a member from the server.
        You can pass either the ID of the banned member or the Name#Discrim
        combination of the member. Typically, the ID is easiest to use.
        In order for this to work, the bot must have Ban Member permissions.
        To use this command, you must have Ban Members' permissions.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        await ctx.guild.unban(member.user, reason=reason)
        if member.reason:
            await ctx.stick(
                True, f'Unbanned {member.user} (ID: `{member.user.id}`), previously banned for **{member.reason}**.')
        else:
            await ctx.stick(True, f'Unbanned {member.user} (ID: `{member.user.id}`).')

    @commands.command(
        commands.hybrid_command,
        name='tempban',
        description='Temporarily bans a member for the specified duration.',
        guild_only=True,
    )
    @commands.permissions(user=['ban_members'], bot=['ban_members'])
    @app_commands.describe(
        duration='The duration to ban the member for. Must be a future Time.',
        member='The member to ban.',
        reason='The reason for banning the member.')
    async def tempban(
            self,
            ctx: GuildContext,
            duration: app_commands.Transform[datetime.datetime, timetools.TimeTransformer(future=True)],
            member: Annotated[discord.abc.Snowflake, MemberID],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Temporarily bans a member for the specified duration.
        The duration can be a short time form, e.g. 30d or a more human
        duration such as 'until thursday at 3PM' or a more concrete time
        such as '2024-12-31'.

        Note that times are in UTC unless the timezone is
        specified using the 'timezone set' command.

        You can also ban from ID to ban regardless of whether they're
        in the server or not.
        In order for this to work, the bot must have Ban Member permissions.
        To use this command, you must have Ban Members' permission.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        reminder = self.bot.reminder
        if reminder is None:
            return await ctx.stick(False, 'This functionality is currently not available.')

        until = f'until {discord.utils.format_dt(duration, 'F')}'
        heads_up_message = f'<:discord_info:1113421814132117545> You have been banned from {ctx.guild.name} {until}. Reason: {reason}'

        try:
            await member.send(heads_up_message)  # type: ignore
        except (AttributeError, discord.HTTPException):
            pass

        reason = safe_reason_append(reason, until)
        config = await self.bot.user_settings.get_user_config(ctx.author.id)
        zone = config.timezone if config else None
        await ctx.guild.ban(member, reason=reason)
        await reminder.create_timer(
            duration,
            'tempban',
            ctx.guild.id,
            ctx.author.id,
            member.id,
            created=ctx.message.created_at,
            timezone=zone or 'UTC',
        )
        await ctx.stick(True, f'Ban for **{member}** ends {discord.utils.format_dt(duration, 'R')}.')

    @commands.Cog.listener()
    async def on_tempban_timer_complete(self, timer: Timer):
        guild_id, mod_id, member_id = timer.args
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        moderator = await self.bot.get_or_fetch_member(guild, mod_id)
        if moderator is None:
            try:
                moderator = await self.bot.fetch_user(mod_id)
            except:  # noqa
                moderator = f'Mod ID {mod_id}'
            else:
                moderator = f'{moderator} (ID: {mod_id})'
        else:
            moderator = f'{moderator} (ID: {mod_id})'

        reason = f'Automatic unban from timer made on {timer.created} by {moderator}.'
        await guild.unban(discord.Object(id=member_id), reason=reason)

    async def update_mute_role(
            self, ctx: GuildContext, config: Optional[GuildConfig], role: discord.Role, *, merge: bool = False
    ) -> None:
        guild = ctx.guild
        members = set()

        if config and merge:
            members |= config.muted_members
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id}): Merging mute roles'
            async for member in self.bot.resolve_member_ids(guild, members):
                if not member._roles.has(role.id):  # noqa
                    try:
                        await member.add_roles(role, reason=reason)
                    except discord.HTTPException:
                        pass

        members.update(map(lambda m: m.id, role.members))
        query = """
            INSERT INTO guild_config (id, mute_role_id, muted_members)
            VALUES ($1, $2, $3::bigint[]) ON CONFLICT (id)
            DO UPDATE SET
               mute_role_id = EXCLUDED.mute_role_id,
               muted_members = EXCLUDED.muted_members
        """
        await self.bot.pool.execute(query, guild.id, role.id, list(members))
        self.get_guild_config.invalidate(self, guild.id)

    # noinspection PyUnresolvedReferences
    @staticmethod
    async def update_role_permissions(
            role: discord.Role,
            guild: discord.Guild,
            invoker: discord.abc.User,
            update_read_permissions: bool = False,
            channels: Optional[Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable]] = None,
    ) -> tuple[int, int, int]:
        success, failure, skipped = 0, 0, 0
        reason = f'Action done by {invoker} (ID: {invoker.id})'
        if channels is None:
            channels = [ch for ch in guild.channels if isinstance(ch, discord.abc.Messageable)]

        guild_perms = guild.me.guild_permissions
        for channel in channels:
            perms = channel.permissions_for(guild.me)
            if perms.manage_roles:
                overwrite = channel.overwrites_for(role)
                perms = {
                    'send_messages': False,
                    'add_reactions': False,
                    'use_application_commands': False,
                    'create_private_threads': False,
                    'create_public_threads': False,
                    'send_messages_in_threads': False,
                }
                if update_read_permissions:
                    perms['read_messages'] = False

                combine_permissions(overwrite, guild_perms, **perms)
                try:
                    await channel.set_permissions(role, overwrite=overwrite, reason=reason)
                except discord.HTTPException:
                    failure += 1
                else:
                    success += 1
            else:
                skipped += 1
        return success, failure, skipped

    @commands.command(
        commands.group,
        name='mute',
        description='Mutes members using the configured mute role.',
        invoke_without_command=True,
        guild_only=True,
    )
    @checks.can_mute()
    @commands.permissions(bot=['manage_roles'])
    async def _mute(
            self,
            ctx: ModGuildContext,
            members: commands.Greedy[discord.Member],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Mutes members using the configured mute role.
        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.
        To use this command, you need to be higher than the
        mute role in the hierarchy and have Manage Roles
        permission at the server level.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        assert ctx.guild_config.mute_role_id is not None
        role = discord.Object(id=ctx.guild_config.mute_role_id)
        total = len(members)
        if total == 0:
            raise commands.BadArgument('Missing members to mute.')

        failed = 0
        for member in members:
            try:
                await member.add_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.stick(True, f'Muted [`{total - failed}`/`{total}`] members.')

    @commands.command(
        commands.core_command,
        name='unmute',
        description='Unmutes members using the configured mute role.',
    )
    @checks.can_mute()
    @commands.permissions(bot=['manage_roles'])
    async def _unmute(
            self,
            ctx: ModGuildContext,
            members: commands.Greedy[discord.Member],
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Unmutes members using the configured mute role.
        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.
        To use this command, you need to be higher than the
        mute role in the hierarchy and have Manage Roles
        permission at the server level.
        """

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        assert ctx.guild_config.mute_role_id is not None
        role = discord.Object(id=ctx.guild_config.mute_role_id)
        total = len(members)
        if total == 0:
            raise commands.BadArgument('Missing members to unmute.')

        failed = 0
        for member in members:
            try:
                await member.remove_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.stick(True, f'Unmuted [`{total - failed}`/`{total}`] members.')

    @commands.command(
        commands.hybrid_command,
        name='tempmute',
        description='Temporarily mutes a member for the specified duration.',
    )
    @checks.can_mute()
    @commands.permissions(bot=['manage_roles'])
    @app_commands.describe(duration='The duration to mute the member for. Must be a future Time.',
                           member='The member to mute.',
                           reason='The reason for muting the member.')
    async def tempmute(
            self,
            ctx: ModGuildContext,
            duration: app_commands.Transform[datetime.datetime, timetools.TimeTransformer(future=True)],
            member: discord.Member,
            *,
            reason: Annotated[Optional[str], ActionReason] = None,
    ):
        """Temporarily mutes a member for the specified duration.
        The duration can be a short time form, e.g. 30d or a more human
        duration such as 'until thursday at 3PM' or a more concrete time
        such as '2024-12-31'.

        Note that times are in UTC unless a timezone is specified
        using the 'timezone set' command.

        This has the same permissions as the `mute` command.
        """
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        reminder = self.bot.reminder
        if reminder is None:
            return await ctx.stick(False, 'This functionality is currently not available.')

        assert ctx.guild_config.mute_role_id is not None
        role_id = ctx.guild_config.mute_role_id
        await member.add_roles(discord.Object(id=role_id), reason=reason)

        config = await self.bot.user_settings.get_user_config(ctx.author.id)
        zone = config.timezone if config else None
        await reminder.create_timer(
            duration,
            'tempmute',
            ctx.guild.id,
            ctx.author.id,
            member.id,
            role_id,
            created=ctx.message.created_at,
            timezone=zone or 'UTC',
        )
        await ctx.stick(
            True,
            f'Mute for {discord.utils.escape_mentions(str(member))} ends {discord.utils.format_dt(duration, 'R')}.')

    @commands.Cog.listener()
    async def on_tempmute_timer_complete(self, timer: Timer):
        guild_id, mod_id, member_id, role_id = timer.args
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        member = await self.bot.get_or_fetch_member(guild, member_id)
        if member is None or not member._roles.has(role_id):  # noqa
            await self.send_mute_patch(guild_id, member_id, False)
            return

        if mod_id != member_id:
            moderator = await self.bot.get_or_fetch_member(guild, mod_id)
            if moderator is None:
                try:
                    moderator = await self.bot.fetch_user(mod_id)
                except:  # noqa
                    moderator = f'Mod ID {mod_id}'
                else:
                    moderator = f'{moderator} (ID: {mod_id})'
            else:
                moderator = f'{moderator} (ID: {mod_id})'

            reason = f'Automatic unmute from timer made on {timer.created} by {moderator}.'
        else:
            reason = f'Expiring self-mute made on {timer.created} by {member}'

        try:
            await member.remove_roles(discord.Object(id=role_id), reason=reason)
        except discord.HTTPException:
            await self.send_mute_patch(guild_id, member_id, False)

    @commands.command(
        _mute.group,
        name='role',
        description='Shows configuration of the mute role.',
    )
    @commands.permissions(user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def _mute_role(self, ctx: GuildContext):
        """Shows configuration of the mute role.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """
        config = await self.get_guild_config(ctx.guild.id)
        role = config and config.mute_role
        if role is not None:
            members = config.muted_members.copy()
            members.update(map(lambda r: r.id, role.members))
            total = len(members)
            role = f'{role} (ID: {role.id})'
        else:
            total = 0
        await ctx.stick(True, f'Role: {role}\nMembers Muted: {total}')

    @commands.command(
        _mute_role.command,
        name='set',
        description='Sets the mute role to a pre-existing role.',
        cooldown=commands.CooldownMap(rate=1, per=60.0, type=commands.BucketType.guild)
    )
    @commands.permissions(user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_set(self, ctx: GuildContext, *, role: discord.Role):
        """Sets the mute role to a pre-existing role.
        This command can only be used once every minute.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """

        if role.is_default():
            raise commands.BadArgument('You cannot set the default role as the mute role.')

        if role > ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            raise commands.BadArgument('You cannot set a role higher than your top role as the mute role.')

        if role > ctx.me.top_role:
            raise commands.BadArgument('I cannot set a role higher than my top role as the mute role.')

        config = await self.get_guild_config(ctx.guild.id)
        has_pre_existing = config is not None and config.mute_role is not None
        merge: Optional[bool] = False

        if has_pre_existing:
            msg = (
                '<:warning:1113421726861238363> **There seems to be a pre-existing mute role set up.**\n\n'
                'If you want to merge the pre-existing member data with the new member data press the Merge button.\n'
                'If you want to replace pre-existing member data with the new member data press the Replace button.\n\n'
                '**Note: Merging is __slow__. It will also add the role to every possible member that needs it.**'
            )

            view = PreExistingMuteRoleView(ctx.author._user)  # noqa
            view.message = await ctx.send(msg, view=view)
            await view.wait()
            if view.merge is None:
                return
            merge = view.merge
        else:
            muted_members = len(role.members)
            if muted_members > 0:
                msg = f'<:warning:1113421726861238363> Are you sure you want to make this the mute role? It has {plural(muted_members):member}.'
                confirm = await ctx.prompt(msg)
                if not confirm:
                    merge = None

        if merge is None:
            return

        async with ctx.typing():
            await self.update_mute_role(ctx, config, role, merge=merge)
            escaped = discord.utils.escape_mentions(role.name)
            await ctx.stick(True, f'Successfully set the {escaped} role as the mute role.\n\n'
                                  '**Note: Permission overwrites have not been changed.**')

    @commands.command(
        _mute_role.command,
        name='update',
        description='Updates the permission overwrites of the mute role.',
        aliases=['sync']
    )
    @commands.permissions(user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_update(self, ctx: GuildContext):
        """Automatically updates the permission overwrites of the mute role on the server."""
        config = await self.get_guild_config(ctx.guild.id)
        role = config and config.mute_role
        if role is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        async with ctx.typing():
            success, failure, skipped = await self.update_role_permissions(
                role, ctx.guild, ctx.author._user  # noqa
            )
            total = success + failure + skipped
            await ctx.send(
                f'<:discord_info:1113421814132117545> Attempted to update {total} channel permissions. '
                f'[Updated: {success}, Failed: {failure}, Skipped (no permissions): {skipped}]')

    @commands.command(
        _mute_role.command,
        name='create',
        description='Creates a mute role with the given name.',
    )
    @commands.permissions(user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_create(self, ctx: GuildContext, *, name):
        """Creates a mute role with the given name.
        This also updates the channels' permission overwrites accordingly
        if wanted.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """

        guild_id = ctx.guild.id
        config = await self.get_guild_config(guild_id)
        if config is not None and config.mute_role is not None:
            return await ctx.stick(False, 'A mute role has already been set up.')

        try:
            role = await ctx.guild.create_role(
                name=name, reason=f'Mute Role Created By {ctx.author} (ID: {ctx.author.id})')
        except discord.HTTPException as e:
            return await ctx.stick(False, f'Failed to create role: {e}')

        query = """
            INSERT INTO guild_config (id, mute_role_id)
            VALUES ($1, $2) ON CONFLICT (id)
            DO UPDATE SET
               mute_role_id = EXCLUDED.mute_role_id;
        """
        await ctx.db.execute(query, guild_id, role.id)
        self.get_guild_config.invalidate(self, guild_id)

        confirm = await ctx.prompt(
            '<:warning:1113421726861238363> Would you like to update the channel overwrites as well?')
        if not confirm:
            return await ctx.stick(True, 'Mute role successfully created.')

        async with ctx.typing():
            success, failure, skipped = await self.update_role_permissions(
                role, ctx.guild, ctx.author._user)  # noqa
            await ctx.stick(
                True, f'Mute role successfully created. Overwrites: '
                      f'[Updated: {success}, Failed: {failure}, Skipped: {skipped}]')

    @commands.command(
        _mute_role.command,
        name='unbind',
        aliases=['delete'],
        description='Unbinds a mute role without deleting it.',
    )
    @commands.permissions(user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_unbind(self, ctx: GuildContext):
        """Unbinds a mute role without deleting it.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """
        guild_id = ctx.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None or config.mute_role is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        muted_members = len(config.muted_members)
        if muted_members > 0:
            msg = f'Are you sure you want to unbind and unmute {plural(muted_members):member}?'
            confirm = await ctx.prompt(msg)
            if not confirm:
                return

        query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
        await self.bot.pool.execute(query, guild_id)
        self.get_guild_config.invalidate(self, guild_id)
        await ctx.stick(True, 'Successfully unbound mute role.')

    @commands.command(
        commands.core_command,
        name='selfmute',
        description='Temporarily mutes yourself for the specified duration.',
    )
    @commands.guild_only()
    @commands.permissions(bot=['manage_roles'])
    async def selfmute(
            self,
            ctx: GuildContext,
            *,
            duration: app_commands.Transform[datetime.datetime, timetools.TimeTransformer(short=True)],
    ):
        """Temporarily mutes yourself for the specified duration.
        The duration must be in a short time form, e.g. 4h. Can
        only mute yourself for a maximum of 24 hours and a minimum
        of 5 minutes.
        Don't ask a moderator to unmute you.
        """

        reminder = self.bot.reminder
        if reminder is None:
            return await ctx.stick(False, 'This functionality is currently not available.')

        config = await self.get_guild_config(ctx.guild.id)
        role_id = config and config.mute_role_id
        if role_id is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        if ctx.author._roles.has(role_id):  # noqa
            return await ctx.stick(False, 'You are already muted.')

        created_at = ctx.message.created_at
        if duration > (created_at + datetime.timedelta(days=1)):
            raise commands.BadArgument('Duration is too long. Must be less than 24 hours.')

        if duration < (created_at + datetime.timedelta(minutes=5)):
            raise commands.BadArgument('Duration is too short. Must be at least 5 minutes.')

        delta = timetools.human_timedelta(duration, source=created_at)
        warning = f'Are you sure you want to be muted for {delta}?\n**Do not ask the moderators to undo this!**'
        confirm = await ctx.prompt(warning, ephemeral=True)
        if not confirm:
            return await ctx.send('Aborting', delete_after=5.0)

        reason = f'Self-mute for {ctx.author} (ID: {ctx.author.id}) for {delta}'
        await ctx.author.add_roles(discord.Object(id=role_id), reason=reason)
        await reminder.create_timer(
            duration,
            'tempmute',
            ctx.guild.id,
            ctx.author.id,
            ctx.author.id,
            role_id,
            created=created_at
        )

        fmt_time = discord.utils.format_dt(duration, 'f')
        await ctx.stick(True, f'Selfmute ends **{fmt_time}**.\nBe sure not to bother anyone about it.')


async def setup(bot):
    await bot.add_cog(Mod(bot))
