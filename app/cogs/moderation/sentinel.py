from __future__ import annotations

import io
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, cast

import asyncpg
import discord

from app.core import Bot
from app.core.models import AppBadArgument
from app.core.views import ConfirmationView, LayoutView
from app.database.base import Sentinel, GuildConfig
from app.utils import checks, get_asset_url, merge_perms, pluralize
from config import Emojis

from .infractions import sync_permissions_with_progress

if TYPE_CHECKING:
    from .cog import Moderation

# Shared palette from klappstuhl.me dashboard
_BRAND = discord.Colour(0xD97757)  # --branding
_SUCCESS = discord.Colour(0x166534)  # --success-border


SENTINEL_DEFAULT_MESSAGE_TITLE: str = "Identity Verification"
SENTINEL_DEFAULT_MESSAGE_BODY: str = (
    "To access this server you must complete a quick verification.\n"
    "**Tap the button below to begin.**"
)


class SentinelSetupRoleView(LayoutView):
    """CV2 sub-view for selecting or creating the lockdown role."""

    def __init__(
        self,
        parent: SentinelSetUpView,
        selected_role: discord.Role | None,
        created_role: discord.Role | None,
        starter_role: discord.Role | None,
    ) -> None:
        super().__init__(timeout=300.0)
        self.selected_role: discord.Role | None = selected_role
        self.created_role = created_role
        self.starter_role: discord.Role | None = starter_role
        self.parent = parent

        self._role_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            min_values=1, max_values=1, placeholder="Pick an existing role..."
        )
        self._role_select.callback = self._on_role_select  # type: ignore[assignment]
        if selected_role is not None:
            self._role_select.default_values = [discord.SelectDefaultValue.from_role(selected_role)]

        self._create_btn: discord.ui.Button = discord.ui.Button(
            label="Create New Role", style=discord.ButtonStyle.green
        )
        self._create_btn.callback = self._on_create_role  # type: ignore[assignment]
        if created_role is not None:
            self._create_btn.disabled = True

        self._build_layout()

    def _build_layout(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=_BRAND)
        container.add_item(discord.ui.TextDisplay(
            "## Lockdown Role Setup\n"
            "Select an existing role to assign on join, or create a fresh one.\n"
            "-# The role will deny channel access until the member verifies."
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(self._role_select))
        container.add_item(discord.ui.ActionRow(self._create_btn))
        self.add_item(container)

    async def _on_role_select(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        role = self._role_select.values[0]
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits above mine in the hierarchy.", ephemeral=True
            )
            return

        if role >= interaction.user.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits above yours in the hierarchy.", ephemeral=True
            )
            return

        if role == self.starter_role:  # type: ignore[union-attr]
            await interaction.response.send_message(
                f"{Emojis.error} Cannot reuse the starter role here.", ephemeral=True
            )
            return

        channels = [
            ch
            for ch in interaction.guild.channels
            if isinstance(ch, discord.abc.Messageable) and not ch.permissions_for(role).read_messages  # type: ignore[arg-type]
        ]

        await interaction.response.defer(ephemeral=True)

        if channels:
            confirm = ConfirmationView(
                interaction.user, timeout=180.0, delete_after=True,
                content=(
                    f"This role needs permission overrides. Sync across "
                    f"{pluralize(len(channels)):channel}?"
                ),
            )
            confirm.message = await interaction.followup.send(view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                await interaction.followup.send(
                    f"{Emojis.success} Role set to {role.mention}.\n"
                    "Channel permissions were **not** synced — configure manually.",
                    ephemeral=True,
                )
            else:
                success, failure, skipped = await sync_permissions_with_progress(
                    interaction,
                    role,
                    self.parent.guild,
                    update_read_permissions=True,
                    channels=channels,  # type: ignore[arg-type]
                    label="Syncing lockdown role across channels",
                )
                total = success + failure + skipped
                await interaction.followup.send(
                    f"{Emojis.success} Role set to {role.mention}.\n"
                    f"Synced {total} channels: {success} ok, "
                    f"{failure} failed, {skipped} skipped.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                f"{Emojis.success} Role set to {role.mention}", ephemeral=True
            )

        self.selected_role = role
        self.stop()

    async def _on_create_role(self, interaction: discord.Interaction) -> None:
        try:
            role = await self.parent.guild.create_role(name="Unverified")
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"{Emojis.error} Could not create role: {e}", ephemeral=True
            )
            return

        self.created_role = role
        self.selected_role = role
        channels = [
            ch for ch in self.parent.guild.channels
            if isinstance(ch, discord.abc.Messageable)
        ]

        await interaction.response.defer(ephemeral=True)

        confirm = ConfirmationView(
            interaction.user, timeout=180.0, delete_after=True,
            content=(
                f"New role needs permission overrides. Sync across "
                f"{pluralize(len(channels)):channel}?"
            ),
        )
        confirm.message = await interaction.followup.send(view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            await interaction.followup.send(
                f"{Emojis.success} Created and set role to {role.mention}.\n"
                "Channel permissions were **not** synced — configure manually.",
                ephemeral=True,
            )
        else:
            success, failure, skipped = await sync_permissions_with_progress(
                interaction,
                role,
                self.parent.guild,
                update_read_permissions=True,
                channels=channels,
                label="Syncing lockdown role across channels",
            )
            total = success + failure + skipped
            await interaction.followup.send(
                f"{Emojis.success} Created and set role to {role.mention}.\n"
                f"Synced {total} channels: {success} ok, "
                f"{failure} failed, {skipped} skipped.",
                ephemeral=True,
            )
        self.stop()


class SentinelRateLimitModal(discord.ui.Modal, title="Auto-Trigger Threshold"):
    """Modal for configuring the join-rate auto-trigger."""

    rate = discord.ui.TextInput(
        label="Joins required", placeholder="5", min_length=1, max_length=3
    )
    per = discord.ui.TextInput(
        label="Within seconds", placeholder="5", min_length=1, max_length=2
    )

    def __init__(self) -> None:
        super().__init__(custom_id="sentinel-rate-limit-modal")
        self.final_rate: tuple[int, int] | None = None

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        try:
            rate = int(self.rate.value)
        except ValueError:
            await interaction.response.send_message(
                f"{Emojis.error} Joins must be a number.", ephemeral=True
            )
            return

        try:
            per = int(self.per.value)
        except ValueError:
            await interaction.response.send_message(
                f"{Emojis.error} Seconds must be a number.", ephemeral=True
            )
            return

        if rate <= 0 or per <= 0:
            await interaction.response.send_message(
                f"{Emojis.error} Both values must be positive.", ephemeral=True
            )
            return

        self.final_rate = (rate, per)
        await interaction.response.send_message(
            f"{Emojis.success} Auto-trigger set: **{rate}** joins "
            f"within **{per}s** activates sentinel.",
            ephemeral=True,
        )


class SentinelMessageModal(discord.ui.Modal, title="Verification Message"):
    """Modal for customizing the captcha verification message content."""

    header = discord.ui.TextInput(
        label="Title", style=discord.TextStyle.short,
        max_length=256, default=SENTINEL_DEFAULT_MESSAGE_TITLE
    )
    message = discord.ui.TextInput(
        label="Body", style=discord.TextStyle.long, max_length=2000, default=SENTINEL_DEFAULT_MESSAGE_BODY
    )

    def __init__(self, default: str) -> None:
        super().__init__()
        self.message.default = default

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.stop()


class SentinelChannelSelect(discord.ui.ChannelSelect["SentinelSetUpView"]):
    def __init__(self, sentinel: Sentinel) -> None:
        channel = sentinel.channel_id
        default_values = (
            [
                discord.SelectDefaultValue(
                    id=channel,  # type: ignore[arg-type]
                    type=discord.SelectDefaultValueType.channel,
                )
            ]
            if channel
            else []
        )

        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=default_values,
            placeholder="Select verification channel...",
        )
        self.bot: Bot = sentinel.bot
        self.sentinel: Sentinel = sentinel
        self.selected_channel: discord.TextChannel | None = None

    @staticmethod
    async def request_permission_sync(
        channel: discord.TextChannel,
        role: discord.Role,
        interaction: discord.Interaction,
    ) -> None:
        assert interaction.guild is not None
        role_perms = channel.permissions_for(role)
        everyone_perms = channel.permissions_for(interaction.guild.default_role)
        if not everyone_perms.read_messages and role_perms.read_messages:
            return

        confirm = ConfirmationView(
            interaction.user, timeout=180.0, delete_after=True,
            content=(
                f"Channel {channel.mention} needs permission adjustments — "
                f"{role.mention} must have access but @everyone should not.\n"
                "Apply automatic fix?"
            ),
        )
        confirm.message = await interaction.followup.send(view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            return

        reason = (
            f"Sentinel permission sync requested by "
            f"{interaction.user} (ID: {interaction.user.id})"
        )
        try:
            if everyone_perms.read_messages:
                overwrite = channel.overwrites_for(interaction.guild.default_role)
                overwrite.update(read_messages=False)
                await channel.set_permissions(
                    interaction.guild.default_role,
                    overwrite=overwrite, reason=reason,
                )
            if not role_perms.read_messages:
                overwrite = channel.overwrites_for(role)
                guild_perms = interaction.guild.me.guild_permissions
                merge_perms(
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
                await channel.set_permissions(
                    role, overwrite=overwrite, reason=reason
                )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"{Emojis.error} Could not edit permissions: {e}", ephemeral=True
            )

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        assert self.view is not None
        channel = self.values[0].resolve()
        if channel is None:
            await interaction.response.send_message(
                f"{Emojis.error} Channel could not be resolved.", ephemeral=True
            )
            return

        assert isinstance(channel, discord.TextChannel)
        perms = channel.permissions_for(self.view.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                f"{Emojis.error} I lack Send Messages or Embed Links "
                f"in that channel.",
                ephemeral=True,
            )
            return

        manage_roles = checks.has_manage_roles_overwrite(
            self.view.guild.me, channel
        )
        if not perms.administrator and not manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Permissions in that channel "
                f"(or Administrator globally).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        role = self.sentinel.role
        if role is not None:
            await self.request_permission_sync(channel, role, interaction)

        # The deployed verification message lives in the *old* channel, so it must be
        # removed when the channel changes. Remember whether one existed so we can
        # automatically redeploy it to the new channel instead of forcing the operator
        # to recreate it by hand.
        had_message = self.sentinel.message_id is not None
        old_message = self.sentinel.message
        if old_message is not None:
            with suppress(discord.HTTPException):
                await old_message.delete()

        await self.sentinel.edit(channel_id=channel.id, message_id=None)

        redeployed = False
        if had_message:
            verify_view = SentinelVerifyView(self.view.config, self.sentinel)
            try:
                new_message = await channel.send(view=verify_view)
            except discord.HTTPException:
                pass
            else:
                await self.sentinel.edit(message_id=new_message.id)
                redeployed = True

        if redeployed:
            await interaction.followup.send(
                f"{Emojis.success} Verification channel set to {channel.mention} — "
                f"the verification message was redeployed there.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"{Emojis.success} Verification channel set to {channel.mention}",
                ephemeral=True,
            )
        self.view.update_state()
        await interaction.edit_original_response(view=self.view)


class SentinelSetUpView(LayoutView):
    """The sentinel setup dashboard — single CV2 container.

    Everything (selects, buttons, text) lives inside one Container so it
    renders as a continuous card. Rebuilt on every state change.
    """

    def __init__(
        self,
        cog: Moderation,
        member: discord.Member,
        config: GuildConfig,
        sentinel: Sentinel,
    ) -> None:
        super().__init__(timeout=900.0, members=member, delete_on_timeout=True)
        self.cog = cog
        self.config = config
        self.sentinel = sentinel

        self.created_role: discord.Role | None = None
        self.selected_role: discord.Role | None = sentinel.role
        self.selected_starter_role: discord.Role | None = sentinel.starter_role
        self.selected_message_id: int | None = sentinel.message_id

        guild = sentinel.bot.get_guild(sentinel.id)
        assert guild is not None
        self.guild: discord.Guild = guild

        # -- interactive components (stable instances, mutated by update_state)
        self.channel_select = SentinelChannelSelect(sentinel)

        self.starter_role_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            min_values=1, max_values=1,
            placeholder="Assign a starter role on verify...",
        )
        self.starter_role_select.callback = self._on_starter_role  # type: ignore[assignment]

        self.setup_bypass_action: discord.ui.Select = discord.ui.Select(
            placeholder="Bypass action...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Kick", value="kick", emoji=Emojis.leave,
                    description="Kick members who participate before verifying.",
                ),
                discord.SelectOption(
                    label="Ban", value="ban", emoji=Emojis.banhammer,
                    description="Ban members who participate before verifying.",
                ),
            ],
        )
        self.setup_bypass_action.callback = self._on_bypass_action  # type: ignore[assignment]

        self.setup_role: discord.ui.Button = discord.ui.Button(
            label="Configure Role", style=discord.ButtonStyle.blurple
        )
        self.setup_role.callback = self._on_setup_role  # type: ignore[assignment]

        self.setup_message: discord.ui.Button = discord.ui.Button(
            label="Deploy Message", style=discord.ButtonStyle.blurple
        )
        self.setup_message.callback = self._on_setup_message  # type: ignore[assignment]

        self.setup_auto: discord.ui.Button = discord.ui.Button(
            label="Auto-Trigger", style=discord.ButtonStyle.blurple
        )
        self.setup_auto.callback = self._on_setup_auto  # type: ignore[assignment]

        self.toggle_flag: discord.ui.Button = discord.ui.Button(
            label="Activate", style=discord.ButtonStyle.green
        )
        self.toggle_flag.callback = self._on_toggle_flag  # type: ignore[assignment]

        self.update_state(invalidate=False)
        self._rebuild_layout()

    def _rebuild_layout(self) -> None:
        self.clear_items()

        enabled = (
            self.config.flags.sentinel
            and self.sentinel.started_at is not None
        )
        role = self.sentinel.role
        rate = self.sentinel.rate
        channel_id = self.sentinel.channel_id

        container = discord.ui.Container(accent_colour=_BRAND)

        # --- Header ---
        container.add_item(
            discord.ui.Section(
                "## Sentinel\n-# Entrance protection & captcha verification",
                accessory=discord.ui.Thumbnail(get_asset_url(self.guild)),
            )
        )
        container.add_item(discord.ui.Separator())
        status_label = (
            "**ACTIVE** — holding new members for verification"
            if enabled
            else "**STANDBY** — all members pass through freely"
        )
        container.add_item(
            discord.ui.TextDisplay(f"-# Status: {status_label}")
        )

        # --- Verification Channel ---
        container.add_item(discord.ui.Separator())
        ch_display = f"<#{channel_id}>" if channel_id else "`not set`"
        container.add_item(discord.ui.TextDisplay(
            f"### Verification Channel\n"
            f"The isolated channel where held members land and solve "
            f"their captcha.\n"
            f"-# Currently: {ch_display}"
        ))
        container.add_item(discord.ui.ActionRow(self.channel_select))

        # --- Lockdown Role ---
        container.add_item(discord.ui.Separator())
        role_display = role.mention if role else "`not set`"
        container.add_item(discord.ui.TextDisplay(
            f"### Lockdown Role\n"
            f"Assigned to every new join — strips channel access until "
            f"captcha is solved.\n"
            f"-# Currently: {role_display}"
        ))
        container.add_item(discord.ui.ActionRow(self.setup_role))

        # --- Post-Verification Role ---
        container.add_item(discord.ui.Separator())
        starter_role = self.sentinel.starter_role
        starter_display = starter_role.mention if starter_role else "`none`"
        container.add_item(discord.ui.TextDisplay(
            f"### Post-Verification Role\n"
            f"Optional role granted once a member passes verification.\n"
            f"-# Currently: {starter_display}"
        ))
        container.add_item(discord.ui.ActionRow(self.starter_role_select))

        # --- Captcha Message ---
        container.add_item(discord.ui.Separator())
        msg_deployed = self.sentinel.message_id is not None
        msg_display = "`deployed`" if msg_deployed else "`awaiting deployment`"
        container.add_item(discord.ui.TextDisplay(
            f"### Captcha Message\n"
            f"The verification embed with the solve button, posted in the "
            f"channel above.\n"
            f"-# Status: {msg_display}"
        ))
        container.add_item(discord.ui.ActionRow(self.setup_message))

        # --- Enforcement Rules ---
        container.add_item(discord.ui.Separator())
        rate_display = (
            f"`{rate[0]}` joins / `{rate[1]}s`" if rate else "`disabled`"
        )
        bypass_display = (self.sentinel.bypass_action or "kick").upper()
        container.add_item(discord.ui.TextDisplay(
            f"### Enforcement Rules\n"
            f"**Bypass action** — `{bypass_display}` any member who speaks "
            f"or joins voice before verifying.\n"
            f"**Auto-trigger** — {rate_display} activates sentinel when "
            f"join velocity spikes."
        ))
        container.add_item(discord.ui.ActionRow(self.setup_bypass_action))
        container.add_item(
            discord.ui.ActionRow(self.setup_auto, self.toggle_flag)
        )

        self.add_item(container)

    def update_state(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self.cog.bot.db.signals.fire(
                "sentinel_changed", self.sentinel.id
            )

        role = self.sentinel.role
        if role is not None:
            label = f'Change: "{role.name}"'
            self.setup_role.label = (
                "Change Role" if len(label) > 50 else label
            )
            self.setup_role.style = discord.ButtonStyle.grey
        else:
            self.setup_role.label = "Configure Role"
            self.setup_role.style = discord.ButtonStyle.blurple

        rate = self.sentinel.rate
        if rate is not None:
            rate_val, per_val = rate
            self.setup_auto.label = f"Auto: {rate_val}/{per_val}s"
            self.setup_auto.style = discord.ButtonStyle.grey
        else:
            self.setup_auto.label = "Auto-Trigger"
            self.setup_auto.style = discord.ButtonStyle.blurple

        enabled = (
            self.config.flags.sentinel
            and self.sentinel.started_at is not None
        )
        if enabled:
            self.toggle_flag.label = "Deactivate"
            self.toggle_flag.style = discord.ButtonStyle.red
        else:
            self.toggle_flag.label = "Activate"
            self.toggle_flag.style = discord.ButtonStyle.green

        for option in self.setup_bypass_action.options:
            option.default = option.value == self.sentinel.bypass_action

        if self.sentinel.starter_role:
            self.starter_role_select.default_values = [
                discord.SelectDefaultValue.from_role(  # type: ignore[arg-type]
                    self.sentinel.starter_role
                )
            ]

        self.setup_message.disabled = False
        self.channel_select.disabled = False
        self.setup_role.disabled = False
        self.starter_role_select.disabled = False

        channel_id = self.sentinel.channel_id
        if channel_id is None:
            self.setup_message.disabled = True

        if self.sentinel.message_id is not None:
            self.setup_message.disabled = True

        if self.sentinel.started_at is not None:
            self.channel_select.disabled = True
            self.setup_role.disabled = True
            self.starter_role_select.disabled = True
            self.setup_message.disabled = True

        if not enabled:
            self.toggle_flag.disabled = self.sentinel.requires_setup

        self._rebuild_layout()

    def stop(self) -> None:
        super().stop()
        self.cog._sentinel_menus.pop(self.sentinel.id, None)

    async def _on_starter_role(
        self, interaction: discord.Interaction
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        role = self.starter_role_select.values[0]
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits above mine in the hierarchy.",
                ephemeral=True,
            )
            return

        if role >= interaction.user.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits above yours in the hierarchy.",
                ephemeral=True,
            )
            return

        if role == self.selected_role or role == self.created_role:  # type: ignore[arg-type]
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use the same role for both "
                f"lockdown and starter.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{Emojis.success} Starter role set to {role.mention}.",
            ephemeral=True,
        )

        self.selected_starter_role = role
        if self.selected_starter_role is not None:  # type: ignore[arg-type]
            await self.sentinel.edit(
                starter_role_id=self.selected_starter_role.id  # type: ignore[arg-type]
            )

        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_bypass_action(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        value: Literal["ban", "kick"] = self.setup_bypass_action.values[0]  # type: ignore[arg-type]
        await self.sentinel.edit(bypass_action=value)
        await interaction.followup.send(
            f"{Emojis.success} Bypass action set to **{value}**.",
            ephemeral=True,
        )

        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_setup_role(
        self, interaction: discord.Interaction
    ) -> None:
        if not interaction.app_permissions.manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Roles permission.",
                ephemeral=True,
            )
            return

        view = SentinelSetupRoleView(
            self, self.selected_role,
            self.created_role, self.selected_starter_role,  # type: ignore[arg-type]
        )
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()
        await view.wait()
        self.created_role = view.created_role
        self.selected_role = view.selected_role
        if self.selected_role is not None:
            await self.sentinel.edit(role_id=self.selected_role.id)

            channel = self.sentinel.channel
            if channel is not None:
                await SentinelChannelSelect.request_permission_sync(
                    channel, self.selected_role, interaction
                )

        with suppress(discord.HTTPException):
            if view.message is not None:
                await view.message.delete()

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    async def _on_setup_message(
        self, interaction: discord.Interaction
    ) -> None:
        channel = self.sentinel.channel
        if self.sentinel.role is None:
            await interaction.response.send_message(
                f"{Emojis.error} Set up the lockdown role first.",
                ephemeral=True,
            )
            return
        if self.sentinel.message is not None:
            await interaction.response.send_message(
                f"{Emojis.error} A verification message is already deployed.",
                ephemeral=True,
            )
            return
        if channel is None:
            await interaction.response.send_message(
                f"{Emojis.error} Select a verification channel first.",
                ephemeral=True,
            )
            return

        modal = SentinelMessageModal(SENTINEL_DEFAULT_MESSAGE_BODY)
        await interaction.response.send_modal(modal)
        await modal.wait()

        # The message posted to the channel uses the SentinelVerifyView
        # CV2 layout so it's a branded card with the button embedded.
        verify_view = SentinelVerifyView(
            self.config, self.sentinel,
            title=modal.header.value or SENTINEL_DEFAULT_MESSAGE_TITLE,
            body=modal.message.value or SENTINEL_DEFAULT_MESSAGE_BODY,
        )
        try:
            message = await channel.send(view=verify_view)
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"{Emojis.error} Failed to send: {e}", ephemeral=True
            )
        else:
            await self.sentinel.edit(message_id=message.id)
            await interaction.followup.send(
                f"{Emojis.success} Verification message deployed.",
                ephemeral=True,
            )

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    @staticmethod
    async def __rate_limit_modal_response(
        existing_rate: tuple[int, int], interaction: discord.Interaction
    ) -> tuple[int, int] | None:
        modal = SentinelRateLimitModal()
        rate, per = existing_rate
        modal.rate.default = str(rate)
        modal.per.default = str(per)
        await interaction.response.send_modal(modal)
        await interaction.delete_original_response()
        await modal.wait()
        if modal.final_rate:
            return modal.final_rate
        return None

    async def _on_setup_auto(
        self, interaction: discord.Interaction
    ) -> None:
        rate = self.sentinel.rate
        if rate is not None:
            view = ConfirmationView(
                interaction.user,
                true="Update",
                false="Remove",
                hook=partial(self.__rate_limit_modal_response, rate),
                delete_after=True,
                content="Auto-trigger is already configured. Update or remove it?",
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            view.message = await interaction.original_response()
            await view.wait()
            new_rate = None if not view.value else view.hook_value
            await self.sentinel.edit(rate=new_rate)
        else:
            modal = SentinelRateLimitModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            if modal.final_rate is not None:
                await self.sentinel.edit(rate=modal.final_rate)

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    async def _on_toggle_flag(
        self, interaction: discord.Interaction
    ) -> None:
        enabled = self.sentinel.started_at is not None
        if enabled:
            newest = await self.cog.bot.db.get_guild_sentinel(
                self.sentinel.id  # type: ignore[arg-type]
            )
            if newest is not None:
                self.sentinel = newest

            members = self.sentinel.pending_members
            if members:
                confirm = ConfirmationView(
                    interaction.user, timeout=180.0, delete_after=True,
                    content=(
                        f"**{pluralize(members):member}** "
                        f"{pluralize(members):is|are!} still pending.\n"
                        "Deactivating will unblock all of them in the "
                        "background. Continue?"
                    ),
                )
                await interaction.response.send_message(view=confirm, ephemeral=True)
                confirm.message = await interaction.original_response()
                await confirm.wait()
                if not confirm.value:
                    return
            else:
                await interaction.response.defer()

            await self.sentinel.disable()
            await interaction.followup.send(
                f"{Emojis.success} Sentinel deactivated."
            )
        else:
            try:
                await self.sentinel.enable()
            except asyncpg.IntegrityConstraintViolationError:
                await interaction.response.send_message(
                    f"{Emojis.error} Cannot activate — ensure role, channel, "
                    f"and message are all configured."
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"{Emojis.error} Activation failed: {e}"
                )
            else:
                await interaction.response.send_message(
                    f"{Emojis.success} Sentinel activated. "
                    f"New members are now held."
                )

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]


class SentinelVerifyView(LayoutView):
    """The persistent CV2 verification card posted in the verification channel.

    Contains a branded container with the customizable title/body text and the
    dynamic verify button embedded directly inside.
    """

    def __init__(
        self,
        config: GuildConfig | None,
        sentinel: Sentinel | None,
        *,
        title: str = SENTINEL_DEFAULT_MESSAGE_TITLE,
        body: str = SENTINEL_DEFAULT_MESSAGE_BODY,
    ) -> None:
        super().__init__(timeout=None)
        self.config = config
        self.sentinel = sentinel

        container = discord.ui.Container(accent_colour=_SUCCESS)
        container.add_item(discord.ui.TextDisplay(
            f"## {title}\n{body}"
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "-# This message was set up by the moderators of this server. "
            "This bot will never ask for personal information and is not "
            "affiliated with Discord."
        ))
        container.add_item(discord.ui.ActionRow(SentinelVerifyButton(config, sentinel)))
        self.add_item(container)


class SentinelVerifyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template="sentinel:verify:captcha",
):
    """The dynamic handler for the captcha verify button."""

    def __init__(
        self,
        config: GuildConfig | None,
        sentinel: Sentinel | None,
    ) -> None:
        super().__init__(discord.ui.Button(
            label="Begin Verification",
            style=discord.ButtonStyle.green,
            custom_id="sentinel:verify:captcha",
        ))
        self.config = config
        self.sentinel = sentinel

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> SentinelVerifyButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} This feature is temporarily unavailable.",
                ephemeral=True,
            )
            raise AppBadArgument(
                f"{Emojis.error} Moderation cog is not loaded"
            )
        cog = cast("Moderation", _cog)

        config = await cog.bot.db.get_guild_config(interaction.guild_id)
        if config is None:
            return cls(None, None)

        sentinel = await cog.bot.db.get_guild_sentinel(
            interaction.guild_id
        )
        return cls(config, sentinel)

    async def interaction_check(
        self, interaction: discord.Interaction, /
    ) -> bool:
        if interaction.guild_id is None:
            return False

        if self.config is None or not self.config.flags.sentinel:
            await interaction.response.send_message(
                f"{Emojis.error} Verification is not active.",
                ephemeral=True,
            )
            return False

        if self.sentinel is None or self.sentinel.started_at is None:
            await interaction.response.send_message(
                f"{Emojis.error} Verification is not active.",
                ephemeral=True,
            )
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        if not self.sentinel.is_blocked(interaction.user.id):
            if self.sentinel.has_role(interaction.user):
                await self.sentinel.block(interaction.user)
                return True

            await interaction.response.send_message(
                f"{Emojis.success} You are already verified.",
                ephemeral=True,
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        assert self.sentinel is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.abc.GuildChannel)

        await interaction.response.defer(ephemeral=True)

        captcha = await self.sentinel.bot.render.captcha()

        await interaction.channel.set_permissions(
            interaction.user,
            reason=(
                f"Sentinel Verification (ID: {interaction.user.id})"
            ),
            send_messages=True,
        )

        # Captcha challenge as a CV2 card. The MediaGallery only references the image by
        # ``attachment://captcha.png``, so the file itself must be uploaded alongside the
        # view or Discord rejects the body ("referenced attachment was not found").
        captcha_view = _CaptchaChallengeView(captcha.file)
        message = await interaction.followup.send(
            view=captcha_view, file=captcha.file, ephemeral=True
        )

        try:
            msg = await interaction.client.wait_for(
                "message",
                check=lambda m: (
                    m.author.id == interaction.user.id
                    and m.channel.id == interaction.channel.id
                ),
                timeout=90.0,
            )
        except TimeoutError:
            await message.edit(
                content=f"{Emojis.error} Time expired. "
                f"Press the button again to retry.",
                view=None,
                attachments=[],
            )
            return
        else:
            await msg.delete()
        finally:
            await interaction.channel.set_permissions(
                interaction.user,
                reason=(
                    f"Sentinel Verification (ID: {interaction.user.id})"
                ),
                send_messages=False,
            )

        if msg.content != captcha.text:
            await message.edit(
                content=f"{Emojis.error} Incorrect. "
                f"Press the button to try again.",
                view=None,
                attachments=[],
            )
            return

        await self.sentinel.unblock(interaction.user)
        await interaction.followup.send(
            f"{Emojis.success} Verification complete — welcome to the "
            f"server!",
            ephemeral=True,
        )


class _CaptchaChallengeView(LayoutView):
    """Ephemeral CV2 card shown to the user with the captcha image."""

    def __init__(self, file: discord.File) -> None:
        super().__init__(timeout=90.0)
        from discord.ui.media_gallery import MediaGalleryItem

        container = discord.ui.Container(accent_colour=_BRAND)
        container.add_item(discord.ui.TextDisplay(
            "## Solve the Captcha\n"
            "Type the **6 characters** shown below. Case-sensitive.\n"
            "-# You have 90 seconds. Your answer will be deleted "
            "automatically."
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.MediaGallery(MediaGalleryItem(file))
        )
        self.add_item(container)


class SentinelAlertResolveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template="sentinel:alert:resolve",
):
    """Button on the alert message to stand down the sentinel."""

    def __init__(self, sentinel: Sentinel | None) -> None:
        super().__init__(discord.ui.Button(
            label="Stand Down",
            style=discord.ButtonStyle.blurple,
            custom_id="sentinel:alert:resolve",
        ))
        self.sentinel = sentinel

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> SentinelAlertResolveButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} This feature is temporarily unavailable.",
                ephemeral=True,
            )
            raise AppBadArgument(
                f"{Emojis.error} Moderation cog is not loaded"
            )
        cog = cast("Moderation", _cog)

        sentinel = await cog.bot.db.get_guild_sentinel(
            interaction.guild_id  # type: ignore[arg-type]
        )
        return cls(sentinel)

    async def interaction_check(
        self, interaction: discord.Interaction[Bot], /
    ) -> bool:
        if interaction.guild_id is None:
            return False
        if self.sentinel is None or self.sentinel.started_at is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sentinel is already inactive.",
                ephemeral=True,
            )
            return False
        return True

    async def callback(
        self, interaction: discord.Interaction[Bot]
    ) -> Any:
        assert self.sentinel is not None
        members = self.sentinel.pending_members
        if members:
            confirm = ConfirmationView(
                interaction.user, timeout=180.0,
                content=(
                    f"**{pluralize(members):member}** "
                    f"{pluralize(members):is|are!} still pending.\n"
                    "Standing down will unblock everyone in the background. "
                    "Proceed?"
                ),
            )
            await interaction.response.send_message(view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                return
        else:
            await interaction.response.defer()

        await self.sentinel.disable()
        await interaction.followup.send(
            f"{Emojis.success} Sentinel stood down.", ephemeral=True
        )
        if interaction.message is not None:
            await interaction.message.edit(view=None)


class SentinelAlertMassbanButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template="sentinel:alert:massban",
):
    """Button on the alert message to mass-ban detected raiders."""

    def __init__(self, cog: Moderation) -> None:
        super().__init__(discord.ui.Button(
            label="Ban All Raiders",
            style=discord.ButtonStyle.red,
            custom_id="sentinel:alert:massban",
        ))
        self.cog: Moderation = cog

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> SentinelAlertMassbanButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} This feature is temporarily unavailable.",
                ephemeral=True,
            )
            raise AppBadArgument(
                f"{Emojis.error} Moderation cog is not loaded"
            )
        return cls(cast("Moderation", _cog))

    async def interaction_check(
        self, interaction: discord.Interaction, /
    ) -> bool:
        if interaction.guild_id is None:
            return False

        if not interaction.app_permissions.ban_members:
            await interaction.response.send_message(
                f"{Emojis.error} I lack Ban Members permission.",
                ephemeral=True,
            )
            return False

        if not interaction.permissions.ban_members:
            await interaction.response.send_message(
                f"{Emojis.error} You lack Ban Members permission.",
                ephemeral=True,
            )
            return False

        return True

    async def callback(
        self, interaction: discord.Interaction[Bot]
    ) -> None:
        assert interaction.guild_id is not None
        assert interaction.guild is not None
        assert interaction.message is not None

        members = self.cog._spam_check[interaction.guild_id].flagged_users
        if not members:
            await interaction.response.send_message(
                f"{Emojis.error} No raiders detected at this time.",
                ephemeral=True,
            )
            return

        now = interaction.created_at
        members = sorted(members.values(), key=lambda m: m.joined_at or now)
        fmt = "\n".join(
            f"{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}"
            for m in members
        )
        content = (
            f"Current Time: {discord.utils.utcnow()}\n"
            f"Total members: {len(members)}\n{fmt}"
        )
        file = discord.File(
            io.BytesIO(content.encode("utf-8")), filename="members.txt"
        )
        confirm = ConfirmationView(
            interaction.user, timeout=180.0,
            content=f"Banning **{pluralize(len(members)):member}**. Confirm?",
        )
        await interaction.response.send_message(view=confirm, file=file)
        await confirm.wait()
        if not confirm.value:
            return

        count = 0
        reason = (
            f"{interaction.user} (ID: {interaction.user.id}): Raid detected"
        )
        for member in members:
            try:
                await interaction.guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await interaction.followup.send(
            f"{Emojis.success} Banned **{count}/{len(members)}** raiders."
        )
