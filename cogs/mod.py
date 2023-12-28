from __future__ import annotations

import argparse
import asyncio
import datetime
import enum
import io
import re
from collections import Counter, defaultdict
from typing import Optional, Callable, Any, Union, Literal, List, TYPE_CHECKING, MutableMapping, Dict, Generic, TypeVar
from collections.abc import Hashable

import asyncpg
import discord
from asyncpg import Record
from discord import app_commands
from discord.ext import commands, tasks
from typing_extensions import Annotated
from time import time as unix_time

from bot import Percy
from cogs.reminder import Timer
from cogs.utils.paginator import BasePaginator
from launcher import get_logger
from cogs.utils.commands_ext import PermissionTemplate
from .utils import timetools, cache, helpers, commands_ext
from .utils.context import GuildContext
from .utils.converters import Snowflake, IgnoreEntity, get_asset_url
from .utils.formats import plural, human_join
from .utils.helpers import BaseFlags, flag_value
from .utils.constants import IgnoreableEntity
from .utils.timetools import ShortTime

if TYPE_CHECKING:
    class ModGuildContext(GuildContext):
        cog: Mod
        guild_config: GuildConfig

log = get_logger(__name__)


HashableT = TypeVar('HashableT', bound=Hashable)


class Arguments(argparse.ArgumentParser):
    def error(self, message: str):
        raise RuntimeError(message)


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


class LockdownTimer(Timer):
    """A timer for a lockdown event."""
    pass


class GuildConfig:
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
        'dstatus_notification_channel_id',
        'mention_count',
        'safe_automod_entity_ids',
        'mute_role_id',
        'muted_members',
        '_cs_audit_log_webhook',
    )

    bot: Percy
    flags: AutoModFlags
    id: int

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

    @classmethod
    def from_record(cls, record: asyncpg.Record, bot: Percy):
        self = cls()

        # the basic configuration
        self.bot = bot
        self.flags = AutoModFlags(record['flags'] or 0)
        self.id = record['id']
        self.audit_log_channel_id = record['audit_log_channel']
        self.audit_log_flags = record['audit_log_flags'] or {}
        self.audit_log_webhook_url = record['audit_log_webhook_url']
        self.poll_channel_id = record['poll_channel']
        self.poll_ping_role_id = record['poll_ping_role_id']
        self.poll_reason_channel_id = record['poll_reason_channel']
        self.mention_count = record['mention_count']
        self.safe_automod_entity_ids = set(record['safe_automod_entity_ids'] or [])
        self.muted_members = set(record['muted_members'] or [])
        self.mute_role_id = record['mute_role_id']
        return self

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
        return discord.Webhook.from_url(self.audit_log_webhook_url, session=self.bot.session)

    @property
    def requires_migration(self) -> bool:
        return self.audit_log_webhook_url is None and self.audit_log_channel_id is not None

    @property
    def mute_role(self) -> Optional[discord.Role]:
        guild = self.bot.get_guild(self.id)
        return guild and self.mute_role_id and guild.get_role(self.mute_role_id)  # type: ignore

    def is_muted(self, member: discord.abc.Snowflake) -> bool:
        return member.id in self.muted_members

    async def apply_mute(self, member: discord.Member, reason: Optional[str]):
        if self.mute_role_id:
            await member.add_roles(discord.Object(id=self.mute_role_id), reason=reason)


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


def can_execute_action(ctx: GuildContext, user: discord.Member, target: discord.Member) -> bool:
    return user.id == ctx.bot.owner_id or user == ctx.guild.owner or user.top_role > target.top_role


class MemberID(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str):  # noqa
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                member_id = int(argument, base=10)
            except ValueError:
                raise commands.BadArgument(ctx.tick(False, f'{argument} is not a valid member or member ID.')) from None
            else:
                m = await ctx.bot.get_or_fetch_member(ctx.guild, member_id)
                if m is None:
                    return type('_Hackban', (), {'id': member_id, '__str__': lambda s: f'Member ID {s.id}'})()

        if not can_execute_action(ctx, ctx.author, m):
            raise commands.BadArgument(ctx.tick(False, 'You cannot do this action on this user due to role hierarchy.'))
        return m


class BannedMember(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str):  # noqa
        if argument.isdigit():
            member_id = int(argument, base=10)
            try:
                return await ctx.guild.fetch_ban(discord.Object(id=member_id))
            except discord.NotFound:
                raise commands.BadArgument(ctx.tick(False, 'This member has not been banned before.')) from None

        entity = await discord.utils.find(lambda u: str(u.user) == argument, ctx.guild.bans(limit=None))

        if entity is None:
            raise commands.BadArgument(ctx.tick(False, 'This member has not been banned before.'))
        return entity


class ActionReason(commands.Converter):
    async def convert(self, ctx: GuildContext, argument: str) -> str:  # noqa
        ret = f'{ctx.author} (ID: {ctx.author.id}): {argument}'

        if len(ret) > 512:
            reason_max = 512 - len(ret) + len(argument)
            raise commands.BadArgument(ctx.tick(False, f'Reason is too long ({len(argument)}/{reason_max})'))
        return ret


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


class Confirm(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.gray,
                       emoji=discord.PartialEmoji(name='yes', id=1066772402270371850, animated=True))
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await interaction.message.delete()
        self.value = True
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.gray,
                       emoji=discord.PartialEmoji(name='declined', id=1066183072984350770, animated=True))
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        await interaction.message.delete()
        self.value = False
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


class MultipleSpammers(SpamCheckerResult):
    def __init__(self, members: list[discord.abc.Snowflake], *, reason: str = 'Auto-ban for spamming') -> None:
        super().__init__(reason)
        self.members: list[discord.abc.Snowflake] = members


class TaggedCooldown(commands.Cooldown, Generic[HashableT]):
    def __init__(self, rate: int, per: float) -> None:
        super().__init__(rate, per)
        self.tagged: set[HashableT] = set()

    def update_rate_limit(self, current: float | None = None, *, tokens: int = 1) -> float | None:
        current = current or unix_time()
        self._last = current

        self._tokens = self.get_tokens(current)

        if self._tokens == self.rate:
            self.tagged.clear()
            self._window = current

        self._tokens -= tokens

        if self._tokens < 0:
            return self.per - (current - self._window)

    def reset(self) -> None:
        super().reset()
        self.tagged.clear()

    def copy(self) -> TaggedCooldown:
        return TaggedCooldown(self.rate, self.per)

    def __repr__(self) -> str:
        return f'<TaggedCooldown rate: {self.rate} per: {self.per} tokens: {self._tokens} tagged: {self.tagged!r}>'


class TaggedCooldownMapping(commands.CooldownMapping[discord.Message], Generic[HashableT]):
    _cache: dict[Any, TaggedCooldown[HashableT]]
    _cooldown: TaggedCooldown[HashableT]

    def __init__(
        self,
        rate: int,
        per: float,
        type: Callable[[discord.Message], Any],
        *,
        tagger: Callable[[discord.Message], HashableT],
    ):
        super().__init__(TaggedCooldown(rate, per), type)
        self.tagger: Callable[[discord.Message], HashableT] = tagger

    def get_bucket(self, message: discord.Message, current: float | None = None) -> TaggedCooldown[HashableT] | None:
        return super().get_bucket(message, current)  # type: ignore

    def update_rate_limit(self, message: discord.Message, current: float | None = None, tokens: int = 1) -> float | None:
        bucket = self.get_bucket(message, current)
        if bucket is None:
            return None

        retry_after = bucket.update_rate_limit(current, tokens=tokens)
        bucket.tagged.add(self.tagger(message))
        return retry_after

    def copy(self) -> TaggedCooldownMapping[HashableT]:
        mapping = TaggedCooldownMapping(self._cooldown.rate, self._cooldown.per, self._type, tagger=self.tagger)
        mapping._cache = self._cache.copy()
        return mapping


class CooldownByContent(commands.CooldownMapping):
    def _bucket_key(self, message: discord.Message) -> tuple[int, str]:
        return message.channel.id, message.content


class MemberJoinType(enum.Enum):
    fast = 1
    suspicious = 2


class SpamChecker:
    """This spam checker does a few things.

    1) It checks if a user has spammed more than 10 times in 12 seconds
    2) It checks if the content has been spammed 15 times in 17 seconds.
    3) It checks if new users have spammed 30 times in 35 seconds.
    4) It checks if 'fast joiners' have spammed 10 times in 12 seconds.
    5) It checks if a member spammed `config.mention_count * 2` mentions in 12 seconds.

    The second case is meant to catch alternating spambots while the first one
    just catches regular singular spambots.
    From experience, these values aren't reached unless someone is actively spamming.
    """

    def __init__(self):
        self.by_content = CooldownByContent.from_cooldown(15, 17.0, commands.BucketType.member)
        self.by_user = commands.CooldownMapping.from_cooldown(10, 12.0, commands.BucketType.user)
        self.last_join: Optional[datetime.datetime] = None
        self.last_member: Optional[discord.Member] = None
        self.new_user = commands.CooldownMapping.from_cooldown(30, 35.0, commands.BucketType.channel)
        self._by_mentions: Optional[commands.CooldownMapping] = None
        self._by_mentions_rate: Optional[int] = None

        self.last_created: Optional[datetime.datetime] = None

        self.flagged_users: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        self.hit_and_run = commands.CooldownMapping.from_cooldown(10, 12, commands.BucketType.channel)

    def get_flagged_member(self, user_id: int, /) -> Optional[FlaggedMember]:
        return self.flagged_users.get(user_id)

    def flag_member(self, member: discord.Member, /) -> None:
        self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())

    def by_mentions(self, config: GuildConfig) -> Optional[commands.CooldownMapping]:
        if not config.mention_count:
            return None

        mention_threshold = config.mention_count
        if self._by_mentions_rate != mention_threshold:
            self._by_mentions = commands.CooldownMapping.from_cooldown(mention_threshold, 15,
                                                                       commands.BucketType.member)
            self._by_mentions_rate = mention_threshold
        return self._by_mentions

    @staticmethod
    def is_new(member: discord.Member) -> bool:
        now = discord.utils.utcnow()
        seven_days_ago = now - datetime.timedelta(days=7)
        ninety_days_ago = now - datetime.timedelta(days=90)
        return member.created_at > ninety_days_ago and member.joined_at is not None and member.joined_at > seven_days_ago

    def is_spamming(self, message: discord.Message) -> Optional[SpamCheckerResult]:
        if message.guild is None:
            return None

        current = message.created_at.timestamp()

        flagged = self.flagged_users.get(message.author.id)
        if flagged is not None:
            flagged.messages += 1
            bucket = self.hit_and_run.get_bucket(message)
            if bucket and bucket.update_rate_limit(current):
                return MultipleSpammers(list(bucket.tagged))

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
                return MemberJoinType.fast

        is_suspicious = abs((member.created_at - self.last_member.created_at).total_seconds()) <= 86400.0
        if is_suspicious:
            self.flagged_users[member.id] = FlaggedMember(member, joined)
            if self.last_member.id not in self.flagged_users:
                self.flag_member(self.last_member)
            return MemberJoinType.suspicious

        return None

    def is_mention_spam(self, message: discord.Message, config: GuildConfig) -> bool:
        mapping = self.by_mentions(config)
        if mapping is None:
            return False

        current = message.created_at.timestamp()
        mention_bucket = mapping.get_bucket(message, current)
        mention_count = sum(not m.bot and m.id != message.author.id for m in message.mentions)
        return mention_bucket is not None and mention_bucket.update_rate_limit(current,
                                                                               tokens=mention_count) is not None

    def remove_member(self, user: discord.abc.User) -> None:
        self.flagged_users.pop(user.id, None)


class NoMuteRole(commands.CommandError):
    def __init__(self):
        super().__init__('This server does not have a mute role set up.')


def can_mute():
    async def predicate(ctx: ModGuildContext) -> bool:
        is_owner = await ctx.bot.is_owner(ctx.author)
        if ctx.guild is None:
            return False

        if not ctx.author.guild_permissions.manage_roles and not is_owner:
            return False

        ctx.guild_config = config = await ctx.cog.get_guild_config(ctx.guild.id)  # type: ignore
        role = config and config.mute_role
        if role is None:
            raise NoMuteRole()
        return ctx.author.top_role > role

    return commands.check(predicate)


class Mod(commands.Cog):
    """Utility commands for moderation."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self._spam_check: defaultdict[int, SpamChecker] = defaultdict(SpamChecker)

        self._data_batch: defaultdict[int, list[tuple[int, Any]]] = defaultdict(list)
        self._batch_lock = asyncio.Lock()
        self._disable_lock = asyncio.Lock()
        self.batch_updates.add_exception_type(asyncpg.PostgresConnectionError)
        self.batch_updates.start()

        self.message_batches: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
        self._batch_message_lock = asyncio.Lock()
        self.bulk_send_messages.start()

    def __repr__(self) -> str:
        return '<cogs.Mod>'

    async def cog_load(self) -> None:
        self._avatar: bytes = await self.bot.user.display_avatar.read()

    def cog_unload(self) -> None:
        self.batch_updates.stop()
        self.bulk_send_messages.stop()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='alumni_mod_animated', id=1076913120599080970, animated=True)

    async def bulk_insert(self):
        query = """
            UPDATE guild_config
                SET muted_members = x.result_array
            FROM jsonb_to_recordset($1::jsonb) AS x(guild_id BIGINT, result_array BIGINT[])
            WHERE guild_config.id = x.guild_id;
        """

        if not self._data_batch:
            return

        final_data = []
        for guild_id, data in self._data_batch.items():
            config = await self.get_guild_config(guild_id)

            if config is None:
                continue

            as_set = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({'guild_id': guild_id, 'result_array': list(as_set)})
            self.get_guild_config.invalidate(self, guild_id)

        await self.bot.pool.execute(query, final_data)
        self._data_batch.clear()

    @tasks.loop(seconds=15.0)
    async def batch_updates(self):
        async with self._batch_lock:
            await self.bulk_insert()

    @tasks.loop(seconds=10.0)
    async def bulk_send_messages(self):
        async with self._batch_message_lock:
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
                return GuildConfig.from_record(record, self.bot)
            return None

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

        if isinstance(result, MultipleSpammers):
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

    async def ban_for_mention_spam(
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
        except Exception as e:
            log.info('[Mention Spam] Failed to ban member %s (ID: %s) in guild ID %s', member, member.id, guild_id)
        else:
            to_send = f'<:discord_info:1113421814132117545> Banned **{member}** (ID: `{member.id}`) for spamming `{mention_count}` mentions.'
            async with self._batch_message_lock:
                self.message_batches[(guild_id, message.channel.id)].append(to_send)

            log.info('[Mention Spam] Member %s (ID: %s) has been banned from guild ID %s', member, member.id, guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if author.id in (self.bot.user.id, self.bot.owner_id):
            return

        if message.guild is None:
            return

        if not isinstance(author, discord.Member):
            return

        if author.bot:
            return

        if author.guild_permissions.manage_messages:
            return

        guild_id = message.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if message.channel.id in config.safe_automod_entity_ids:
            return

        if author.id in config.safe_automod_entity_ids:
            return

        if any(i in config.safe_automod_entity_ids for i in author._roles):
            return

        await self.check_raid(config, message.guild, author, message)

        if not config.mention_count:
            return

        checker = self._spam_check[guild_id]
        if checker.is_mention_spam(message, config):
            await self.ban_for_mention_spam(config.mention_count, guild_id, message, author, multiple=True)
            return

        if len(message.mentions) <= 3:
            return

        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        await self.ban_for_mention_spam(mention_count, guild_id, message, author)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = member.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None:
            return

        if config.is_muted(member):
            return await config.apply_mute(member, 'Member was previously muted.')

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        checker = self._spam_check.get(payload.guild_id)
        if checker is None:
            return

        checker.remove_member(payload.user)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
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

        async with self._batch_lock:
            self._data_batch[guild_id].append((after.id, after_has))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild_id = role.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None or config.mute_role_id != role.id:
            return

        query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
        await self.bot.pool.execute(query, guild_id)
        self.get_guild_config.invalidate(self, guild_id)

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    @app_commands.describe(duration='The slowmode duration or 0s to disable')
    @app_commands.guild_only()
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

    @commands_ext.command(
        commands.hybrid_group,
        name='moderation',
        aliases=['mod'],
        fallback='info',
        description='Show current Moderation (automatic moderation) behaviour on the server.'
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
    async def moderation(self, ctx: GuildContext):
        """Show current Moderation (Automatic Moderation) behavior on the server.
        You must have Ban Members and Manage Messages permissions to use this
        command or its subcommands.
        """

        config: GuildConfig = await self.get_guild_config(ctx.guild.id)
        if config is None:
            return await ctx.stick(False, 'This server does not have Moderation set up.')

        e = discord.Embed(title=f'{ctx.guild.name} Moderation',
                          timestamp=datetime.datetime.utcnow(),
                          color=helpers.Colour.darker_red())
        e.set_thumbnail(url=get_asset_url(ctx.guild))

        if config.flags.audit_log:
            channel = f'<#{config.audit_log_channel_id}>'
            if config.requires_migration:
                audit_log_broadcast = (
                    f'{channel}\n\n\N{WARNING SIGN}\ufe0f '
                    'This server requires migration for this feature to continue working.\n'
                    f'Run "`{ctx.prefix}moderation disable Audit Logging`" followed by "`{ctx.prefix}moderation auditlog channel {channel}`" '
                    'to ensure this feature continues working!'
                )
            else:
                audit_log_broadcast = f'Bound to {channel}'
        else:
            audit_log_broadcast = '*Disabled*'

        e.add_field(name='Audit Log', value=audit_log_broadcast)
        e.add_field(name='Raid Protection', value='Enabled' if config.flags.raid else '*Disabled*')

        mention_spam = f'{config.mention_count} mentions' if config.mention_count else '*Disabled*'
        e.add_field(name='Mention Spam Protection', value=mention_spam)

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

        e.add_field(name='Ignored Entities', value=ignored, inline=False)

        await ctx.send(embed=e)

    @commands_ext.command(
        moderation.group,
        name='auditlog',
        fallback='channel',
        description='Toggles audit text log on the server.'
    )
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
    @app_commands.describe(
        channel='The channel to broadcast audit log messages to. The bot must be able to create webhooks in it.'
    )
    async def moderation_auditlog(self, ctx: GuildContext, *, channel: discord.TextChannel):
        """Toggles audit text log on the server.
        Audit Log sends a message to the log channel whenever a certain event is triggered.
        """

        await ctx.defer()
        config: GuildConfig = await self.get_guild_config(ctx.guild.id)
        if config.flags.audit_log:
            await ctx.stick(False, f'You already have audit logging enabled. To disable, use "`{ctx.prefix}moderation disable Audit Logging`"')
            return

        reason = f'{ctx.author} (ID: {ctx.author.id}) enabled Moderation audit log'

        try:
            webhook = await channel.create_webhook(name='Moderation Audit Log', avatar=self._avatar, reason=reason)
        except discord.Forbidden:
            await ctx.stick(False, f'The bot does not have permissions to create webhooks in {channel.mention}.')
            return
        except discord.HTTPException:
            await ctx.stick(False, 'An error occurred while creating the webhook. Note you can only have 10 webhooks per channel.')
            return

        query = """
            INSERT INTO guild_config (id, flags, audit_log_channel, audit_log_webhook_url, audit_log_flags)
                VALUES ($1, $2, $3, $4, DEFAULT) ON CONFLICT (id)
                DO UPDATE SET
                   flags = guild_config.flags | EXCLUDED.flags,
                   audit_log_channel = EXCLUDED.audit_log_channel,
                   audit_log_webhook_url = EXCLUDED.audit_log_webhook_url,
                   audit_log_flags = DEFAULT;
        """

        flags = AutoModFlags()
        flags.audit_log = True
        await ctx.db.execute(query, ctx.guild.id, flags.value, channel.id, webhook.url)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, f'Audit log enabled. Broadcasting log events to <#{channel.id}>.')

    @commands_ext.command(
        moderation_auditlog.command,
        name='alter',
        description='Configures the audit log events.',
    )
    @app_commands.describe(flag='The flag you want to set.',
                           value='The value you want to set the flag to.')
    async def moderation_auditlog_alter(self, ctx: GuildContext, flag: str, value: bool):
        """Configures the audit log events.
        You can set the Events you want to get notified about via the Audit Log Channel.
        """
        config: GuildConfig = await self.get_guild_config(ctx.guild.id)
        if not config.flags.audit_log:
            return await ctx.stick(False, 'You do not have audit logging enabled. To enable, use `moderation auditlog`.')

        if flag == 'all':
            for key in config.audit_log_flags:
                config.audit_log_flags[key] = value
            content = f'Set all Audit Log Events to `{value}`.'
        else:
            if flag in config.audit_log_flags:
                config.audit_log_flags[flag] = value
                content = f'Set Audit Log Event **{flag}** to `{value}`.'
            else:
                return await ctx.stick(False, 'That is not a valid flag.')

        query = "UPDATE guild_config SET audit_log_flags = $2 WHERE id = $1;"
        await ctx.db.execute(query, ctx.guild.id, config.audit_log_flags)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.stick(True, content)

    @moderation_auditlog_alter.autocomplete('flag')
    async def moderation_auditlog_alter_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT audit_log_flags AS flags FROM guild_config WHERE id = $1;"
        results: Record = await self.bot.pool.fetchrow(query, interaction.guild_id)

        flags: dict[str, bool] = results.get('flags')
        choices = [app_commands.Choice(name='All Logs', value='all')]

        if current:
            choices.extend(
                [app_commands.Choice(name=f'{flag} - {state}', value=flag)
                 for flag, state in flags.items() if current.lower() in flag]
            )
        else:
            choices.extend(
                [app_commands.Choice(name=f'{flag} - {state}', value=flag) for flag, state in flags.items()]
            )
        return choices

    @commands_ext.command(
        moderation.command,
        name='disable',
        description='Disables Moderation on the server.',
    )
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
    @app_commands.describe(protection='The protection to disable')
    @app_commands.choices(
        protection=[
            app_commands.Choice(name='Everything', value='all'),
            app_commands.Choice(name='Join logging', value='joins'),
            app_commands.Choice(name='Raid protection', value='raid'),
            app_commands.Choice(name='Mention spam protection', value='mentions'),
            app_commands.Choice(name='Audit Logging', value='auditlog'),
        ]
    )
    async def moderation_disable(self, ctx: GuildContext, *,
                                 protection: Literal['all', 'joins', 'raid', 'mentions', 'auditlog'] = 'all'):
        """Disables Moderation on the server.
        This can be one of these settings:
        - 'all' to disable everything
        - 'joins' to disable join logging
        - 'raid' to disable raid protection
        - 'mentions' to disable mention spam protection
        - 'auditlog' to disable audit logging
        If not given then it defaults to 'all'.
        """

        if protection == 'all':
            updates = 'flags = 0, mention_count = 0, broadcast_channel = NULL, audit_log_channel = NULL'
            message = 'Moderation has been disabled.'
        elif protection == 'raid':
            updates = f'flags = guild_config.flags & ~{AutoModFlags.raid.flag}'
            message = 'Raid protection has been disabled.'
        elif protection == 'mentions':
            updates = 'mention_count = NULL'
            message = 'Mention spam protection has been disabled'
        elif protection == 'auditlog':
            updates = f'flags = guild_config.flags & ~{AutoModFlags.audit_log.flag}'
            updates += ', audit_log_channel = NULL, audit_log_flags = NULL'
            message = 'Audit logging has been disabled.'
        else:
            raise commands.BadArgument(f'Unknown protection {protection}')

        query = f'UPDATE guild_config SET {updates} WHERE id=$1 RETURNING audit_log_webhook_url'

        guild_id = ctx.guild.id
        records = await self.bot.pool.fetchrow(query, guild_id)
        self._spam_check.pop(guild_id, None)
        self.get_guild_config.invalidate(self, guild_id)
        hooks: list = []
        if protection in ('auditlog', 'all'):
            hooks.append([records.get('audit_log_webhook_url', None), 'Audit Log Webhook'])
        for record in hooks:
            if record[0]:
                wh = discord.Webhook.from_url(record[0], session=self.bot.session)
                try:
                    await wh.delete(reason=message)  # noqa
                except discord.HTTPException:
                    await ctx.send(
                        f'<:warning:1113421726861238363> The webhook `{record[1]}` could not be deleted for some reason.')

        await ctx.stick(True, message)

    @commands_ext.command(
        moderation.command,
        name='raid',
        description='Toggles raid protection on the server.',
    )
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
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
                -- If we're toggling then we need to negate the previous result
                flags = CASE COALESCE($3, NOT (guild_config.flags & $2 = $2))
                                WHEN TRUE THEN guild_config.flags | $2
                                WHEN FALSE THEN guild_config.flags & ~$2
                        END
            RETURNING COALESCE($3, (flags & $2 = $2));
        """

        row: Optional[tuple[bool]] = await ctx.db.fetchrow(query, ctx.guild.id, AutoModFlags.raid.flag, enabled)
        enabled = row and row[0]
        self.get_guild_config.invalidate(self, ctx.guild.id)
        fmt = '*enabled*' if enabled else '*disabled*'
        await ctx.stick(True, f'Raid protection {fmt}.')

    @commands_ext.command(
        moderation.command,
        name='mentions',
        description='Enables auto-banning accounts that spam more than \'count\' mentions.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod)
    @app_commands.describe(count='The maximum amount of mentions before banning.')
    async def moderation_mentions(self, ctx: GuildContext, count: commands.Range[int, 3]):
        """Enables auto-banning accounts that spam more than 'count' mentions.
        If a message contains `count` or more mentions then the
        bot will automatically attempt to auto-ban the member.
        The `count` must be greater than 3.
        This only applies for user mentions. Everyone or Role
        mentions aren't included.
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
            await ctx.stick(False, 'Mention spam protection threshold must be greater than three.')

    @commands_ext.command(
        moderation.command,
        name='ignore',
        description='Specifies what roles, members, or channels ignore Moderation Inspections.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['ban_members'])
    @app_commands.describe(entities='Space separated list of roles, members, or channels to ignore')
    async def moderation_ignore(
            self, ctx: GuildContext, entities: Annotated[List[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ):
        """Specifies what roles, members, or channels ignore Moderation auto-bans.
        To use this command, you must have the Ban Members permission.
        """

        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
               ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_automod_entity_ids, '{}') || $2::bigint[]))
            WHERE id = $1;
        """

        if len(entities) == 0:
            return await ctx.stick(False, 'Missing entities to ignore.')

        ids = [c.id for c in entities]
        await ctx.db.execute(query, ctx.guild.id, ids)
        self.get_guild_config.invalidate(self, ctx.guild.id)
        await ctx.send(
            f'<:discord_info:1113421814132117545> Updated ignore list to ignore {', '.join(c.mention for c in entities)}',
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands_ext.command(
        moderation.command,
        name='unignore',
        description='Specifies what roles, members, or channels to take off the ignore list.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['ban_members'])
    @app_commands.describe(entities='Space separated list of roles, members, or channels to take off the ignore list')
    async def moderation_unignore(
            self, ctx: GuildContext, entities: Annotated[List[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ):
        """Specifies what roles, members, or channels to take off the Moderation ignore list.
        To use this command, you must have the Ban Members permission.
        """

        if len(entities) == 0:
            return await ctx.stick(False, 'Missing entities to unignore.')

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
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands_ext.command(
        moderation.command,
        name='ignored',
        description='Lists what channels, roles, and members are in the Moderation ignore list.',
    )
    @commands.guild_only()
    async def moderation_ignored(self, ctx: GuildContext):
        """Lists what channels, roles, and members are in the Moderation ignore list"""

        config = await self.get_guild_config(ctx.guild.id)
        if config is None or not config.safe_automod_entity_ids:
            return await ctx.stick(False, 'Nothing is ignored!')

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
                embed = discord.Embed(timestamp=datetime.datetime.utcnow(), color=self.colour)
                embed.set_author(name=f'Ignored Entities', icon_url=get_asset_url(ctx.guild))
                embed.set_footer(text=f'{plural(len(entities)):entity|entities}')
                embed.description = '\n'.join(entries)
                return embed

        await EmbedPaginator.start(ctx, entries=entities, per_page=15)

    @commands_ext.command(
        commands.hybrid_command,
        name='purge',
        description='Removes messages that meet a criteria.',
        aliases=['clear'],
        usage='[search]'
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['manage_messages'], bot=['manage_messages'])
    @app_commands.describe(search='How many messages to search for')
    async def purge(
            self, ctx: GuildContext, search: Optional[commands.Range[int, 1, 2000]] = None, *, flags: PurgeFlags
    ):
        """Removes messages that meet a criteria.
        This command uses a syntax similar to Discord's search bar.
        The messages are only deleted if all options are met unless
        the `--require` flag is passed to override the behaviour.

        In order to use this command, you must have Manage Messages permissions.
        Note that the bot needs Manage Messages as well. These commands can't
        be used in a private message.
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
                return await ctx.stick(False, f'E: **{e}** Maybe your search was too big?')

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
            query = """SELECT channel_id, allow, deny FROM guild_lockdowns WHERE guild_id=$1;"""
            rows = await self.bot.pool.fetch(query, guild_id)
        else:
            query = """SELECT channel_id, allow, deny
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
        default_role = ctx.guild.default_role

        records = []
        success, failures = [], []
        reason = f'Lockdown request by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            for channel in channels:
                ow = channel.overwrites_for(default_role)
                allow, deny = ow.pair()
                ow.update(
                    send_messages=False,
                    connect=False,
                    add_reactions=False,
                    use_application_commands=False,
                    create_private_threads=False,
                    create_public_threads=False,
                    send_messages_in_threads=False,
                )

                try:
                    await channel.set_permissions(default_role, overwrite=ow, reason=reason)
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
            FROM jsonb_to_recordset($1::jsonb) AS d(guild_id BIGINT, channel_id BIGINT, allow BIGINT, deny BIGINT)
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
        get_channel = guild.get_channel
        http_fallback: Optional[dict[int, discord.abc.GuildChannel]] = None
        default_role = guild.default_role
        failures = []
        lockdowns = await self.get_lockdown_information(guild.id, channel_ids=channel_ids)
        for channel_id, permissions in lockdowns.items():
            channel = get_channel(channel_id)
            if channel is None:
                if http_fallback is None:
                    http_fallback = {c.id: c for c in await guild.fetch_channels()}
                    get_channel = http_fallback.get
                    channel = get_channel(channel_id)
                    if channel is None:
                        continue
                continue

            try:
                await channel.set_permissions(default_role, overwrite=permissions, reason=reason)
            except discord.HTTPException:
                failures.append(channel)

        return failures

    async def check_active_lockdown(self, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        query = """SELECT * FROM guild_lockdowns WHERE guild_id=$1 AND channel_id=$2;"""
        row = await self.bot.pool.fetchrow(query, guild.id, channel.id)
        if row:
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

    @commands_ext.command(
        commands.hybrid_group,
        name='lockdown',
        fallback='start',
        description='Locks down specific channels.',
    )
    @commands.guild_only()
    @app_commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    @commands.cooldown(1, 30.0, commands.BucketType.guild)
    @app_commands.describe(channels='A space separated list of text or voice channels to lock down')
    async def lockdown(
            self,
            ctx: GuildContext,
            channels: commands.Greedy[Union[discord.TextChannel, discord.VoiceChannel]]
    ):
        """Locks down specific channels.
        A lockdown is done by forbidding users from communicating with the channels.
        This is implemented by blocking certain permissions for the default everyone
        role:
        - Send Messages
        - Add Reactions
        - Use Application Commands
        - Create Public Threads
        - Create Private Threads
        - Send Messages in Threads
        When the lockdown is over, the permissions are reverted into their previous
        state.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """

        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                await ctx.send(embed=discord.Embed(title='Error',
                                                   description='For some reason, I could not find an an appropriate channel to edit overwrites for.'
                                                               'Note that this lockdown will potentially lock the bot from sending messages. '
                                                               'Please explicitly give the bot permissions to send messages in threads and channels.',
                                                   color=discord.Color.red()))
                return

            view = LockdownPermissionIssueView(ctx.me, parent)
            view.message = await ctx.send(
                embed=discord.Embed(title='Warning',
                                    description='<:warning:1113421726861238363> This will potentially lock the bot from sending messages.\n'
                                                'Would you like to resolve the permission issue?',
                                    color=discord.Color(0xF8DB5E)),
                view=view
            )
            await view.wait()
            if view.abort:
                return
            ctx = await self.bot.get_context(view.message, cls=GuildContext)

        success, failures = await self.start_lockdown(ctx, channels)
        if failures:
            message = (
                f'Successfully locked down `{len(success)}`/`{len(failures)}` channels.\n'
                f'Failed channels: {', '.join(c.mention for c in failures)}\n\n'
                f'Failed channels: {', '.join(c.mention for c in failures)}\n\n'
                f'Give the bot Manage Roles permissions in those channels and try again.'
            )
        else:
            message = f'**{plural(len(success)):channel}** were successfully locked down.'

        embed = discord.Embed(title='Locked down', description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands_ext.command(
        lockdown.command,
        name='for',
        description='Locks down specific channels for a specified amount of timetools.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    @commands.cooldown(1, 30.0, commands.BucketType.guild)
    @app_commands.describe(
        duration='A duration on how long to lock down for, e.g. 30m',
        channels='A space separated list of text or voice channels to lock down',
    )
    async def lockdown_for(
            self,
            ctx: GuildContext,
            duration: timetools.ShortTime,
            channels: commands.Greedy[Union[discord.TextChannel, discord.VoiceChannel]],
    ):
        """Locks down specific channels for a specified amount of timetools.
        A lockdown is done by forbidding users from communicating with the channels.
        This is implemented by blocking certain permissions for the default everyone
        role:
        - Send Messages
        - Add Reactions
        - Use Application Commands
        - Create Public Threads
        - Create Private Threads
        - Send Messages in Threads
        When the lockdown is over, the permissions are reverted into their previous
        state.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """

        reminder = self.bot.reminder
        if reminder is None:
            return await ctx.stick(False, 'Sorry, this functionality is currently unavailable. Try again later?')

        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                await ctx.send(embed=discord.Embed(title='Error',
                                                   description='For some reason, I could not find an an appropriate channel to edit overwrites for.'
                                                               'Note that this lockdown will potentially lock the bot from sending messages. '
                                                               'Please explicitly give the bot permissions to send messages in threads and channels.',
                                                   color=discord.Color.red()))
                return

            view = LockdownPermissionIssueView(ctx.me, parent)
            view.message = await ctx.send(
                embed=discord.Embed(title='Warning',
                                    description='<:warning:1113421726861238363> This will potentially lock the bot from sending messages.\n'
                                                'Would you like to resolve the permission issue?',
                                    color=discord.Color.yellow()),
                view=view
            )
            await view.wait()
            if view.abort:
                return
            ctx = await self.bot.get_context(view.message, cls=GuildContext)

        success, failures = await self.start_lockdown(ctx, channels)
        timer = await reminder.create_timer(
            duration.dt,
            'lockdown',
            ctx.guild.id,
            ctx.author.id,
            ctx.channel.id,
            [c.id for c in success],
            created=ctx.message.created_at,
        )
        long = duration.dt >= ctx.message.created_at + datetime.timedelta(days=1)
        formatted_time = discord.utils.format_dt(timer.expires, 'f' if long else 'T')  # type: ignore

        if failures:
            message = (
                f'Successfully locked down `{len(success)}`/`{len(channels)}` channels until {formatted_time}.\n'
                f'Failed channels: {', '.join(c.mention for c in failures)}\n'
                f'Give the bot Manage Roles permissions in {plural(len(failures)):the channel|those channels} and try '
                f'the lockdown command on the failed **{plural(len(failures)):channel}** again.'
            )
        else:
            message = f'**{plural(len(success)):Channel}** were successfully locked down until {formatted_time}.'

        embed = discord.Embed(title='Locked down', description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands_ext.command(
        lockdown.command,
        name='end',
        description='Ends all lockdowns set.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=PermissionTemplate.mod, bot=['manage_roles'])
    async def lockdown_end(self, ctx: GuildContext):
        """Ends all set lockdowns.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """

        if not await self.check_active_lockdown(ctx.guild, ctx.channel):
            return await ctx.stick(False, 'This channel is currently not on lock down.')

        reason = f'Lockdown ended by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            failures = await self.end_lockdown(ctx.guild, reason=reason)

        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        if failures:
            formatted = [c.mention for c in failures]
            await ctx.send(
                f'<:discord_info:1113421814132117545> Lockdown ended. Failed to edit {human_join(formatted, final='and')}')
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

    @commands_ext.command(
        commands.command,
        name='cleanup',
        description='Cleans up only the bot\'s messages from the channel.',
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

    @commands_ext.command(
        commands.command,
        name='kick',
        description='Kicks a member from the server.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, user=['kick_members'], bot=['kick_members'])
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

    @commands_ext.command(
        commands.command,
        name='ban',
        description='Bans a member from the server.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, user=['ban_members'], bot=['ban_members'])
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

    @commands_ext.command(
        commands.command,
        name='multiban',
        description='Bans multiple members from the server.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, user=['ban_members'], bot=['ban_members'])
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
            return await ctx.stick(False, 'Missing members to ban.')

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

    @commands_ext.command(
        commands.hybrid_command,
        name='massban',
        description='Mass bans multiple members from the server.'
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['ban_members'], bot=['ban_members'])
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
                    return await ctx.stick(False, f'Invalid regex passed to `match:` flag: {e}')
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
            lambda m: isinstance(m, discord.Member) and can_execute_action(ctx, author, m),  # Only if applicable
            lambda m: not m.bot,  # No bots
            lambda m: m.discriminator != '0000',  # No deleted users
        ]

        if flags.username:
            try:
                _regex = re.compile(flags.username)
            except re.error as e:
                return await ctx.stick(False, f'Invalid regex passed to `username:` flag: {e}')
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
            return await ctx.send('Missing at least one filter to use')

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
            return await ctx.stick(False, 'No members found matching criteria.')

        if flags.show:
            members = sorted(members.values(), key=lambda m: m.joined_at or now)
            fmt = '\n'.join(f'ID: {m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\tMember: {m}' for m in members)
            content = f'- Current Time: {discord.utils.utcnow()}\n- Total members: {len(members)}\n\n{fmt}'
            file = discord.File(io.BytesIO(content.encode('utf-8')), filename='members.txt')
            return await ctx.send(file=file)

        if flags.reason is None:
            return await ctx.stick(False, '`--reason` flag is required.')
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

    @commands_ext.command(
        commands.command,
        name='softban',
        description='Soft bans a member from the server.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, user=['kick_members'], bot=['kick_members'])
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
        await ctx.stick(True, f'Successfully softbanned **{member}**.')

    @commands_ext.command(
        commands.command,
        name='unban',
        description='Unbans a member from the server.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, user=['ban_members'], bot=['ban_members'])
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
            await ctx.stick(True, f'Unbanned {member.user} (ID: `{member.user.id}`), previously banned for **{member.reason}**.')
        else:
            await ctx.stick(True, f'Unbanned {member.user} (ID: `{member.user.id}`).')

    @commands_ext.command(
        commands.hybrid_command,
        name='tempban',
        description='Temporarily bans a member for the specified duration.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['ban_members'], bot=['ban_members'])
    @app_commands.describe(duration='The duration to ban the member for. Must be a future Time.',
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
            return await ctx.stick(False, 'Sorry, this functionality is currently unavailable. Try again later?')

        until = f'until {discord.utils.format_dt(duration, 'F')}'
        heads_up_message = f'<:discord_info:1113421814132117545> You have been banned from {ctx.guild.name} {until}. Reason: {reason}'

        try:
            await member.send(heads_up_message)  # type: ignore  # Guarded by AttributeError
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
            except:
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

    @staticmethod
    async def update_mute_role_permissions(
            role: discord.Role, guild: discord.Guild, invoker: discord.abc.User
    ) -> tuple[int, int, int]:
        success, failure, skipped = 0, 0, 0

        reason = f'Action done by {invoker} (ID: {invoker.id})'
        for channel in guild.text_channels:
            perms = channel.permissions_for(guild.me)
            if perms.manage_roles:
                ow = channel.overwrites_for(role)
                ow.update(
                    send_messages=False,
                    add_reactions=False,
                    use_application_commands=False,
                    create_private_threads=False,
                    create_public_threads=False,
                    send_messages_in_threads=False,
                )
                try:
                    await channel.set_permissions(role, overwrite=ow, reason=reason)
                except discord.HTTPException:
                    failure += 1
                else:
                    success += 1
            else:
                skipped += 1
        return success, failure, skipped

    @commands_ext.command(
        commands.group,
        name='mute',
        invoke_without_command=True,
    )
    @can_mute()
    @commands_ext.command_permissions(3, bot=['manage_roles'])
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
            return await ctx.stick(False, 'Missing members to mute.')

        failed = 0
        for member in members:
            try:
                await member.add_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.stick(True, f'Muted [`{total - failed}`/`{total}`] members.')

    @commands_ext.command(
        commands.command,
        name='unmute',
        description='Unmutes members using the configured mute role.',
    )
    @can_mute()
    @commands_ext.command_permissions(3, bot=['manage_roles'])
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
            return await ctx.stick(False, 'Missing members to unmute.')

        failed = 0
        for member in members:
            try:
                await member.remove_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.stick(True, f'Unmuted [`{total - failed}`/`{total}`] members.')

    @commands_ext.command(
        commands.hybrid_command,
        name='tempmute',
        description='Temporarily mutes a member for the specified duration.',
    )
    @can_mute()
    @commands_ext.command_permissions(bot=['manage_roles'])
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
            return await ctx.stick(False, 'Sorry, this functionality is currently unavailable. Try again later?')

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
            True, f'Mute for {discord.utils.escape_mentions(str(member))} ends {discord.utils.format_dt(duration, 'R')}.')

    @commands.Cog.listener()
    async def on_tempmute_timer_complete(self, timer: Timer):
        guild_id, mod_id, member_id, role_id = timer.args
        await self.bot.wait_until_ready()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            # RIP
            return

        member = await self.bot.get_or_fetch_member(guild, member_id)
        if member is None or not member._roles.has(role_id):  # noqa
            async with self._batch_lock:
                self._data_batch[guild_id].append((member_id, False))
            return

        if mod_id != member_id:
            moderator = await self.bot.get_or_fetch_member(guild, mod_id)
            if moderator is None:
                try:
                    moderator = await self.bot.fetch_user(mod_id)
                except:  # noqa
                    # request failed somehow
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
            async with self._batch_lock:
                self._data_batch[guild_id].append((member_id, False))

    @commands_ext.command(
        _mute.group,
        name='role',
        description='Shows configuration of the mute role.',
    )
    @commands_ext.command_permissions(3, user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def _mute_role(self, ctx: GuildContext):
        """Shows configuration of the mute role.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """
        config = await self.get_guild_config(ctx.guild.id)
        role = config and config.mute_role
        if role is not None:
            members = config.muted_members.copy()  # type: ignore  # This is already narrowed
            members.update(map(lambda r: r.id, role.members))
            total = len(members)
            role = f'{role} (ID: {role.id})'
        else:
            total = 0
        await ctx.stick(True, f'Role: {role}\nMembers Muted: {total}')

    @commands_ext.command(
        _mute_role.command,
        name='set',
        description='Sets the mute role to a pre-existing role.',
    )
    @commands_ext.command_permissions(3, user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    @commands.cooldown(1, 60.0, commands.BucketType.guild)
    async def mute_role_set(self, ctx: GuildContext, *, role: discord.Role):
        """Sets the mute role to a pre-existing role.
        This command can only be used once every minute.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """

        if role.is_default():
            return await ctx.stick(False, 'Cannot use the @\u200beveryone role.')

        if role > ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.stick(False, 'This role is higher than your highest role.')

        if role > ctx.me.top_role:
            return await ctx.stick(False, 'This role is higher than my highest role.')

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

    @commands_ext.command(
        _mute_role.command,
        name='update',
        description='Updates the permission overwrites of the mute role.',
        aliases=['sync']
    )
    @commands_ext.command_permissions(3, user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_update(self, ctx: GuildContext):
        """Updates the permission overwrites of the mute role.
        This works by blocking the Send Messages and Adding Reactions
        permission on every text channel that the bot can do.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """

        config = await self.get_guild_config(ctx.guild.id)
        role = config and config.mute_role
        if role is None:
            return await ctx.stick(False, 'No mute role has been set up to update.')

        async with ctx.typing():
            success, failure, skipped = await self.update_mute_role_permissions(
                role, ctx.guild, ctx.author._user  # noqa
            )
            total = success + failure + skipped
            await ctx.send(
                f'<:discord_info:1113421814132117545> Attempted to update {total} channel permissions. '
                f'[Updated: {success}, Failed: {failure}, Skipped (no permissions): {skipped}]'
            )

    @commands_ext.command(
        _mute_role.command,
        name='create',
        description='Creates a mute role with the given name.',
    )
    @commands_ext.command_permissions(3, user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
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
            return await ctx.send('A mute role already exists.')

        try:
            role = await ctx.guild.create_role(name=name,
                                               reason=f'Mute Role Created By {ctx.author} (ID: {ctx.author.id})')
        except discord.HTTPException as e:
            return await ctx.stick(False, f'An error happened: {e}')

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
            success, failure, skipped = await self.update_mute_role_permissions(
                role, ctx.guild, ctx.author._user  # noqa
            )
            await ctx.stick(
                True, 'Mute role successfully created. Overwrites: ' f'[Updated: {success}, Failed: {failure}, Skipped: {skipped}]')

    @commands_ext.command(
        _mute_role.command,
        name='unbind',
        aliases=['delete'],
        description='Unbinds a mute role without deleting it.',
    )
    @commands_ext.command_permissions(3, user=['manage_roles', 'moderate_members'], bot=['manage_roles'])
    async def mute_role_unbind(self, ctx: GuildContext):
        """Unbinds a mute role without deleting it.
        To use these commands, you need to have Manage Roles
        and Manage Server permission at the server level.
        """
        guild_id = ctx.guild.id
        config = await self.get_guild_config(guild_id)
        if config is None or config.mute_role is None:
            return await ctx.send('No mute role has been set up.')

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

    @commands_ext.command(
        commands.command,
        name='selfmute',
        description='Temporarily mutes yourself for the specified duration.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(3, bot=['manage_roles'])
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
            return await ctx.stick(False, 'Sorry, this functionality is currently unavailable. Try again later?')

        config = await self.get_guild_config(ctx.guild.id)
        role_id = config and config.mute_role_id
        if role_id is None:
            raise NoMuteRole()

        if ctx.author._roles.has(role_id):  # noqa
            return await ctx.stick(False, 'Somehow you are already muted?')

        created_at = ctx.message.created_at
        if duration > (created_at + datetime.timedelta(days=1)):
            return await ctx.stick(False, 'Duration is too long. Must be at most 24 hours.')

        if duration < (created_at + datetime.timedelta(minutes=5)):
            return await ctx.stick(False, 'Duration is too short. Must be at least 5 minutes.')

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

        await ctx.stick(
            True, f'Selfmute ends **{discord.utils.format_dt(duration, 'f')}**. Be sure not to bother anyone about it.')


async def setup(bot):
    await bot.add_cog(Mod(bot))
