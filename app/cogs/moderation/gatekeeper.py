from __future__ import annotations

import io
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, cast

import asyncpg
import discord

from app.core import Bot
from app.core.models import AppBadArgument
from app.core.views import ConfirmationView, LayoutView, View
from app.database.base import Gatekeeper, GuildConfig
from app.utils import checks, get_asset_url, helpers, merge_perms, pluralize
from config import Emojis

from .infractions import update_role_permissions

if TYPE_CHECKING:
    from .cog import Moderation


class GatekeeperSetupRoleView(View):
    """A view that is used to set up the gatekeeper role.

    This view is used to set up the gatekeeper role, it allows the user to either select a pre-existing role or create a new
    role to automatically assign to new members. This view also allows the user to select the starter role which is the role
    that is given to the user when they first join the server.

    This view is used in the `GatekeeperSetUpView` and is not meant to be used on its own.

    Attributes
    -----------
    parent: GatekeeperSetUpView
        The parent view that this view is attached to.
    selected_role: discord.Role | None
        The role that the user has selected.
    created_role: discord.Role | None
        The role that the user has created.
    starter_role: discord.Role | None
        The role that the user has selected as the starter role.
    """

    def __init__(
        self,
        parent: GatekeeperSetUpView,
        selected_role: discord.Role | None,
        created_role: discord.Role | None,
        starter_role: discord.Role | None,
    ) -> None:
        super().__init__(timeout=300.0)
        self.selected_role: discord.Role | None = selected_role
        self.created_role = created_role
        self.starter_role: discord.Role | None = starter_role
        self.parent = parent
        if selected_role is not None:
            self.role_select.default_values = [discord.SelectDefaultValue.from_role(selected_role)]

        if self.created_role is not None:
            self.create_role.disabled = True

    @discord.ui.select(
        cls=discord.ui.RoleSelect, min_values=1, max_values=1, placeholder="Choose the automatically assigned role"
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        role = select.values[0]
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use this role as it is higher than my role in the hierarchy.", ephemeral=True
            )
            return

        if role >= interaction.user.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use this role as it is higher than your role in the hierarchy.", ephemeral=True
            )
            return

        if role == self.starter_role:  # type: ignore[union-attr]
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use this role as it is the starter role.", ephemeral=True
            )
            return

        channels = [
            ch
            for ch in interaction.guild.channels
            if isinstance(ch, discord.abc.Messageable) and not ch.permissions_for(role).read_messages  # type: ignore[arg-type]
        ]

        await interaction.response.defer(ephemeral=True)

        if channels:
            embed = discord.Embed(
                title="Gatekeeper Configuration - Role",
                description=(
                    "In order for this role to work, it requires editing the permissions in every applicable channel.\n"
                    f"Would you like to edit the permissions of potentially {pluralize(len(channels)):channel}?"
                ),
                colour=helpers.Colour.light_grey(),
            )
            confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
            confirm.message = await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                embed = discord.Embed(
                    title="Gatekeeper Configuration - Role",
                    description=(
                        f"{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n"
                        "\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n"
                        "Please edit the permissions of applicable channels to block the user from accessing it when possible."
                    ),
                    colour=helpers.Colour.lime_green(),
                )
            else:
                assert isinstance(interaction.channel, discord.abc.Messageable)
                async with interaction.channel.typing():  # type: ignore[union-attr]
                    success, failure, skipped = await update_role_permissions(
                        role,
                        self.parent.guild,
                        interaction.user,
                        update_read_permissions=True,
                        channels=channels,  # type: ignore[arg-type]
                    )
                    total = success + failure + skipped
                    embed = discord.Embed(
                        title="Gatekeeper Configuration - Role",
                        description=(
                            f"{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n"
                            f"Attempted to update {total} channel permissions: "
                            f"[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]"
                        ),
                        colour=helpers.Colour.lime_green(),
                    )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                f"{Emojis.success} Successfully set the automatically assigned role to {role.mention}", ephemeral=True
            )

        self.selected_role = role
        self.stop()

    @discord.ui.button(label="Create New Role", style=discord.ButtonStyle.blurple)
    async def create_role(self, interaction: discord.Interaction, _) -> None:
        try:
            role = await self.parent.guild.create_role(name="Unverified")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"{Emojis.error} Could not create role: {e}", ephemeral=True)
            return

        self.created_role = role
        self.selected_role = role
        channels = [ch for ch in self.parent.guild.channels if isinstance(ch, discord.abc.Messageable)]

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="Gatekeeper Configuration - Role",
            description=(
                "In order for this role to work, it requires editing the permissions in every applicable channel.\n"
                f"Would you like to edit the permissions of potentially {pluralize(len(channels)):channel}?"
            ),
            colour=helpers.Colour.light_grey(),
        )
        confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
        confirm.message = await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            embed = discord.Embed(
                title="Gatekeeper Configuration - Role",
                description=(
                    f"{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n"
                    "\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n"
                    "Please edit the permissions of applicable channels to block the user from accessing it when possible."
                ),
                colour=helpers.Colour.lime_green(),
            )
        else:
            async with interaction.channel.typing():  # type: ignore[union-attr]
                success, failure, skipped = await update_role_permissions(
                    role, self.parent.guild, interaction.user, update_read_permissions=True, channels=channels
                )
                total = success + failure + skipped
                embed = discord.Embed(
                    title="Gatekeeper Configuration - Role",
                    description=(
                        f"{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n"
                        f"Attempted to update {total} channel permissions: "
                        f"[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]"
                    ),
                    colour=helpers.Colour.lime_green(),
                )
        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()


class GatekeeperRateLimitModal(discord.ui.Modal, title="Join Rate Trigger"):
    """A modal that is used to set the join rate trigger for the gatekeeper."""

    rate = discord.ui.TextInput(label="Number of Joins", placeholder="5", min_length=1, max_length=3)
    per = discord.ui.TextInput(label="Number of seconds", placeholder="5", min_length=1, max_length=2)

    def __init__(self) -> None:
        super().__init__(custom_id="gatekeeper-rate-limit-modal")
        self.final_rate: tuple[int, int] | None = None

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        try:
            rate = int(self.rate.value)
        except ValueError:
            await interaction.response.send_message(
                f"{Emojis.error} Invalid number of joins given, must be a number.", ephemeral=True
            )
            return

        try:
            per = int(self.per.value)
        except ValueError:
            await interaction.response.send_message(
                f"{Emojis.error} Invalid number of seconds given, must be a number.", ephemeral=True
            )
            return

        if rate <= 0 or per <= 0:
            await interaction.response.send_message(
                f"{Emojis.error} Joins and seconds cannot be negative or zero", ephemeral=True
            )
            return

        self.final_rate = (rate, per)
        await interaction.response.send_message(
            f"{Emojis.success} Successfully set auto trigger join rate to more than {pluralize(rate):member join} in {per} seconds",
            ephemeral=True,
        )


class GatekeeperMessageModal(discord.ui.Modal, title="Starter Message"):
    """A modal that is used to set the starter message for the gatekeeper."""

    header = discord.ui.TextInput(
        label="Title", style=discord.TextStyle.short, max_length=256, default="Verification Required"
    )
    message = discord.ui.TextInput(label="Content", style=discord.TextStyle.long, max_length=2000)

    def __init__(self, default: str) -> None:
        super().__init__()
        self.message.default = default

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.stop()


class GatekeeperChannelSelect(discord.ui.ChannelSelect["GatekeeperSetUpView"]):
    def __init__(self, gatekeeper: Gatekeeper) -> None:
        channel = gatekeeper.channel_id
        default_values = (
            [
                discord.SelectDefaultValue(id=channel, type=discord.SelectDefaultValueType.channel)  # type: ignore[arg-type]
            ]
            if channel
            else []
        )

        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=default_values,
            placeholder="Select a channel to force members to see when joining",
        )
        self.bot: Bot = gatekeeper.bot
        self.gatekeeper: Gatekeeper = gatekeeper
        self.selected_channel: discord.TextChannel | None = None

    @staticmethod
    async def request_permission_sync(
        channel: discord.TextChannel, role: discord.Role, interaction: discord.Interaction
    ) -> None:
        assert interaction.guild is not None
        role_perms = channel.permissions_for(role)
        everyone_perms = channel.permissions_for(interaction.guild.default_role)
        if not everyone_perms.read_messages and role_perms.read_messages:
            return

        embed = discord.Embed(
            title="Gatekeeper Configuration - Permission Sync",
            description=(
                f"The permissions for {channel.mention} seem to not be properly set up, would you like the bot to set it up for you?\n"
                f"The channel requires the {role.mention} role to have access to it but the @everyone role should not."
            ),
            colour=helpers.Colour.lime_green(),
        )
        confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
        confirm.message = await interaction.followup.send(embed=embed, ephemeral=True, view=confirm)
        await confirm.wait()
        if not confirm.value:
            return

        reason = f"Gatekeeper permission sync requested by {interaction.user} (ID: {interaction.user.id})"
        try:
            if everyone_perms.read_messages:
                overwrite = channel.overwrites_for(interaction.guild.default_role)
                overwrite.update(read_messages=False)
                await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
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
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
        except discord.HTTPException as e:
            await interaction.followup.send(f"{Emojis.error} Could not edit permissions: {e}", ephemeral=True)

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        assert self.view is not None
        channel = self.values[0].resolve()
        if channel is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, somehow this channel did not resolve on my end.", ephemeral=True
            )
            return

        assert isinstance(channel, discord.TextChannel)
        perms = channel.permissions_for(self.view.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot send messages or embeds to this channel, please select another channel or provide those permissions",
                ephemeral=True,
            )
            return

        manage_roles = checks.has_manage_roles_overwrite(self.view.guild.me, channel)
        if not perms.administrator and not manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} Since I do not have Administrator permission, I require Manage Permissions permission in that channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        role = self.gatekeeper.role
        if role is not None:
            await self.request_permission_sync(channel, role, interaction)

        message = self.gatekeeper.message
        if message is not None:
            await message.delete()

        await self.gatekeeper.edit(channel_id=channel.id, message_id=None)
        await interaction.followup.send(
            f"{Emojis.success} Successfully changed channel to {channel.mention}", ephemeral=True
        )
        self.view.update_state()
        await interaction.edit_original_response(view=self.view)


#: The explanatory text shown at the top of the gatekeeper setup dashboard.
GATEKEEPER_INFO = (
    "Gatekeeper is a feature that automatically assigns a role to a member when they join, "
    "for the sole purpose of blocking them from accessing the server.\n"
    "The user must press a button in order to verify themselves and have their role removed.\n\n"
    "**In order to set up gatekeeper, a few things are required:**\n"
    "- A channel that locked users will see but regular users will not.\n"
    "- A role that is assigned when users join.\n"
    "- A message that the bot sends in the channel with the verify button.\n\n"
    "**Optional Settings:**\n"
    "- A role that is assigned when users finish the verification. (Starter Role)\n\n"
    "**There are also settings to help configure some aspects of it:**\n"
    '- "Auto" automatically triggers the gatekeeper if N members join in a span of M seconds\n'
    '- "Bypass Action" configures what action is taken when a user talks or joins voice before verifying\n\n'
    "Note that once gatekeeper is enabled, even by auto, it must be manually disabled.\n\n"
    f"{Emojis.info} The Users can verify by solving an image captcha consisting of 6 random letters "
    "they need to type into the chat."
)


class GatekeeperSetUpView(LayoutView):
    """The gatekeeper setup dashboard, rendered with Components V2.

    The explanatory header lives in a :class:`~discord.ui.Container`; the channel/role
    selects, bypass-action select and the role/message/auto/toggle buttons live in
    :class:`~discord.ui.ActionRow`s beneath it. The control components are stable
    instances mutated in place by :meth:`update_state` (then the message is re-edited),
    so navigation behaves exactly like the old embed-based view.
    """

    def __init__(self, cog: Moderation, member: discord.Member, config: GuildConfig, gatekeeper: Gatekeeper) -> None:
        super().__init__(timeout=900.0, members=member, delete_on_timeout=True)
        self.cog = cog
        self.config = config
        self.gatekeeper = gatekeeper

        self.created_role: discord.Role | None = None
        self.selected_role: discord.Role | None = gatekeeper.role
        self.selected_starter_role: discord.Role | None = gatekeeper.starter_role
        self.selected_message_id: int | None = gatekeeper.message_id

        guild = gatekeeper.bot.get_guild(gatekeeper.id)
        assert guild is not None
        self.guild: discord.Guild = guild

        # -- control components (stable instances, mutated by update_state) --
        self.channel_select = GatekeeperChannelSelect(gatekeeper)

        self.starter_role_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            min_values=1, max_values=1, placeholder="Choose the automatically assigned starter role"
        )
        self.starter_role_select.callback = self._on_starter_role  # type: ignore[assignment]

        self.setup_bypass_action: discord.ui.Select = discord.ui.Select(
            placeholder="Select a bypass action...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Kick User", value="kick", emoji=Emojis.leave,
                    description="Kick the member if they talk before verifying.",
                ),
                discord.SelectOption(
                    label="Ban User", value="ban", emoji=Emojis.banhammer,
                    description="Ban the member if they talk before verifying.",
                ),
            ],
        )
        self.setup_bypass_action.callback = self._on_bypass_action  # type: ignore[assignment]

        self.setup_role: discord.ui.Button = discord.ui.Button(label="Set up Role", style=discord.ButtonStyle.blurple)
        self.setup_role.callback = self._on_setup_role  # type: ignore[assignment]

        self.setup_message: discord.ui.Button = discord.ui.Button(
            label="Send Starter Message", style=discord.ButtonStyle.blurple
        )
        self.setup_message.callback = self._on_setup_message  # type: ignore[assignment]

        self.setup_auto: discord.ui.Button = discord.ui.Button(label="Auto", style=discord.ButtonStyle.blurple)
        self.setup_auto.callback = self._on_setup_auto  # type: ignore[assignment]

        self.toggle_flag: discord.ui.Button = discord.ui.Button(label="Enable", style=discord.ButtonStyle.green)
        self.toggle_flag.callback = self._on_toggle_flag  # type: ignore[assignment]

        self.update_state(invalidate=False)
        self._build_layout()

    def _build_layout(self) -> None:
        container = discord.ui.Container(accent_colour=helpers.Colour.white())
        container.add_item(
            discord.ui.Section(
                f"## Gatekeeper Configuration\n{GATEKEEPER_INFO}",
                accessory=discord.ui.Thumbnail(get_asset_url(self.guild)),
            )
        )
        self.add_item(container)
        self.add_item(discord.ui.ActionRow(self.channel_select))
        self.add_item(discord.ui.ActionRow(self.starter_role_select))
        self.add_item(discord.ui.ActionRow(self.setup_bypass_action))
        self.add_item(discord.ui.ActionRow(self.setup_role, self.setup_message))
        self.add_item(discord.ui.ActionRow(self.setup_auto, self.toggle_flag))

    def update_state(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self.cog.bot.db.signals.fire("gatekeeper_changed", self.gatekeeper.id)

        role = self.gatekeeper.role
        if role is not None:
            label = f'Change Role: "{role.name}"'
            self.setup_role.label = "Change Role" if len(label) > 80 else label
            self.setup_role.style = discord.ButtonStyle.grey
        else:
            self.setup_role.label = "Set up Role"
            self.setup_role.style = discord.ButtonStyle.blurple

        rate = self.gatekeeper.rate
        if rate is not None:
            rate, per = rate
            self.setup_auto.label = f"Auto: {rate}/{per} seconds"
            self.setup_auto.style = discord.ButtonStyle.grey
        else:
            self.setup_auto.label = "Auto"
            self.setup_auto.style = discord.ButtonStyle.blurple

        enabled = self.config.flags.gatekeeper and self.gatekeeper.started_at is not None
        if enabled:
            self.toggle_flag.label = "Disable"
            self.toggle_flag.style = discord.ButtonStyle.red
        else:
            self.toggle_flag.label = "Enable"
            self.toggle_flag.style = discord.ButtonStyle.green

        for option in self.setup_bypass_action.options:
            option.default = option.value == self.gatekeeper.bypass_action

        if self.gatekeeper.starter_role:
            self.starter_role_select.default_values = [discord.SelectDefaultValue.from_role(self.gatekeeper.starter_role)]  # type: ignore[arg-type]

        self.setup_message.disabled = False
        self.channel_select.disabled = False
        self.setup_role.disabled = False
        self.starter_role_select.disabled = False

        channel_id = self.gatekeeper.channel_id
        if channel_id is None:
            self.setup_message.disabled = True

        if self.gatekeeper.message_id is not None:
            self.setup_message.disabled = True

        if self.gatekeeper.started_at is not None:
            self.channel_select.disabled = True
            self.setup_role.disabled = True
            self.starter_role_select.disabled = True
            self.setup_message.disabled = True

        if not enabled:
            self.toggle_flag.disabled = self.gatekeeper.requires_setup

    def stop(self) -> None:
        super().stop()
        self.cog._gatekeeper_menus.pop(self.gatekeeper.id, None)

    async def _on_starter_role(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        role = self.starter_role_select.values[0]
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use this role as it is higher than my role in the hierarchy.", ephemeral=True
            )
            return

        if role >= interaction.user.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use this role as it is higher than your role in the hierarchy.", ephemeral=True
            )
            return

        if role == self.selected_role or role == self.created_role:  # type: ignore[arg-type]
            await interaction.response.send_message(
                f"{Emojis.error} Cannot use the same role for both the starter and the main role.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Gatekeeper Configuration - Starter Role",
            description=f"{Emojis.success} Successfully set the automatically assigned starter role to {role.mention}.",
            colour=helpers.Colour.lime_green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        self.selected_starter_role = role
        if self.selected_starter_role is not None:  # type: ignore[arg-type]
            await self.gatekeeper.edit(starter_role_id=self.selected_starter_role.id)  # type: ignore[arg-type]

        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_bypass_action(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        value: Literal["ban", "kick"] = self.setup_bypass_action.values[0]  # type: ignore[arg-type]
        await self.gatekeeper.edit(bypass_action=value)
        await interaction.followup.send(f"{Emojis.success} Successfully set bypass action to {value}", ephemeral=True)

    async def _on_setup_role(self, interaction: discord.Interaction) -> None:
        if not interaction.app_permissions.manage_roles:
            await interaction.response.send_message(f"{Emojis.error} Bot requires Manage Roles permission for this to work.")
            return

        view = GatekeeperSetupRoleView(self, self.selected_role, self.created_role, self.selected_starter_role)  # type: ignore[arg-type]
        embed = discord.Embed(
            title="Gatekeeper Configuration - Role",
            description="Please either select a pre-existing role or create a new role to automatically assign to new members.",
            colour=helpers.Colour.light_grey(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        await view.wait()
        self.created_role = view.created_role
        self.selected_role = view.selected_role
        if self.selected_role is not None:
            await self.gatekeeper.edit(role_id=self.selected_role.id)

            channel = self.gatekeeper.channel
            if channel is not None:
                await GatekeeperChannelSelect.request_permission_sync(channel, self.selected_role, interaction)

        with suppress(discord.HTTPException):
            if view.message is not None:
                await view.message.delete()

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    async def _on_setup_message(self, interaction: discord.Interaction) -> None:
        channel = self.gatekeeper.channel
        if self.gatekeeper.role is None:
            await interaction.response.send_message(
                f"{Emojis.none} Somehow you managed to press this while no role is set up.", ephemeral=True
            )
            return
        if self.gatekeeper.message is not None:
            await interaction.response.send_message(
                f"{Emojis.none} Somehow you managed to press this while a message is already set up.", ephemeral=True
            )
            return
        if channel is None:
            await interaction.response.send_message(
                f"{Emojis.none} Somehow you managed to press this while no channel is set up.", ephemeral=True
            )
            return

        modal = GatekeeperMessageModal(
            "This server requires verification in order to continue participating.\n"
            "**Press the button below to verify your account.**"
        )
        await interaction.response.send_modal(modal)
        await modal.wait()
        embed = discord.Embed(title=modal.header.value, description=modal.message.value, colour=helpers.Colour.lime_green())
        embed.set_footer(
            text="\u26a0\ufe0f This message was set up by the moderators of this server. "
            "This bot will never ask for your personal information, nor is it related to Discord"
        )

        view = View(timeout=None).add_item(GatekeeperVerifyButton(self.config, self.gatekeeper))
        try:
            message = await channel.send(view=view, embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(f"{Emojis.error} The message could not be sent: {e}", ephemeral=True)
        else:
            await self.gatekeeper.edit(message_id=message.id)
            await interaction.followup.send(f"{Emojis.success} Starter message successfully sent", ephemeral=True)

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    @staticmethod
    async def __rate_limit_modal_response(
        existing_rate: tuple[int, int], interaction: discord.Interaction
    ) -> tuple[int, int] | None:
        modal = GatekeeperRateLimitModal()
        rate, per = existing_rate
        modal.rate.default = str(rate)
        modal.per.default = str(per)
        await interaction.response.send_modal(modal)
        await interaction.delete_original_response()
        await modal.wait()
        if modal.final_rate:
            return modal.final_rate
        return None

    async def _on_setup_auto(self, interaction: discord.Interaction) -> None:
        rate = self.gatekeeper.rate
        if rate is not None:
            view = ConfirmationView(
                interaction.user,
                true="Update",
                false="Remove",
                hook=partial(self.__rate_limit_modal_response, rate),
                delete_after=True,
            )
            await interaction.response.send_message(
                f"{Emojis.none} You already have auto gatekeeper set up, what would you like to do?",
                view=view,
                ephemeral=True,
            )
            view.message = await interaction.original_response()
            await view.wait()
            new_rate = None if not view.value else view.hook_value
            await self.gatekeeper.edit(rate=new_rate)
        else:
            modal = GatekeeperRateLimitModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            if modal.final_rate is not None:
                await self.gatekeeper.edit(rate=modal.final_rate)

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]

    async def _on_toggle_flag(self, interaction: discord.Interaction) -> None:
        enabled = self.gatekeeper.started_at is not None
        if enabled:
            newest = await self.cog.bot.db.get_guild_gatekeeper(self.gatekeeper.id)  # type: ignore[arg-type]
            if newest is not None:
                self.gatekeeper = newest

            members = self.gatekeeper.pending_members
            if members:
                confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
                embed = discord.Embed(
                    title="Gatekeeper Configuration - Toggle",
                    description=(
                        f"There {pluralize(members):is|are!} still {pluralize(members):member} either waiting for their role "
                        "or still solving captcha.\n\n"
                        "Are you sure you want to remove the role from all of them? "
                        "**This has potential to be very slow and will be done in the background**"
                    ),
                    colour=helpers.Colour.light_grey(),
                )
                await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
                confirm.message = await interaction.original_response()
                await confirm.wait()
                if not confirm.value:
                    return
            else:
                await interaction.response.defer()

            await self.gatekeeper.disable()
            await interaction.followup.send(f"{Emojis.success} Successfully disabled gatekeeper.")
        else:
            try:
                await self.gatekeeper.enable()
            except asyncpg.IntegrityConstraintViolationError:
                await interaction.response.send_message(
                    f"{Emojis.error} Could not enable gatekeeper due to either a role or channel being unset or the message failing to send"
                )
            except Exception as e:
                await interaction.response.send_message(f"{Emojis.error} Could not enable gatekeeper: {e}")
            else:
                await interaction.response.send_message(f"{Emojis.success} Successfully enabled gatekeeper.")

        self.update_state()

        if interaction.message is not None:
            await interaction.message.edit(view=self)  # type: ignore[arg-type]


class GatekeeperVerifyButton(discord.ui.DynamicItem[discord.ui.Button], template="gatekeeper:verify:captcha"):
    """A dynamic button that is used to verify a user in the gatekeeper."""

    def __init__(self, config: GuildConfig | None, gatekeeper: Gatekeeper | None) -> None:
        super().__init__(
            discord.ui.Button(label="Verify", style=discord.ButtonStyle.blurple, custom_id="gatekeeper:verify:captcha")
        )
        self.config = config
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction[Bot], _, __, /) -> GatekeeperVerifyButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Moderation cog is not loaded")
        cog = cast("Moderation", _cog)

        config = await cog.bot.db.get_guild_config(interaction.guild_id)
        if config is None:
            return cls(None, None)

        gatekeeper = await cog.bot.db.get_guild_gatekeeper(interaction.guild_id)
        return cls(config, gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.config is None or not self.config.flags.gatekeeper:
            await interaction.response.send_message(f"{Emojis.error} Gatekeeper is not enabled.", ephemeral=True)
            return False

        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f"{Emojis.error} Gatekeeper is not enabled.", ephemeral=True)
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        if not self.gatekeeper.is_blocked(interaction.user.id):
            if self.gatekeeper.has_role(interaction.user):
                # Add the user manually to the queue
                # This is used if the member somehow still has the gatekeeper role but is not in the queue
                await self.gatekeeper.block(interaction.user)
                return True

            await interaction.response.send_message(f"{Emojis.error} You are already verified.", ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        assert self.gatekeeper is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.abc.GuildChannel)

        await interaction.response.defer(ephemeral=True)

        captcha = await self.gatekeeper.bot.render.captcha()

        await interaction.channel.set_permissions(
            interaction.user, reason=f"Gaktekeeper User Verification (ID: {interaction.user.id})", send_messages=True
        )

        embed = discord.Embed(
            title="Enter the captcha",
            description="Please enter the captcha to verify yourself.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="You have 90 seconds to enter the captcha.")

        embed.set_image(url="attachment://captcha.png")

        message = await interaction.followup.send(embed=embed, file=captcha.file, ephemeral=True)

        # Wait for message input from user
        try:
            msg = await interaction.client.wait_for(
                "message",
                check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id,
                timeout=90.0,
            )
        except TimeoutError:
            await message.edit(
                content=f"{Emojis.error} You took too long to enter the captcha, please try again.",
                embed=None,
                attachments=[],
            )
            return
        else:
            await msg.delete()
        finally:
            await interaction.channel.set_permissions(
                interaction.user, reason=f"Gaktekeeper User Verification (ID: {interaction.user.id})", send_messages=False
            )

        if msg.content != captcha.text:
            await message.edit(
                content=f"{Emojis.error} The captcha you entered is incorrect, please try again.", embed=None, attachments=[]
            )
            return

        await self.gatekeeper.unblock(interaction.user)
        await interaction.followup.send(f"{Emojis.success} You have successfully verified yourself.", ephemeral=True)


class GatekeeperAlertResolveButton(discord.ui.DynamicItem[discord.ui.Button], template="gatekeeper:alert:resolve"):
    """A dynamic button that is used to resolve the gatekeeper alert.

    This button is only shown if there are pending members in the gatekeeper.
    There should be a message from the `alerts` webhook.
    """

    def __init__(self, gatekeeper: Gatekeeper | None) -> None:
        super().__init__(
            discord.ui.Button(label="Resolve", style=discord.ButtonStyle.blurple, custom_id="gatekeeper:alert:resolve")
        )
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction[Bot], _, __, /) -> GatekeeperAlertResolveButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Moderation cog is not loaded")
        cog = cast("Moderation", _cog)

        gatekeeper = await cog.bot.db.get_guild_gatekeeper(interaction.guild_id)  # type: ignore[arg-type]
        return cls(gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False
        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f"{Emojis.error} Gatekeeper is not enabled anymore.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        assert self.gatekeeper is not None
        members = self.gatekeeper.pending_members
        if members:
            confirm = ConfirmationView(interaction.user, timeout=180.0)
            embed = discord.Embed(
                title="Gatekeeper Configuration - Alert Resolve",
                description=(
                    f"There {pluralize(members):is|are!} still {pluralize(members):member} either waiting for their role "
                    "or still solving captcha.\n\n"
                    "Are you sure you want to remove the role from all of them? "
                    "**This has potential to be very slow and will be done in the background**"
                ),
                colour=helpers.Colour.light_grey(),
            )
            await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                return
        else:
            await interaction.response.defer()

        await self.gatekeeper.disable()
        await interaction.followup.send(f"{Emojis.success} Successfully disabled gatekeeper.", ephemeral=True)
        if interaction.message is not None:
            await interaction.message.edit(view=None)


class GatekeeperAlertMassbanButton(discord.ui.DynamicItem[discord.ui.Button], template="gatekeeper:alert:massban"):
    """A dynamic button that is used to mass ban the detected raiders.

    This button is only shown if there are detected raiders in the gatekeeper.
    There should be a message from the `alerts` webhook.
    """

    def __init__(self, cog: Moderation) -> None:
        super().__init__(
            discord.ui.Button(label="Ban Raiders", style=discord.ButtonStyle.red, custom_id="gatekeeper:alert:massban")
        )
        self.cog: Moderation = cog

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction[Bot], _, __, /) -> GatekeeperAlertMassbanButton | None:
        _cog = interaction.client.get_cog("Moderation")
        if _cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Moderation cog is not loaded")
        return cls(cast("Moderation", _cog))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if not interaction.app_permissions.ban_members:
            await interaction.response.send_message(f"{Emojis.error} I do not have permissions to ban these members.")
            return False

        if not interaction.permissions.ban_members:
            await interaction.response.send_message(f"{Emojis.error} You do not have permissions to ban these members.")
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> None:
        assert interaction.guild_id is not None
        assert interaction.guild is not None
        assert interaction.message is not None

        members = self.cog._spam_check[interaction.guild_id].flagged_users
        if not members:
            await interaction.response.send_message(f"{Emojis.none} No detected raiders found at the moment.")
            return

        now = interaction.created_at
        members = sorted(members.values(), key=lambda m: m.joined_at or now)
        fmt = "\n".join(f"{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}" for m in members)
        content = f"Current Time: {discord.utils.utcnow()}\nTotal members: {len(members)}\n{fmt}"
        file = discord.File(io.BytesIO(content.encode("utf-8")), filename="members.txt")
        confirm = ConfirmationView(interaction.user, timeout=180.0)
        await interaction.response.send_message(
            f"This will ban the following **{pluralize(len(members)):member}**. Are you sure?", view=confirm, file=file
        )
        await confirm.wait()
        if not confirm.value:
            return

        count = 0
        reason = f"{interaction.user} (ID: {interaction.user.id}): Raid detected"
        for member in members:
            try:
                await interaction.guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await interaction.followup.send(f"{Emojis.success} Banned {count}/{len(members)}")
