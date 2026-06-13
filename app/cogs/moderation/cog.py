from __future__ import annotations

import asyncio
import datetime
import logging
from collections import Counter, defaultdict
from contextlib import suppress
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import asyncpg
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands, tasks

from app.core import Bot, Context, Flags, NoticeView, flag, store_true
from app.core.converter import ActionReason, BannedMember, IgnoreableEntity, IgnoreEntity, MemberID
from app.core.models import BadArgument, Cog, PermissionTemplate, command, cooldown, describe, group
from app.core.pagination import LinePaginator, TextSource
from app.core.views import View
from app.database.base import Gatekeeper, GuildConfig
from app.services import build_purge_predicate
from app.utils import (
    checks,
    fuzzy,
    get_asset_url,
    helpers,
    human_join,
    pluralize,
    resolve_entity_id,
    timetools,
)
from app.utils.lock import lock
from config import Emojis

from .antispam import SpamChecker, check_raid, mention_spam_ban
from .gatekeeper import (
    GatekeeperAlertMassbanButton,
    GatekeeperAlertResolveButton,
    GatekeeperSetUpView,
    GatekeeperVerifyButton,
)
from .infractions import check_member_hierarchy, default_reason, safe_reason_append, update_role_permissions
from .lockdown import (
    build_lockdown_error_embed,
    end_lockdown,
    is_cooldown_active,
    is_potential_lockout,
    start_lockdown,
)
from .ui import PreExistingMuteRoleView

if TYPE_CHECKING:
    from app.core.timer import Timer

    class ModGuildContext(Context):
        cog: Moderation
        guild_config: GuildConfig


MaybeMember = discord.Member | discord.abc.Snowflake

log = logging.getLogger(__name__)


AutoModFlags = GuildConfig.AutoModFlags


class PurgeFlags(Flags):
    user: discord.User | None = flag(description="Remove messages from this user")
    contains: str | None = flag(description="Remove messages that contains this string (case sensitive)")
    prefix: str | None = flag(description="Remove messages that start with this string (case sensitive)")
    suffix: str | None = flag(description="Remove messages that end with this string (case sensitive)")
    after: int | None = flag(description="Search for messages that come after this message ID")
    before: int | None = flag(description="Search for messages that come before this message ID")
    delete_pinned: bool = store_true(description="Whether to delete messages that are pinned. Defaults to True.")
    bot: bool = store_true(description="Remove messages from bots (not webhooks!)")
    webhooks: bool = store_true(description="Remove messages from webhooks")
    embeds: bool = store_true(description="Remove messages that have embeds")
    files: bool = store_true(description="Remove messages that have attachments")
    emoji: bool = store_true(description="Remove messages that have custom emoji")
    reactions: bool = store_true(description="Remove messages that have reactions")
    require: Literal["any", "all"] = flag(
        description='Whether any or all of the flags should be met before deleting messages. Defaults to "all"',
        default="all",
    )


# noinspection PyProtectedMember
class Moderation(Cog):
    """Utility commands for moderation."""

    emoji = "<:mod_badge:1322337933428260874>"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self._spam_check: defaultdict[int, SpamChecker] = defaultdict(SpamChecker)

        self._mute_data_batch: defaultdict[int, list[tuple[int, Any]]] = defaultdict(list)
        self.bulk_mute_insert.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_mute_insert.start()

        self._gatekeeper_menus: dict[int, GatekeeperSetUpView] = {}
        self._gatekeepers: dict[int, Gatekeeper] = {}

        bot.add_dynamic_items(GatekeeperVerifyButton, GatekeeperAlertMassbanButton, GatekeeperAlertResolveButton)

    def cog_unload(self) -> None:
        self.bulk_mute_insert.stop()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        guild_ctx = cast("ModGuildContext", ctx)
        if ctx.guild is None:
            return
        guild_ctx.guild_config = await self.bot.db.get_guild_config(guild_id=ctx.guild.id)

    async def bot_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True

        full_bypass = ctx.permissions.manage_guild or await self.bot.is_owner(ctx.author)
        if full_bypass:
            return True

        guild_id = ctx.guild.id
        config = await self.bot.db.get_guild_config(guild_id)  # type: ignore[arg-type]
        if config is None or not config.flags.value:
            return True

        checker = self._spam_check[guild_id]
        return not checker.is_flagged(ctx.author.id)

    @tasks.loop(seconds=15.0)
    @lock("Moderation", "mute_batch", wait=True)
    async def bulk_mute_insert(self) -> None:
        """|coro|

        Bulk insert the mute data into the database.
        """
        if not self._mute_data_batch:
            return

        final_data = []
        for guild_id, data in self._mute_data_batch.items():
            config = await self.bot.db.get_guild_config(guild_id)  # type: ignore[arg-type]

            if config is None:
                continue

            as_set: set[int] = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)  # type: ignore[arg-type]

            final_data.append({"guild_id": guild_id, "result_array": list(as_set)})
            self.bot.db.get_guild_config.invalidate(guild_id)

        await self.bot.db.moderation.bulk_update_muted_members(final_data)
        self._mute_data_batch.clear()

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """|coro|

        This listener is used to check if a message is spamming and if a member is a fast joiner or a suspicious joiner.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message that has been sent.
        """
        author = message.author
        if (
            author.id in (self.bot.user.id if self.bot.user else None, self.bot.owner_id)
            or message.guild is None
            or not isinstance(author, discord.Member)
            or author.bot
            or author.guild_permissions.manage_messages
        ):
            return

        if message.is_system():
            return

        config: GuildConfig = await self.bot.db.get_guild_config(guild_id=message.guild.id)  # type: ignore[arg-type]
        if config is None:
            return

        if (
            message.channel.id in config.safe_automod_entity_ids
            or author.id in config.safe_automod_entity_ids
            or any(i in config.safe_automod_entity_ids for i in author._roles)  # type: ignore[arg-type]
        ):
            return

        await check_raid(self._spam_check[message.guild.id], config, message.guild, author, message)

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(message.guild.id)  # type: ignore[arg-type]
            if gatekeeper is not None and gatekeeper.is_bypassing(author) and message.channel.id != gatekeeper.channel_id:
                reason = "Bypassing gatekeeper by messaging early"
                coro = author.ban if gatekeeper.bypass_action == "ban" else author.kick
                with suppress(discord.HTTPException):
                    await coro(reason=reason)
                return

        if not config.flags.mentions or not config.mention_count:
            return

        checker = self._spam_check[message.guild.id]
        if checker.is_mention_spam(message, config):
            responses = mention_spam_ban(config.mention_count, message.guild.id, author, multiple=True)
            pages = TextSource(prefix="", suffix="").add_lines([x async for x in responses]).pages
            for page in pages:
                await config.send_alert(page)
            return

        if len(message.mentions) <= 3:
            return

        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        responses = mention_spam_ban(mention_count, message.guild.id, author)
        pages = TextSource(prefix="", suffix="").add_lines([x async for x in responses]).pages
        for page in pages:
            await config.send_alert(page)

    @Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """|coro|

        This listener is used to check if a member is a fast joiner or a suspicious joiner.
        If a member is a fast joiner, they are flagged and if they are a suspicious joiner, they are flagged as well.
        If the guild has the `gatekeeper` flag enabled, the gatekeeper is used to check if the member is a spammer.

        Parameters
        ----------
        member: :class:`discord.Member`
            The member that has joined the guild.
        """
        if member.bot:
            return

        config = await self.bot.db.get_guild_config(member.guild.id)  # type: ignore[arg-type]
        if config is None:
            return

        if config.is_muted(member):
            await config.apply_mute(member, "Member was previously muted.")
            return

        if not config.flags.gatekeeper:
            return

        checker = self._spam_check[member.guild.id]

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(member.guild.id)  # type: ignore[arg-type]
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
                                title="Gatekeeper - Rapid Join",
                                description=(
                                    f"Detected {pluralize(len(spammers)):member} joining in rapid succession. "
                                    "The following actions have been automatically taken:\n"
                                    "- Enabled Gatekeeper to block them from participating.\n"
                                ),
                                colour=helpers.Colour.light_orange(),
                            )
                            view = View(timeout=None)
                            view.add_item(GatekeeperAlertMassbanButton(self))
                            view.add_item(GatekeeperAlertResolveButton(gatekeeper))
                            await config.send_alert(embed=embed, view=view)

        if config.flags.alerts:
            spammers = checker.is_alertable_join_spam(member)
            if spammers:
                view = View(timeout=None)
                view.add_item(GatekeeperAlertMassbanButton(self))
                await config.send_alert(
                    f"Detected **{pluralize(len(spammers)):member}** joining in rapid succession. **Please review!**",
                    view=view,
                )

    @Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        """|coro|

        This listener is used to remove members from the spam checker when they leave the guild.

        Parameters
        ----------
        payload: :class:`discord.RawMemberRemoveEvent`
            The payload of the member that has left the guild.
        """
        checker = self._spam_check.get(payload.guild_id)
        if checker is None:
            return

        checker.remove_member(payload.user)

    @Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
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

        config = await self.bot.db.get_guild_config(after.guild.id)  # type: ignore[arg-type]
        if config is None:
            return

        if config.mute_role_id is None:
            return

        before_has = before.get_role(config.mute_role_id)
        after_has = after.get_role(config.mute_role_id)

        if before_has == after_has:
            return

        self._mute_data_batch[after.guild.id].append((after.id, after_has))

    @Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """|coro|

        This listener is used to check if a role has been deleted.
        If a role has been deleted, the mute role is checked and if the role is the mute role, the mute role is removed.

        Parameters
        ----------
        role: :class:`discord.Role`
            The role that has been deleted.
        """
        config: GuildConfig = await self.bot.db.get_guild_config(role.guild.id)  # type: ignore[arg-type]
        if config is None:
            return

        if role.id == config.poll_ping_role_id:
            await config.update(poll_ping_role_id=None)
            await config.send_alert("Poll ping role has been deleted, therefore it's been automatically reset.")
            return

        if role.id == config.mute_role_id:
            await config.update(mute_role_id=None, muted_members=[])
            await config.send_alert("Mute role has been deleted, therefore it's been automatically reset.")
            return

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(role.guild.id)  # type: ignore[arg-type]
            if gatekeeper is not None and gatekeeper.role_id == role.id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        "Gatekeeper **role** has been deleted while it's active, therefore it's been automatically disabled."
                    )
                await gatekeeper.edit(started_at=None, role_id=None)
                return

    @Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """|coro|

        This listener is used to check if a channel has been created.
        Handles all permission updates for Mute Configuration and Gatekeeper Configuration.

        Parameters
        ----------
        channel: :class:`discord.abc.GuildChannel`
            The channel that has been created.
        """
        config: GuildConfig = await self.bot.db.get_guild_config(guild_id=channel.guild.id)
        if config is None:
            return

        me = channel.guild.me

        if config.mute_role is not None:
            _, failed, _ = await update_role_permissions(config.mute_role, channel.guild, me, channels=[channel])  # type: ignore
            if failed:
                await config.send_alert(
                    f"Failed to update permissions for the **mute role** on channel creation. [{channel.mention}]"
                )

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(guild_id=channel.guild.id)
            if gatekeeper is not None and gatekeeper.role_id:
                role = channel.guild.get_role(gatekeeper.role_id)
                if role is not None:
                    _, failed, _ = await update_role_permissions(
                        role, channel.guild, me, update_read_permissions=True, channels=[channel]
                    )
                    if failed:
                        await config.send_alert(
                            f"Failed to update permissions for the **gatekeeper role** on channel creation. [{channel.mention}]"
                        )

    @Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """|coro|

        This listener is used to check if a channel has been deleted.
        If a channel has been deleted, the gatekeeper channel is checked and if the channel is the gatekeeper channel,
        the gatekeeper channel is removed.

        Parameters
        ----------
        channel: :class:`discord.abc.GuildChannel`
            The channel that has been deleted.
        """
        config: GuildConfig = await self.bot.db.get_guild_config(guild_id=channel.guild.id)
        if config is None:
            return

        if config.music_panel_channel_id and config.music_panel_message_id and channel.id == config.music_panel_channel_id:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            await config.send_alert("Music panel channel has been deleted, therefore it's been automatically disabled.")
            return

        if config.poll_channel_id and channel.id == config.poll_channel_id:
            await config.update(poll_channel_id=None)
            await config.send_alert("Poll channel has been deleted, therefore it's been automatically disabled.")
            return

        if config.poll_reason_channel_id and channel.id == config.poll_reason_channel_id:
            await config.update(poll_reason_channel_id=None)
            await config.send_alert("Poll reason channel has been deleted, therefore it's been automatically disabled.")
            return

        if config.alert_channel_id and channel.id == config.alert_channel_id:
            await config.update(alert_channel_id=None)
            await config.send_alert("Alert channel has been deleted, therefore it's been automatically disabled.")
            return

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(channel.guild.id)  # type: ignore[arg-type]
            if gatekeeper is not None and gatekeeper.channel_id == channel.id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        "Gatekeeper **channel** has been deleted while it's active, "
                        "therefore it's been automatically disabled."
                    )
                await gatekeeper.edit(started_at=None, channel_id=None)
                return

    @Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """|coro|

        This listener is used to check if a message has been deleted.
        If a message has been deleted, the gatekeeper starter message is checked and if the message is the gatekeeper
        starter message, the gatekeeper starter message is removed.

        Parameters
        ----------
        payload: :class:`discord.RawMessageDeleteEvent`
            The message that has been deleted.
        """
        if payload.guild_id is None:
            return
        config: GuildConfig = await self.bot.db.get_guild_config(payload.guild_id)  # type: ignore[arg-type]
        if config is None:
            return

        if config.music_panel_message_id and payload.message_id == config.music_panel_message_id:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            await config.send_alert("Music panel message has been deleted, therefore it's been automatically disabled.")
            return

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(payload.guild_id)  # type: ignore[arg-type]
            if gatekeeper is not None and gatekeeper.message_id == payload.message_id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        "Gatekeeper **starter message** has been deleted while it's active, "
                        "therefore it's been automatically disabled."
                    )
                await gatekeeper.edit(started_at=None, message_id=None)
                return

    @Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        """|coro|

        This listener is used to check if a message has been deleted in bulk.
        If a message has been deleted in bulk, the gatekeeper starter message is checked and if the message is the gatekeeper
        starter message, the gatekeeper starter message is removed.

        Parameters
        ----------
        payload: :class:`discord.RawBulkMessageDeleteEvent`
            The message that has been deleted in bulk.
        """
        if payload.guild_id is None:
            return
        config: GuildConfig = await self.bot.db.get_guild_config(payload.guild_id)  # type: ignore[arg-type]
        if config is None:
            return

        if config.music_panel_message_id and config.music_panel_message_id in payload.message_ids:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            await config.send_alert("Music panel message has been deleted, therefore it's been automatically disabled.")
            return

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(payload.guild_id)  # type: ignore[arg-type]
            if gatekeeper is not None and gatekeeper.message_id in payload.message_ids:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        "Gatekeeper starter message has been deleted while it's active, therefore it's been automatically disabled."
                    )
                await gatekeeper.edit(started_at=None, message_id=None)
                return

    @Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
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

        config = await self.bot.db.get_guild_config(member.guild.id)  # type: ignore[arg-type]
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.bot.db.get_guild_gatekeeper(member.guild.id)  # type: ignore[arg-type]
        # Joined VC and is bypassing gatekeeper
        if gatekeeper is not None and gatekeeper.is_bypassing(member):
            reason = "Bypassing gatekeeper by joining a voice channel early"
            coro = member.ban if gatekeeper.bypass_action == "ban" else member.kick
            with suppress(discord.HTTPException):
                await coro(reason=reason)

    @command(
        "slowmode",
        aliases=["sm"],
        description="Applies slowmode to this channel.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["manage_channels"],
        user_permissions=["manage_channels"],
    )
    @describe(duration="The slowmode duration or 0s to disable")
    async def slowmode(self, ctx: ModGuildContext, *, duration: timetools.ShortTime) -> None:
        """Applies slowmode to this channel"""
        delta = duration.dt - ctx.message.created_at
        slowmode_delay = int(delta.total_seconds())

        if slowmode_delay > 21600:
            await ctx.send_error("Provided slowmode duration is too long!", ephemeral=True)
        else:
            reason = f"Slowmode changed by {ctx.author} (ID: {ctx.author.id})"
            await ctx.channel.edit(slowmode_delay=slowmode_delay, reason=reason)  # type: ignore[union-attr]
            if slowmode_delay > 0:
                fmt = timetools.human_timedelta(duration.dt, source=ctx.message.created_at, accuracy=2)
                await ctx.send_error(f"Configured slowmode to {fmt}", ephemeral=True)
            else:
                await ctx.send_success("Disabled slowmode", ephemeral=True)

    @group(
        "moderation",
        aliases=["mod"],
        fallback="info",
        description="Show the current Bot-Automatic-Moderation behaviour on the server.",
        guild_only=True,
        hybrid=True,
        user_permissions=PermissionTemplate.mod,
    )
    async def moderation(self, ctx: ModGuildContext) -> None:
        """Show current Bot-Automatic-Moderation behavior on the server."""
        assert ctx.guild is not None
        if ctx.guild_config is None:
            await ctx.send_error("This server does not have moderation enabled.")
            return

        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        container.add_item(
            discord.ui.Section(
                f"## {ctx.guild.name} Moderation Configuration\n"
                "This is the current Bot-Automatic-Moderation configuration for this server.\n"
                "You can use the commands in this category to modify these settings.",
                accessory=discord.ui.Thumbnail(get_asset_url(ctx.guild)),
            )
        )
        container.add_item(discord.ui.Separator())

        enabled = 0

        if ctx.guild_config.flags.audit_log:
            audit_log_broadcast = f"Bound to <#{ctx.guild_config.audit_log_channel_id}>"
            enabled += 1
        else:
            audit_log_broadcast = "*Disabled*"

        if ctx.guild_config.flags.alerts:
            alerts = f"Bound to <#{ctx.guild_config.alert_channel_id}>"
            enabled += 1
        else:
            alerts = "Disabled"

        if ctx.guild_config.flags.raid:
            raid = "Enabled"
            enabled += 1
        else:
            raid = "*Disabled*"

        if ctx.guild_config.mention_count:
            mention_spam = f"Set to **{ctx.guild_config.mention_count}** mentions"
            enabled += 1
        else:
            mention_spam = "*Disabled*"

        container.add_item(
            discord.ui.TextDisplay(
                f"**\N{IDENTIFICATION CARD} Audit Log**\n{audit_log_broadcast}\n"
                f"**⚠️ Mod Alerts**\n{alerts}\n"
                f"**\N{SHIELD} Raid Protection**\n{raid}\n"
                f"**\N{PUBLIC ADDRESS LOUDSPEAKER} Mention Spam Protection**\n{mention_spam}"
            )
        )

        if ctx.guild_config.flags.gatekeeper:
            enabled += 1
            gatekeeper = await self.bot.db.get_guild_gatekeeper(ctx.guild.id)  # type: ignore[arg-type]
            if gatekeeper is not None:
                gatekeeper_status = gatekeeper.status
            else:
                gatekeeper_status = "Partially Disabled (Configuration Setup, but not enabled)"
        else:
            gatekeeper_status = "Completely Disabled"

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"**\N{LOCK} Gatekeeper**\n{gatekeeper_status}"))

        if ctx.guild_config.safe_automod_entity_ids:
            resolved = [resolve_entity_id(c, guild=ctx.guild) for c in ctx.guild_config.safe_automod_entity_ids]  # type: ignore[arg-type]

            if len(ctx.guild_config.safe_automod_entity_ids) <= 5:
                ignored = "\n".join(resolved)
            else:
                entities = "\n".join(resolved[:5])
                ignored = f"{entities}\n(*{len(ctx.guild_config.safe_automod_entity_ids) - 5} more...*)"
        else:
            ignored = "*N/A*"

        container.add_item(discord.ui.TextDisplay(f"**\N{BUSTS IN SILHOUETTE} Ignored Entities**\n{ignored}"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Enabled Features: {enabled}/5"))
        await ctx.send(view=NoticeView(container))

    @moderation.command(
        "alerts",
        description="Toggles alert message logging on the server.",
        guild_only=True,
        bot_permissions=["manage_webhooks"],
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to send alert messages to. The bot must be able to create webhooks in it.")
    async def moderation_alerts(self, ctx: ModGuildContext, *, channel: discord.TextChannel) -> None:
        """Toggles alert message logging on the server.

        The bot must have the ability to create webhooks in the given channel.
        """
        assert ctx.guild is not None
        await ctx.defer()
        if ctx.guild_config and ctx.guild_config.flags.alerts:
            await ctx.send_info(
                f'You already have alert message logging enabled. To disable, use "{ctx.prefix}moderation disable alerts"'
            )
            return

        channel_id = channel.id

        reason = f"{ctx.author} enabled alert message logging (ID: {ctx.author.id})"

        assert self.bot.user is not None
        avatar_asset = self.bot.user.avatar
        avatar_data = await avatar_asset.read() if avatar_asset is not None else None

        try:
            webhook = await channel.create_webhook(name="Moderation Alerts", avatar=avatar_data, reason=reason)
        except discord.Forbidden:
            await ctx.send_error(f"The bot does not have permissions to create webhooks in {channel.mention}.")
            return
        except discord.HTTPException:
            await ctx.send_error(
                "An error occurred while creating the webhook. Note you can only have 10 webhooks per channel."
            )
            return

        flags = AutoModFlags()
        flags.alerts = True
        await ctx.db.moderation.enable_alerts(ctx.guild.id, flags.value, channel_id, webhook.url)
        await ctx.send_success(f"Alert messages enabled. Sending alerts to <#{channel_id}>.")

    @moderation.group(
        "auditlog",
        fallback="set",
        description="Toggles audit text log on the server.",
        bot_permissions=["manage_webhooks"],
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel="The channel to broadcast audit log messages to.")
    async def moderation_auditlog(self, ctx: ModGuildContext, *, channel: discord.TextChannel) -> None:
        """Toggles audit text log on the server.
        Audit Log sends a message to the log channel whenever a certain event is triggered.
        """
        assert ctx.guild is not None
        await ctx.defer()
        reason = f"{ctx.author} enabled mod audit log (ID: {ctx.author.id})"

        wh_url = await self.bot.db.moderation.get_audit_log_webhook_url(ctx.guild.id)
        if wh_url is not None:
            # Delete the old webhook if it exists
            with suppress(discord.HTTPException):
                webhook = discord.Webhook.from_url(wh_url, session=self.bot.session)
                await webhook.delete(reason=reason)

        assert self.bot.user is not None
        try:
            webhook = await channel.create_webhook(
                name="Moderation Audit Log",
                avatar=await self.bot.user.display_avatar.read(),
                reason=reason,  # type: ignore[arg-type]
            )
        except discord.Forbidden:
            await ctx.send_error("I do not have permissions to create a webhook in that channel.")
            return
        except discord.HTTPException:
            await ctx.send_error(
                "Failed to create a webhook in that channel. Note that the limit for webhooks in each channel is **10**."
            )
            return

        await ctx.db.moderation.enable_audit_log(ctx.guild.id, AutoModFlags.audit_log.flag, channel.id, webhook.url)
        await ctx.send_success(f"Audit log enabled. Broadcasting log events to <#{channel.id}>.")

    @moderation_auditlog.command(
        "alter",
        description="Configures the audit log events.",
        user_permissions=PermissionTemplate.mod,
    )
    @describe(flag="The flag you want to set.", value="The value you want to set the flag to.")
    async def moderation_auditlog_alter(self, ctx: ModGuildContext, flag: str, value: bool) -> None:
        """Configures the audit log events.
        You can set the Events you want to get notified about via the Audit Log Channel.
        """
        if ctx.guild_config is None:
            await ctx.send_error("This server does not have moderation enabled.")
            return

        if not ctx.guild_config.flags.audit_log:
            await ctx.send_error("Audit log is not enabled on this server.")
            return

        if flag == "all":
            for key in ctx.guild_config.audit_log_flags:
                ctx.guild_config.audit_log_flags[key] = value
            content = f"Set all Audit Log Events to `{value}`."
        else:
            if flag in ctx.guild_config.audit_log_flags:
                ctx.guild_config.audit_log_flags[flag] = value
                content = f"Set Audit Log Event **{flag}** to `{value}`."
            else:
                raise commands.BadArgument(f"Unknown flag **{flag}**")

        assert ctx.guild is not None
        await ctx.db.moderation.set_audit_log_flags(ctx.guild.id, ctx.guild_config.audit_log_flags)
        await ctx.send_success(content)

    @moderation_auditlog_alter.autocomplete("flag")
    async def moderation_auditlog_alter_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        assert interaction.guild_id is not None
        flags_map = await self.bot.db.moderation.get_audit_log_flags(interaction.guild_id)
        flags = list(flags_map.items()) if flags_map else []

        results = fuzzy.finder(current, flags, key=lambda x: x[0])
        return [app_commands.Choice(name="All", value="all")] + [
            app_commands.Choice(name=f"{flg} - {value}", value=flg) for (flg, value) in results
        ]

    @moderation.command(
        "disable", description="Disables Moderation on the server.", guild_only=True, user_permissions=PermissionTemplate.mod
    )
    @describe(protection="The protection to disable")
    @app_commands.choices(
        protection=[
            app_commands.Choice(name="Everything", value="all"),
            app_commands.Choice(name="Alerts", value="alerts"),
            app_commands.Choice(name="Raid protection", value="raid"),
            app_commands.Choice(name="Mention spam protection", value="mentions"),
            app_commands.Choice(name="Audit Logging", value="auditlog"),
            app_commands.Choice(name="Gatekeeper", value="gatekeeper"),
        ]
    )
    async def moderation_disable(
        self,
        ctx: ModGuildContext,
        *,
        protection: Literal["all", "raid", "mentions", "auditlog", "alerts", "gatekeeper"] = "all",
    ) -> None:
        """Disables Moderation on the server.

        ## Settings
        - **all**: to disable everything
        - **alerts**: to disable alert messages
        - **raid**: to disable raid protection
        - **mentions**: to disable mention spam protection
        - **auditlog**: to disable audit logging
        - **gatekeeper**: to disable gatekeeper

        If not given then it defaults to 'all'.
        """
        if protection == "all":
            updates = "flags = 0, mention_count = 0, broadcast_channel = NULL, audit_log_channel = NULL"
            message = "Moderation has been disabled."
        elif protection == "raid":
            updates = f"flags = guild_config.flags & ~{AutoModFlags.raid.flag}"
            message = "Raid protection has been disabled."
        elif protection == "alerts":
            updates = f"flags = guild_config.flags & ~{AutoModFlags.alerts.flag}, alert_channel = NULL"
            message = "Alert messages have been disabled."
        elif protection == "mentions":
            updates = f"flags = guild_config.flags & ~{AutoModFlags.mentions.flag}, mention_count = NULL"
            message = "Mention spam protection has been disabled"
        elif protection == "auditlog":
            updates = f"flags = guild_config.flags & ~{AutoModFlags.audit_log.flag}, audit_log_channel = NULL, audit_log_flags = NULL"
            message = "Audit logging has been disabled."
        elif protection == "gatekeeper":
            updates = f"flags = guild_config.flags & ~{AutoModFlags.gatekeeper.flag}"
            message = "Gatekeeper has been disabled."
        else:
            raise commands.BadArgument(f"Unknown protection {protection}")

        assert ctx.guild is not None
        guild_id = ctx.guild.id
        records = await self.bot.db.moderation.disable_protection(guild_id, updates)
        self._spam_check.pop(guild_id, None)

        hooks = (
            [[records.get("audit_log_webhook_url", None), "Audit Log"], [records.get("alert_webhook_url", None), "Alerts"]]
            if protection in ("auditlog", "all")
            else []
        )

        warnings = []

        for record in hooks:
            if record[0]:
                wh = discord.Webhook.from_url(str(record[0]), session=self.bot.session)
                try:
                    await wh.delete(reason=message)
                except discord.HTTPException:
                    warnings.append(f"The webhook `{record[1]}` could not be deleted for some reason.")

        if protection in ("all", "gatekeeper"):
            gatekeeper = await self.bot.db.get_guild_gatekeeper(guild_id=guild_id)
            if gatekeeper is not None and gatekeeper.started_at is not None:
                await gatekeeper.disable()
                warnings.append("Gatekeeper was previously running and has been forcibly disabled.")
                members = gatekeeper.pending_members
                if members:
                    warnings.append(
                        f"There {pluralize(members):is|are!} still {pluralize(members):member} waiting in the role queue."
                        " **The queue will be paused until gatekeeper is re-enabled**"
                    )

        if warnings:
            warning = f"{Emojis.warning} **Warnings:**\n" + "\n".join(warnings)
            message = f"{message}\n\n{warning}"

        await ctx.send_success(message)

    @moderation.command(
        "gatekeeper",
        description="Enables and shows the gatekeeper settings menu for the server.",
        guild_only=True,
        # Creates/assigns the unverified role and edits channel overwrites (manage_roles),
        # and removes bypassers via the configurable ban/kick action.
        bot_permissions=["manage_roles", "ban_members", "kick_members"],
        user_permissions=PermissionTemplate.mod,
    )
    async def moderation_gatekeeper(self, ctx: ModGuildContext) -> None:
        """Enables and shows the gatekeeper settings menu for the server.

        Gatekeeper automatically assigns a role to members who join to prevent
        them from participating in the server until they verify themselves by
        pressing a button.
        """
        assert ctx.guild is not None
        previous = self._gatekeeper_menus.pop(ctx.guild.id, None)
        if previous is not None:
            await previous.on_timeout()
            previous.stop()

        gatekeeper = await self.bot.db.get_guild_gatekeeper(guild_id=ctx.guild.id)
        gatekeeper_record, config_record = await self.bot.db.moderation.setup_gatekeeper(
            ctx.guild.id, AutoModFlags.gatekeeper.flag, create_gatekeeper=gatekeeper is None
        )

        if gatekeeper is None:
            gatekeeper = Gatekeeper([], bot=self.bot, record=gatekeeper_record)
        config = GuildConfig(bot=self.bot, record=config_record)

        # The explanatory header now lives inside the Components V2 view's container.
        self._gatekeeper_menus[ctx.guild.id] = view = GatekeeperSetUpView(self, ctx.author, config, gatekeeper)  # type: ignore[arg-type]
        view.message = await ctx.send(view=view)

    @moderation.command(
        "raid",
        description="Toggles raid protection on the server.",
        guild_only=True,
        bot_permissions=["ban_members"],
        user_permissions=PermissionTemplate.mod,
    )
    @describe(enabled="Whether raid protection should be enabled or not, toggles if not given.")
    async def moderation_raid(self, ctx: ModGuildContext, enabled: bool | None = None) -> None:
        """Toggles raid protection on the server.
        Raid protection automatically bans members that spam messages in your server.
        """
        assert ctx.guild is not None
        enabled = await self.bot.db.moderation.toggle_raid_protection(ctx.guild.id, AutoModFlags.raid.flag, enabled)
        fmt = "*enabled*" if enabled else "*disabled*"
        await ctx.send_success(f"Raid protection {fmt}.")

    @moderation.command(
        "mentions",
        description="Enables auto-banning accounts that spam more than 'count' mentions.",
        guild_only=True,
        bot_permissions=["ban_members"],  # the protection auto-bans mention spammers
        user_permissions=PermissionTemplate.mod,
    )
    @describe(count="The maximum amount of mentions before banning.")
    async def moderation_mentions(self, ctx: ModGuildContext, count: commands.Range[int, 3]) -> None:
        """
        Enables auto-banning accounts that spam more than 'count' mentions.
        To use this command, you must have the Ban Members permission.

        The count must be greater than 3.
        The bot will automatically ban members that spam more than the specified amount of mentions.

        Note: This applies to only for user mentions, role mentions are not counted.
        """
        assert ctx.guild is not None
        await ctx.db.moderation.set_mention_count(ctx.guild.id, count)
        await ctx.db.moderation.toggle_raid_protection(ctx.guild.id, AutoModFlags.mentions.flag, True)
        await ctx.send_success(f"Mention spam protection threshold set to `{count}`.")

    @moderation_mentions.error
    async def moderation_mentions_error(self, ctx: ModGuildContext, error: commands.BadArgument) -> None:
        if isinstance(error, commands.RangeError):
            await ctx.send_error("Mention spam protection threshold must be greater than **3**.")

    @moderation.command(
        "ignore",
        description="Specifies what roles, members, or channels ignore Moderation Inspections.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(entities="Space separated list of roles, members, or channels to ignore")
    async def moderation_ignore(
        self, ctx: ModGuildContext, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Adds roles, members, or channels to the ignore list for Moderation auto-bans."""
        if len(entities) == 0:
            raise commands.BadArgument("Missing entities to ignore.")

        assert ctx.guild is not None
        await ctx.db.moderation.add_safe_entities(ctx.guild.id, [c.id for c in entities])

        embed = discord.Embed(title="New Ignored Entities", color=helpers.Colour.white())
        embed.description = "\n".join(f"- {c.mention}" for c in entities)
        await ctx.send_success("Updated ignore list to ignore:", embed=embed)

    @moderation.command(
        "unignore",
        description="Specifies what roles, members, or channels to take off the ignore list.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(entities="Space separated list of roles, members, or channels to take off the ignore list")
    async def moderation_unignore(
        self, ctx: ModGuildContext, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Remove roles, members, or channels from the ignore list for Moderation auto-bans."""
        if len(entities) == 0:
            raise commands.BadArgument("Missing entities to unignore.")

        assert ctx.guild is not None
        await ctx.db.moderation.remove_safe_entities(ctx.guild.id, [c.id for c in entities])
        embed = discord.Embed(title="Removed Ignored Entities", color=helpers.Colour.white())
        embed.description = "\n".join(f"- {c.mention}" for c in entities)
        await ctx.send_success("Updated ignore list to no longer ignore:", embed=embed)

    @moderation.command(
        "ignored", description="Lists what channels, roles, and members are in the moderation ignore list.", guild_only=True
    )
    async def moderation_ignored(self, ctx: ModGuildContext) -> None:
        """List all the channels, roles, and members that are in the Moderation ignore list."""

        if ctx.guild_config is None or not ctx.guild_config.safe_automod_entity_ids:
            await ctx.send_error("This server does not have any ignored entities.")
            return

        assert ctx.guild is not None
        entities = [resolve_entity_id(x, guild=ctx.guild) for x in ctx.guild_config.safe_automod_entity_ids]
        entities = [f"- {e}" for e in entities]
        await LinePaginator.start(ctx, entries=entities, location="description")

    @command(
        "purge",
        description="Removes messages that meet a criteria.",
        aliases=["clear"],
        guild_only=True,
        hybrid=True,
        user_permissions=["manage_messages"],
        bot_permissions=["manage_messages"],
    )
    @describe(search="How many messages to search for")
    async def purge(
        self, ctx: ModGuildContext, search: commands.Range[int, 1, 2000] | None = None, *, flags: PurgeFlags
    ) -> None:
        """Removes messages that meet a criteria.
        This command uses a syntax similar to Discord's search bar.
        The messages are only deleted if all options are met unless
        the `--require` flag is passed to override the behaviour.

        When the command is done doing its work, you will get a message
        detailing which users got removed and how many messages got removed.
        """
        await ctx.defer()

        plan = build_purge_predicate(
            bot=flags.bot,
            webhooks=flags.webhooks,
            embeds=flags.embeds,
            files=flags.files,
            reactions=flags.reactions,
            emoji=flags.emoji,
            user=flags.user,
            contains=flags.contains,
            prefix=flags.prefix,
            suffix=flags.suffix,
            delete_pinned=flags.delete_pinned,
            require=flags.require,
        )
        predicate = plan.predicate
        require_prompt = plan.require_prompt

        if flags.after and search is None:
            search = 2000

        if search is None:
            search = 100

        if require_prompt:
            confirm = await ctx.confirm(
                f"{Emojis.warning} Are you sure you want to delete `{pluralize(search):message}`?",
                ephemeral=True,
                timeout=30,
            )
            if not confirm:
                return

        async with ctx.channel.typing():
            before = discord.Object(id=flags.before) if flags.before else None
            after = discord.Object(id=flags.after) if flags.after else None

            try:
                deleted = await asyncio.wait_for(
                    ctx.channel.purge(limit=search, before=before, after=after, check=predicate),  # type: ignore[arg-type]
                    timeout=100,  # type: ignore[union-attr]
                )
            except discord.Forbidden:
                await ctx.send_error("I do not have permissions to delete messages.")
                return
            except discord.HTTPException as e:
                await ctx.send_error(f"Failed to delete messages: {e}")
                return

            spammers = Counter(m.author.display_name for m in deleted)
            deleted = len(deleted)
            messages = [f"`{deleted}` message{' was' if deleted == 1 else 's were'} removed."]
            if deleted:
                messages.append("")
                spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
                messages.extend(f"**{name}**: `{count}`" for name, count in spammers)

            to_send = "\n".join(messages)

            if len(to_send) > 4000:
                to_send = f"Successfully removed `{deleted}` messages."

            embed = discord.Embed(title="Channel Purge", description=to_send, colour=helpers.Colour.lime_green())
            await ctx.send(embed=embed, delete_after=15)

    @group(
        "lockdown",
        fallback="start",
        description="Locks down specific channels.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["manage_roles"],
        user_permissions=PermissionTemplate.mod,
    )
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(channels="A space-separated list of text or voice channels to lock down")
    async def lockdown(
        self, ctx: ModGuildContext, channels: commands.Greedy[discord.TextChannel | discord.VoiceChannel]
    ) -> None:
        """Locks down channels by denying the default role to send messages or connect to voice channels."""
        if ctx.channel in channels and is_potential_lockout(ctx.me, ctx.channel):  # type: ignore[arg-type]
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                await ctx.send(embed=build_lockdown_error_embed())
                return

            confirm = await ctx.confirm(
                f"{Emojis.warning} This will potentially lock the bot from sending messages.\n"
                "Would you like to resolve the permission issue?"
            )
            if not confirm:
                return

        success, failures = await start_lockdown(ctx, channels)
        if failures:
            message = (
                f"Successfully locked down `{len(success)}`/`{len(failures)}` channels.\n"
                f"Failed channels: {', '.join(c.mention for c in failures)}\n\n"
                f"Give the bot Manage Roles permissions in those channels and try again."
            )
        else:
            message = f"**{pluralize(len(success)):channel}** were successfully locked down."

        embed = discord.Embed(title="Locked down", description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    @lockdown.command(
        "for",
        description="Locks down specific channels for a specified amount of time.",
        bot_permissions=["manage_roles"],
        user_permissions=PermissionTemplate.mod,
    )
    @checks.requires_timer()
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(
        duration="A duration on how long to lock down for, e.g. 30m.",
        channels="A space-separated list of text or voice channels to lock down.",
    )
    async def lockdown_for(
        self,
        ctx: ModGuildContext,
        duration: timetools.ShortTime,
        channels: commands.Greedy[discord.TextChannel | discord.VoiceChannel],
    ) -> None:
        """Locks down specific channels for a specified amount of time."""
        if ctx.channel in channels and is_potential_lockout(ctx.me, ctx.channel):  # type: ignore[arg-type]
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                await ctx.send(embed=build_lockdown_error_embed())
                return

            confirm = await ctx.confirm(
                f"{Emojis.warning} This will potentially lock the bot from sending messages.\n"
                "Would you like to resolve the permission issue?"
            )
            if not confirm:
                return

        assert ctx.guild is not None
        success, failures = await start_lockdown(ctx, channels)
        timer = await self.bot.timers.create(
            duration.dt,
            "lockdown",
            ctx.guild.id,
            ctx.author.id,
            ctx.channel.id,
            [c.id for c in success],
            created=ctx.message.created_at,
        )

        long = timer.expires >= timer.created + datetime.timedelta(days=1)
        formatted_time = discord.utils.format_dt(timer.expires, "f" if long else "T")  # type: ignore

        if failures:
            message = (
                f"Successfully locked down `{len(success)}`/`{len(failures)}` channels until {formatted_time}.\n"
                f"Failed channels: {', '.join(c.mention for c in failures)}\n"
                f"Give the bot Manage Roles permissions in {pluralize(len(failures)):channel|those channels} and try "
                f"the lockdown command on the failed **{pluralize(len(failures)):channel}** again."
            )
        else:
            message = f"**{pluralize(len(success)):Channel}** were successfully locked down until {formatted_time}."

        embed = discord.Embed(title="Locked down", description=message, color=helpers.Colour.lime_green())
        await ctx.send(embed=embed)

    @lockdown.command(
        "end",
        description="Ends all lockdowns set.",
        bot_permissions=["manage_roles"],
        user_permissions=PermissionTemplate.mod,
    )
    async def lockdown_end(self, ctx: ModGuildContext) -> None:
        """Ends all set lockdowns.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """
        assert ctx.guild is not None
        if not await is_cooldown_active(self.bot, ctx.guild, ctx.channel):  # type: ignore[arg-type]
            await ctx.send_error("There is no active lockdown.")
            return

        reason = f"Lockdown ended by {ctx.author} (ID: {ctx.author.id})"
        async with ctx.typing():
            failures = await end_lockdown(self.bot, ctx.guild, reason=reason)

        await ctx.db.moderation.clear_lockdowns(ctx.guild.id)
        if failures:
            await ctx.send_info(f"Lockdown ended. Failed to edit {human_join([c.mention for c in failures], final='and')}")
        else:
            await ctx.send_success("Lockdown successfully ended.")

    @Cog.listener()
    async def on_lockdown_timer_complete(self, timer: Timer) -> None:
        await self.bot.wait_until_ready()
        guild_id, mod_id, channel_id, channel_ids = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            return

        member = await self.bot.get_or_fetch_member(guild, mod_id)
        moderator = f"Mod ID {mod_id}" if member is None else f"{member} (ID: {mod_id})"

        reason = f"Automatic lockdown ended from timer made on {timer.created} by {moderator}"
        failures = await end_lockdown(self.bot, guild, channel_ids=channel_ids, reason=reason)

        await self.bot.db.moderation.remove_lockdowns(guild_id, channel_ids)

        channel = guild.get_channel_or_thread(channel_id)
        if channel is not None:
            assert isinstance(channel, discord.abc.Messageable)
            if failures:
                formatted = [c.mention for c in failures]
                await channel.send(f"{Emojis.info} Lockdown ended. Failed to edit {human_join(formatted, final='and')}.")
            else:
                valid = [f"<#{c}>" for c in channel_ids]
                await channel.send(f"{Emojis.success} Lockdown successfully ended for {human_join(valid, final='and')}.")

    @command(
        "kick",
        description="Kicks a member from the server.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["kick_members"],
        user_permissions=["kick_members"],
    )
    @describe(member="The member to ban. You can also pass in an ID to ban.", reason="The reason for banning the member.")
    async def kick(
        self,
        ctx: ModGuildContext,
        member: Annotated[MaybeMember, MemberID],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> discord.Message | None:
        """Kicks a member from the server."""
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        if error := check_member_hierarchy(ctx, member, action="kick"):
            await ctx.send_error(error)
            return

        await ctx.guild.kick(member, reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "kick", member.id, ctx.author.id, reason)
        await ctx.send_success(f"Kicked {member}.")

    @command(
        "ban",
        description="Bans a member from the server.",
        guild_only=True,
        bot_permissions=["ban_members"],
        user_permissions=["ban_members"],
    )
    @describe(
        member="The member to ban. You can also pass in an ID to ban regardless of whether they're in the server or not.",
        reason="The reason for banning the member.",
    )
    async def ban(
        self,
        ctx: ModGuildContext,
        member: Annotated[MaybeMember, MemberID],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Bans a member from the server.
        You can also ban from ID to ban regardless of whether they're
        in the server or not.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        if error := check_member_hierarchy(ctx, member, action="ban"):
            await ctx.send_error(error)
            return

        await ctx.guild.ban(member, reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "ban", member.id, ctx.author.id, reason)
        await ctx.send_success(f"Successfully banned `{member}`.")

    @command(
        "multiban",
        description="Bans multiple members by ID from the server.",
        guild_only=True,
        bot_permissions=["ban_members"],
        user_permissions=["ban_members", "kick_members"],
    )
    @describe(
        members="The members to ban. You can also pass in IDs to ban regardless of whether they're in the server or not.",
        reason="The reason for banning the members.",
    )
    async def multiban(
        self,
        ctx: ModGuildContext,
        members: Annotated[list[MaybeMember], commands.Greedy[MemberID]],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Bans multiple members from the server.
        This only works through banning via ID.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        total_members = len(members)
        if total_members == 0:
            raise commands.BadArgument("No members were passed to ban.")

        confirm = await ctx.confirm(f"{Emojis.warning} This will ban **{pluralize(total_members):member}**. Are you sure?")
        if not confirm:
            return

        failed = 0
        for member in members:
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.send_success(f"Successfully banned [`{total_members - failed}`/`{total_members}`] members.")

    @command(
        "softban",
        description="Soft bans a member from the server.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["ban_members"],
        user_permissions=["kick_members"],
    )
    @app_commands.describe(member="The member to softban.", reason="The reason for softbanning the member.")
    async def softban(
        self,
        ctx: ModGuildContext,
        member: Annotated[MaybeMember, MemberID],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Soft bans a member from the server.

        A softban is basically banning the member from the server but
        then unbanning the member as well. This allows you to essentially
        kick the member while removing their messages.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        if error := check_member_hierarchy(ctx, member, action="soft-ban"):
            await ctx.send_error(error)
            return

        await ctx.guild.ban(member, reason=reason)
        await ctx.guild.unban(member, reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "softban", member.id, ctx.author.id, reason)
        await ctx.send_success(f"Successfully soft-banned **{member}**.")

    @command(
        "unban",
        description="Unbans a member from the server.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["ban_members"],
        user_permissions=["ban_members"],
    )
    @describe(member="The member to unban.", reason="The reason for unbanning the member.")
    async def unban(
        self,
        ctx: ModGuildContext,
        member: Annotated[discord.BanEntry, BannedMember],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Unbans a member from the server.
        You can pass either the ID of the banned member or the Name#Discrim
        combination/Global Name of the member. Typically, the ID is easiest to use.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        await ctx.guild.unban(member.user, reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "unban", member.user.id, ctx.author.id, reason)
        if member.reason:
            await ctx.send_success(
                f"Unbanned {member.user} (ID: `{member.user.id}`); Previously banned for **{member.reason}**."
            )
        else:
            await ctx.send_success(f"Unbanned {member.user} (ID: `{member.user.id}`).")

    @command(
        "tempban",
        description="Temporarily bans a member for the specified duration.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["ban_members"],
        user_permissions=["ban_members"],
    )
    @checks.requires_timer()
    @describe(
        duration="The duration to ban the member for. Must be a future Time.",
        member="The member to ban.",
        reason="The reason for banning the member.",
    )
    async def tempban(
        self,
        ctx: ModGuildContext,
        duration: timetools.FutureTime,
        member: Annotated[MaybeMember, MemberID],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Temporarily bans a member for the specified duration.
        The duration can be a short time form e.g., 30d or a more human
        duration such as "until thursday at 3PM" or a more concrete time
        such as "2024-12-31".
        **You need to quote the duration if it contains spaces.**

        Note that times are in UTC unless the timezone is
        specified using the 'timezone set' command.

        ### Important
        If you want to ban a member by ID, consider using the text version of this command.
        The App Commands version of this command does not support banning by ID.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        if error := check_member_hierarchy(ctx, member, action="ban"):
            await ctx.send_error(error)
            return

        try:
            already_banned = await ctx.guild.fetch_ban(discord.Object(id=member.id)) is not None
        except (discord.NotFound, discord.HTTPException):
            already_banned = False

        if already_banned:
            existing = await self.bot.timers.fetch_member_timer("tempban", ctx.guild.id, member.id)
            if existing is not None:
                expires = discord.utils.format_dt(existing.expires.replace(tzinfo=datetime.UTC), "R")
                await ctx.send_error(f"`{member}` is already temporarily banned (expires {expires}).")
            else:
                await ctx.send_error(f"`{member}` is already banned. Unban them first to apply a temporary ban.")
            return

        until = f"until {discord.utils.format_dt(duration.dt, 'F')}"

        with suppress(discord.HTTPException, AttributeError):
            await member.send(f"{Emojis.info} You have been banned from {ctx.guild.name} {until}. Reason: {reason}")  # type: ignore[union-attr]

        reason = safe_reason_append(reason, until)
        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await ctx.guild.ban(member, reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "tempban", member.id, ctx.author.id, reason)
        await self.bot.timers.create(
            duration.dt,
            "tempban",
            ctx.guild.id,
            ctx.author.id,
            member.id,
            created=ctx.message.created_at,
            timezone=zone or "UTC",
        )
        await ctx.send_success(f"Temporarily banned **{member}** until {discord.utils.format_dt(duration.dt, 'R')}.")

    @Cog.listener()
    async def on_tempban_timer_complete(self, timer: Timer) -> None:
        await self.bot.wait_until_ready()
        guild_id, mod_id, member_id = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        moderator = await self.bot.get_or_fetch_member(guild, mod_id)
        if moderator is None:
            try:
                moderator = await self.bot.fetch_user(mod_id)
            except discord.HTTPException:
                moderator = f"Mod ID {mod_id}"
            else:
                moderator = f"{moderator} (ID: {mod_id})"
        else:
            moderator = f"{moderator} (ID: {mod_id})"

        reason = f"Automatic unban from timer made on {timer.created} by {moderator}."
        await guild.unban(discord.Object(id=member_id), reason=reason)

    # MUTE

    @command(
        "mute",
        description="Mutes members indefinitely using the configured mute role.",
        hybrid=True,
        guild_only=True,
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles"],
    )
    @checks.can_mute()
    @describe(members="The members to mute.", reason="The reason for muting the members.")
    async def _mute(
        self,
        ctx: ModGuildContext,
        members: commands.Greedy[discord.Member],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Mutes members indefinitely using the configured mute role.
        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.
        To use this command, you need to be higher than the
        mute role in the hierarchy.

        Members who are already muted are skipped; use `tempmute` for a timed mute.
        """
        assert ctx.guild is not None
        if (total := len(members)) == 0:
            raise BadArgument("Missing members to mute.", "members")

        if reason is None:
            reason = default_reason(ctx.author)

        assert ctx.guild_config.mute_role_id is not None
        role_id = ctx.guild_config.mute_role_id
        role = discord.Object(id=role_id)

        if ctx.guild.me.top_role < ctx.guild.get_role(role_id):
            await ctx.send_error("I cannot mute a member with a role equal to or higher than the mute role.")
            return

        failed = 0
        skipped: list[str] = []
        for member in members:
            if member._roles.has(role_id):
                skipped.append(str(member))
                continue
            try:
                await member.add_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1
            else:
                self.bot.dispatch("mod_action", ctx.guild.id, "mute", member.id, ctx.author.id, reason)

        message = f"Muted [`{total - failed - len(skipped)}`/`{total}`] members."
        if skipped:
            message += f"\nAlready muted (skipped): {human_join([f'`{m}`' for m in skipped], final='and')}."
        await ctx.send_success(message)

    @command(
        "unmute",
        description="Unmutes members using the configured mute role.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles"],
    )
    @checks.can_mute()
    @describe(members="The members to unmute.", reason="The reason for unmuting the members.")
    async def _unmute(
        self,
        ctx: ModGuildContext,
        members: commands.Greedy[discord.Member],
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Unmutes members using the configured mute role.
        Works for normal mutes, tempmutes and self-mutes, cancelling any pending
        expiry timer so the member is not unmuted twice.

        You cannot remove your own self-mute -- a moderator has to do it for you.

        The bot must have Manage Roles permission and be above the muted role in the
        hierarchy, and you need to be higher than the mute role in the hierarchy.
        """
        assert ctx.guild is not None
        if (total := len(members)) == 0:
            raise BadArgument("Missing members to unmute.", "members")

        if reason is None:
            reason = default_reason(ctx.author)

        assert ctx.guild_config.mute_role_id is not None
        role_id = ctx.guild_config.mute_role_id
        role = discord.Object(id=role_id)

        if ctx.guild.me.top_role < ctx.guild.get_role(role_id):
            await ctx.send_error("I cannot mute a member with a role equal to or higher than the mute role.")
            return

        failed = 0
        blocked: list[str] = []
        for member in members:
            timer = await self.bot.timers.fetch_member_timer("tempmute", ctx.guild.id, member.id)
            # A self-mute stores the same id for both the moderator and the target (args[1] == args[2]).
            is_selfmute = timer is not None and timer.args[1] == member.id
            if is_selfmute and member.id == ctx.author.id:
                blocked.append(str(member))
                continue

            try:
                await member.remove_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1
            else:
                if timer is not None:
                    await self.bot.timers.delete_member_timer("tempmute", ctx.guild.id, member.id)
                self.bot.dispatch("mod_action", ctx.guild.id, "unmute", member.id, ctx.author.id, reason)

        message = f"Unmuted [`{total - failed - len(blocked)}`/`{total}`] members."
        if blocked:
            message += (
                f"\nYou cannot remove your own self-mute: {human_join([f'`{m}`' for m in blocked], final='and')}."
            )
        await ctx.send_success(message)

    @command(
        "tempmute",
        description="Temporarily mutes a member for the specified duration.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles"],
    )
    @checks.requires_timer()
    @checks.can_mute()
    @describe(
        duration="The duration to mute the member for. Must be a future Time.",
        member="The member to mute.",
        reason="The reason for muting the member.",
    )
    async def tempmute(
        self,
        ctx: ModGuildContext,
        duration: timetools.FutureTime,
        member: discord.Member,
        *,
        reason: Annotated[str | None, ActionReason] = None,
    ) -> None:
        """Temporarily mutes a member for the specified duration.
        The duration can be a short time form e.g., 30d or a more human
        duration such as "until thursday at 3PM" or a more concrete time
        such as "2024-12-31".
        **You need to quote the duration if it contains spaces.**

        Note that times are in UTC unless a timezone is specified
        using the 'timezone set' command.

        ### Important
        If you want to ban a member by ID, consider using the text version of this command.
        The App Commands version of this command does not support banning by ID.
        """
        assert ctx.guild is not None
        if reason is None:
            reason = default_reason(ctx.author)

        if error := check_member_hierarchy(ctx, member, action="mute", check_owner=False):
            await ctx.send_error(error)
            return

        role_id = ctx.guild_config.mute_role_id
        assert role_id is not None

        if member._roles.has(role_id):
            existing = await self.bot.timers.fetch_member_timer("tempmute", ctx.guild.id, member.id)
            if existing is not None:
                until = discord.utils.format_dt(existing.expires.replace(tzinfo=datetime.UTC), "R")
                kind = "self-muted" if existing.args[1] == member.id else "temporarily muted"
                await ctx.send_error(f"{member} is already {kind} (expires {until}). Unmute them first to change it.")
            else:
                await ctx.send_error(f"{member} is already muted. Unmute them first to apply a temporary mute.")
            return

        if ctx.guild.me.top_role < ctx.guild.get_role(role_id):
            await ctx.send_error("I cannot mute a member with a role equal to or higher than the mute role.")
            return

        await member.add_roles(discord.Object(id=role_id), reason=reason)
        self.bot.dispatch("mod_action", ctx.guild.id, "tempmute", member.id, ctx.author.id, reason)

        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await self.bot.timers.create(
            duration.dt,
            "tempmute",
            ctx.guild.id,
            ctx.author.id,
            member.id,
            role_id,
            created=ctx.message.created_at,
            timezone=zone or "UTC",
        )
        await ctx.send_success(f"Temporarily muted {member} until {discord.utils.format_dt(duration.dt, 'F')}.")

    @Cog.listener()
    async def on_tempmute_timer_complete(self, timer: Timer) -> None:
        await self.bot.wait_until_ready()
        guild_id, mod_id, member_id, role_id = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        member = await self.bot.get_or_fetch_member(guild, member_id)
        if member is None or not member._roles.has(role_id):
            self._mute_data_batch[guild_id].append((member_id, False))
            return

        if mod_id != member_id:
            moderator = await self.bot.get_or_fetch_member(guild, mod_id)
            if moderator is None:
                try:
                    moderator = await self.bot.fetch_user(mod_id)
                except discord.HTTPException:
                    moderator = f"Mod ID {mod_id}"
                else:
                    moderator = f"{moderator} (ID: {mod_id})"
            else:
                moderator = f"{moderator} (ID: {mod_id})"

            reason = f"Automatic unmute from timer made on {timer.created} by {moderator}."
        else:
            reason = f"Expiring self-mute made on {timer.created} by {member}"

        try:
            await member.remove_roles(discord.Object(id=role_id), reason=reason)
        except discord.HTTPException:
            self._mute_data_batch[guild_id].append((member_id, False))

    @group(
        "muterole",
        description="Shows and manages the configuration of the mute role.",
        guild_only=True,
        hybrid=True,
        fallback="show",
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles", "manage_channels"],
    )
    async def _mute_role(self, ctx: ModGuildContext) -> None:
        """Shows configuration of the mute role."""
        role = ctx.guild_config.mute_role if ctx.guild_config else None
        total = 0
        if role is not None:
            members = ctx.guild_config.muted_members.copy()
            members.update(r.id for r in role.members)
            total = len(members)
            role = f"{role} (ID: {role.id})"

        await ctx.send_success(f"Role: {role}\nMembers Muted: {total}")

    @_mute_role.command(
        "set",
        description="Sets the mute role to a pre-existing role.",
        guild_only=True,
        bot_permissions=["manage_roles"],
        user_permissions=["manage_roles", "manage_channels"],
    )
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(role="The role to set as the mute role.")
    async def mute_role_set(self, ctx: ModGuildContext, *, role: discord.Role) -> None:
        """Sets the mute role to a pre-existing role."""
        assert ctx.guild is not None
        if role.is_default():
            raise commands.BadArgument("You cannot set the default role as the mute role.")

        if role > ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:  # type: ignore[union-attr]
            raise commands.BadArgument("You cannot set a role higher than your top role as the mute role.")

        if role > ctx.guild.me.top_role:
            raise commands.BadArgument("I cannot set a role higher than my top role as the mute role.")

        has_pre_existing = ctx.guild_config is not None and ctx.guild_config.mute_role is not None
        merge: bool = False

        if has_pre_existing:
            view = PreExistingMuteRoleView(ctx.author)  # type: ignore[arg-type]
            view.message = await ctx.send_warning(
                "**There seems to be a pre-existing mute role set up.**\n\n"
                "If you want to merge the pre-existing member data with the new member data press the Merge button.\n"
                "If you want to replace pre-existing member data with the new member data press the Replace button.\n\n"
                "**Note: Merging is __slow__. It will also add the role to every possible member that needs it.**",
                view=view,
            )
            await view.wait()
            if view.merge is None:
                return
            merge = view.merge
        else:
            muted_members = len(role.members)
            if muted_members > 0:
                msg = f"{Emojis.warning} Are you sure you want to make this the mute role? It has {pluralize(muted_members):member}."
                confirm = await ctx.confirm(msg)
                if not confirm:
                    return

        async with ctx.typing():
            members = set()

            if ctx.guild_config and merge:
                members |= ctx.guild_config.muted_members
                reason = f"Action done by {ctx.author} (ID: {ctx.author.id}): Merging mute roles"
                async for member in self.bot.resolve_member_ids(ctx.guild, members):  # type: ignore[arg-type]
                    if not member._roles.has(role.id):
                        with suppress(discord.HTTPException):
                            await member.add_roles(role, reason=reason)

            members.update(m.id for m in role.members)
            await self.bot.db.moderation.set_mute_role(ctx.guild.id, role.id, list(members))

            escaped = discord.utils.escape_mentions(role.name)
            await ctx.send_success(
                f"Successfully set the {escaped} role as the mute role.\n\n"
                "**Note: Permission overwrites have not been changed.**"
            )

    @_mute_role.command(
        "update",
        description="Updates the permission overwrites of the mute role.",
        aliases=["sync"],
        guild_only=True,
        bot_permissions=["manage_roles", "manage_channels"],
        user_permissions=["manage_roles"],
    )
    @checks.can_mute()
    async def mute_role_update(self, ctx: ModGuildContext) -> None:
        """Automatically updates the permission overwrites of the mute role on the server."""
        assert ctx.guild is not None
        assert ctx.guild_config is not None
        assert ctx.guild_config.mute_role is not None

        async with ctx.typing():
            success, failure, skipped = await update_role_permissions(ctx.guild_config.mute_role, ctx.guild, ctx.author)  # type: ignore[arg-type]
            total = success + failure + skipped
            await ctx.send_info(
                f"Attempted to update {total} channel permissions. "
                f"[Updated: `{success}`, Failed: `{failure}`, Skipped (*no permissions*): `{skipped}`]"
            )

    @_mute_role.command(
        "create",
        description="Creates a mute role with the given name.",
        guild_only=True,
        bot_permissions=["manage_roles", "manage_channels"],
        user_permissions=["manage_roles"],
    )
    @describe(name="The name of the mute role to create.")
    async def mute_role_create(self, ctx: ModGuildContext, *, name: str) -> None:
        """Creates a mute role with the given name.
        This also updates the channels' permission overwrites accordingly if needed.
        """
        assert ctx.guild is not None
        guild_id = ctx.guild.id
        if ctx.guild_config is not None and ctx.guild_config.mute_role is not None:
            await ctx.send_error("A mute role has already been set up.")
            return

        try:
            role = await ctx.guild.create_role(name=name, reason=f"Mute Role Created By {ctx.author} (ID: {ctx.author.id})")
        except discord.HTTPException as e:
            await ctx.send_error(f"Failed to create role: {e}")
            return

        await ctx.db.moderation.create_mute_role(guild_id, role.id)

        confirm = await ctx.confirm(f"{Emojis.warning} Would you like to update the channel overwrites as well?")
        if not confirm:
            await ctx.send_success("Mute role successfully created.")
            return

        async with ctx.typing():
            success, failure, skipped = await update_role_permissions(role, ctx.guild, ctx.author)
            await ctx.send_success(
                f"Mute role successfully created. Overwrites: [Updated: {success}, Failed: {failure}, Skipped: {skipped}]"
            )

    @_mute_role.command(
        "unbind",
        aliases=["delete"],
        description="Unbinds a mute role without deleting it.",
        guild_only=True,
        user_permissions=["manage_roles"],
    )
    async def mute_role_unbind(self, ctx: ModGuildContext) -> None:
        """Unbinds a mute role without deleting it."""
        assert ctx.guild is not None
        guild_id = ctx.guild.id
        if ctx.guild_config is None or ctx.guild_config.mute_role is None:
            raise commands.BadArgument("This server does not have a mute role set up.")

        muted_members = len(ctx.guild_config.muted_members)
        if muted_members > 0:
            msg = f"Are you sure you want to unbind and unmute {pluralize(muted_members):member}?"
            confirm = await ctx.confirm(msg)
            if not confirm:
                return

        await self.bot.db.moderation.unbind_mute_role(guild_id)
        await ctx.send_success("Successfully unbound mute role.")

    @command(
        "selfmute",
        description="Temporarily mutes yourself for the specified duration.",
        guild_only=True,
        hybrid=True,
        bot_permissions=["manage_roles"],
    )
    @checks.requires_timer()
    @describe(duration="The duration to mute yourself for. Must be in a short time form e.g., 4h.")
    async def selfmute(self, ctx: ModGuildContext, *, duration: timetools.ShortTime) -> None:
        """Temporarily mutes yourself for the specified duration.
        The duration must be in a short time form e.g., 4h. Can
        only mute yourself for a maximum of 24 hours and a minimum
        of 5 minutes.

        **Don't ask a moderator to unmute you.**
        """
        assert ctx.guild is not None
        assert isinstance(ctx.author, discord.Member)
        role_id = ctx.guild_config.mute_role_id if ctx.guild_config else None
        if role_id is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        if ctx.author._roles.has(role_id):
            await ctx.send_error('You are already muted.')
            return

        if ctx.guild.me.top_role < discord.Object(id=role_id):
            await ctx.send_error('I cannot mute you with a role equal to or higher than the mute role.')
            return

        created_at = ctx.message.created_at
        if duration.dt > (created_at + datetime.timedelta(days=1)):
            raise commands.BadArgument('Duration is too long. Must be less than 24 hours.')

        if duration.dt < (created_at + datetime.timedelta(minutes=5)):
            raise commands.BadArgument('Duration is too short. Must be at least 5 minutes.')

        delta = timetools.human_timedelta(duration.dt, source=created_at)
        warning = f'Are you sure you want to be muted for {delta}?\n**Do not ask the moderators to undo this!**'
        confirm = await ctx.confirm(warning, ephemeral=True)
        if not confirm:
            return

        reason = f'Self-mute for {ctx.author} (ID: {ctx.author.id}) for {delta}'
        await ctx.author.add_roles(discord.Object(id=role_id), reason=reason)
        await self.bot.timers.create(
            duration.dt,
            'tempmute',
            ctx.guild.id,
            ctx.author.id,
            ctx.author.id,
            role_id,
            created=created_at
        )

        fmt_time = discord.utils.format_dt(duration.dt, 'f')
        await ctx.send_success(f'Selfmute ends **{fmt_time}**.\nBe sure not to bother anyone about it.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(Moderation(bot))
