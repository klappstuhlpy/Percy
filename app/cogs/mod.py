from __future__ import annotations

import asyncio
import datetime
import enum
import io
import logging
import re
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable, MutableMapping, Sequence
from contextlib import suppress
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, Annotated, Any, Literal

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core import Bot, Context, Flags, flag, store_true
from app.core.converter import ActionReason, BannedMember, IgnoreableEntity, IgnoreEntity, MemberID
from app.core.models import AppBadArgument, BadArgument, Cog, PermissionTemplate, command, cooldown, describe, group
from app.core.timer import Timer
from app.core.views import ConfirmationView, View
from app.utils.constants import Coro
from app.database.base import Gatekeeper, GuildConfig
from app.utils import (
    ListedRateLimit,
    RateLimit,
    cache,
    checks,
    fuzzy,
    get_asset_url,
    helpers,
    human_join,
    merge_perms,
    pluralize,
    resolve_entity_id,
    timetools,
)
from app.utils.lock import lock
from app.utils.pagination import LinePaginator, TextSource
from config import Emojis

if TYPE_CHECKING:
    class ModGuildContext(Context):
        cog: Moderation
        guild_config: GuildConfig

MaybeMember = discord.Member | discord.abc.Snowflake

log = logging.getLogger(__name__)


def safe_reason_append(base: str, to_append: str) -> str:
    appended = f'{base} ({to_append})'
    if len(appended) > 512:
        return base
    return appended


AutoModFlags = GuildConfig.AutoModFlags


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
            starter_role: discord.Role | None
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
        cls=discord.ui.RoleSelect, min_values=1, max_values=1, placeholder='Choose the automatically assigned role'
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use this role as it is higher than my role in the hierarchy.', ephemeral=True)

        if role >= interaction.user.top_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use this role as it is higher than your role in the hierarchy.', ephemeral=True)

        if role == self.starter_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use this role as it is the starter role.', ephemeral=True)

        channels = [
            ch for ch in interaction.guild.channels
            if isinstance(ch, discord.abc.Messageable) and not ch.permissions_for(role).read_messages  # type: ignore
        ]

        await interaction.response.defer(ephemeral=True)

        if channels:
            embed = discord.Embed(
                title='Gatekeeper Configuration - Role',
                description=(
                    'In order for this role to work, it requires editing the permissions in every applicable channel.\n'
                    f'Would you like to edit the permissions of potentially {pluralize(len(channels)):channel}?'
                ),
                colour=helpers.Colour.light_grey()
            )
            confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
            confirm.message = await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                embed = discord.Embed(
                    title='Gatekeeper Configuration - Role',
                    description=(
                        f'{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n'
                        '\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n'
                        'Please edit the permissions of applicable channels to block the user from accessing it when possible.'
                    ),
                    colour=helpers.Colour.lime_green()
                )
            else:
                async with interaction.channel.typing():
                    success, failure, skipped = await Moderation.update_role_permissions(
                        role, self.parent.guild, interaction.user, update_read_permissions=True, channels=channels
                    )
                    total = success + failure + skipped
                    embed = discord.Embed(
                        title='Gatekeeper Configuration - Role',
                        description=(
                            f'{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n'
                            f'Attempted to update {total} channel permissions: '
                            f'[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]'
                        ),
                        colour=helpers.Colour.lime_green()
                    )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(
                f'{Emojis.success} Successfully set the automatically assigned role to {role.mention}', ephemeral=True)

        self.selected_role = role
        self.stop()

    @discord.ui.button(label='Create New Role', style=discord.ButtonStyle.blurple)
    async def create_role(self, interaction: discord.Interaction, _) -> None:
        try:
            role = await self.parent.guild.create_role(name='Unverified')
        except discord.HTTPException as e:
            return await interaction.response.send_message(f'{Emojis.error} Could not create role: {e}', ephemeral=True)

        self.created_role = role
        self.selected_role = role
        channels = [ch for ch in self.parent.guild.channels if isinstance(ch, discord.abc.Messageable)]

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title='Gatekeeper Configuration - Role',
            description=(
                'In order for this role to work, it requires editing the permissions in every applicable channel.\n'
                f'Would you like to edit the permissions of potentially {pluralize(len(channels)):channel}?'
            ),
            colour=helpers.Colour.light_grey()
        )
        confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
        confirm.message = await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            embed = discord.Embed(
                title='Gatekeeper Configuration - Role',
                description=(
                    f'{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n'
                    '\u26a0\ufe0f This role might not work properly unless manually edited to have proper permissions.\n'
                    'Please edit the permissions of applicable channels to block the user from accessing it when possible.'
                ),
                colour=helpers.Colour.lime_green()
            )
        else:
            async with interaction.channel.typing():
                success, failure, skipped = await Moderation.update_role_permissions(
                    role, self.parent.guild, interaction.user, update_read_permissions=True, channels=channels
                )
                total = success + failure + skipped
                embed = discord.Embed(
                    title='Gatekeeper Configuration - Role',
                    description=(
                        f'{Emojis.success} Successfully set the automatically assigned role to {role.mention}.\n\n'
                        f'Attempted to update {total} channel permissions: '
                        f'[Success: {success}, Failure: {failure}, Skipped (no permissions): {skipped}]'
                    ),
                    colour=helpers.Colour.lime_green()
                )
        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()


class GatekeeperRateLimitModal(discord.ui.Modal, title='Join Rate Trigger'):
    """A modal that is used to set the join rate trigger for the gatekeeper."""
    rate = discord.ui.TextInput(label='Number of Joins', placeholder='5', min_length=1, max_length=3)
    per = discord.ui.TextInput(label='Number of seconds', placeholder='5', min_length=1, max_length=2)

    def __init__(self) -> None:
        super().__init__(custom_id='gatekeeper-rate-limit-modal')
        self.final_rate: tuple[int, int] | None = None

    async def on_submit(self, interaction: discord.Interaction[Bot], /) -> None:
        try:
            rate = int(self.rate.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{Emojis.error} Invalid number of joins given, must be a number.', ephemeral=True)

        try:
            per = int(self.per.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{Emojis.error} Invalid number of seconds given, must be a number.', ephemeral=True)

        if rate <= 0 or per <= 0:
            return await interaction.response.send_message(
                f'{Emojis.error} Joins and seconds cannot be negative or zero', ephemeral=True)

        self.final_rate = (rate, per)
        await interaction.response.send_message(
            f'{Emojis.success} Successfully set auto trigger join rate to more than {pluralize(rate):member join} in {per} seconds',
            ephemeral=True,
        )


class GatekeeperMessageModal(discord.ui.Modal, title='Starter Message'):
    """A modal that is used to set the starter message for the gatekeeper."""
    header = discord.ui.TextInput(label='Title', style=discord.TextStyle.short,
                                  max_length=256, default='Verification Required')
    message = discord.ui.TextInput(label='Content', style=discord.TextStyle.long, max_length=2000)

    def __init__(self, default: str) -> None:
        super().__init__()
        self.message.default = default

    async def on_submit(self, interaction: discord.Interaction[Bot], /) -> None:
        await interaction.response.defer()
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
        self.bot: Bot = gatekeeper.bot
        self.gatekeeper: Gatekeeper = gatekeeper
        self.selected_channel: discord.TextChannel | None = None

    @staticmethod
    async def request_permission_sync(
            channel: discord.TextChannel, role: discord.Role, interaction: discord.Interaction
    ) -> None:
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
        confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
        confirm.message = await interaction.followup.send(embed=embed, ephemeral=True, view=confirm)
        await confirm.wait()
        if not confirm.value:
            return

        reason = f'Gatekeeper permission sync requested by {interaction.user} (ID: {interaction.user.id})'
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
            await interaction.followup.send(f'{Emojis.error} Could not edit permissions: {e}', ephemeral=True)

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        channel = self.values[0].resolve()
        if channel is None:
            return await interaction.response.send_message(
                f'{Emojis.error} Sorry, somehow this channel did not resolve on my end.', ephemeral=True)

        assert isinstance(channel, discord.TextChannel)
        perms = channel.permissions_for(self.view.guild.me)
        if not perms.send_messages or not perms.embed_links:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot send messages or embeds to this channel, please select another channel or provide those permissions',
                ephemeral=True)

        manage_roles = checks.has_manage_roles_overwrite(self.view.guild.me, channel)
        if not perms.administrator and not manage_roles:
            return await interaction.response.send_message(
                f'{Emojis.error} Since I do not have Administrator permission, I require Manage Permissions permission in that channel.',
                ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        role = self.gatekeeper.role
        if role is not None:
            await self.request_permission_sync(channel, role, interaction)

        message = self.gatekeeper.message
        if message is not None:
            await message.delete()

        await self.gatekeeper.edit(channel_id=channel.id, message_id=None)
        await interaction.followup.send(f'{Emojis.success} Successfully changed channel to {channel.mention}',
                                        ephemeral=True)
        self.view.update_state()
        await interaction.edit_original_response(view=self.view)


class GatekeeperSetUpView(View):
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

        self.channel_select = GatekeeperChannelSelect(gatekeeper)
        self.add_item(self.channel_select)
        self.setup_bypass_action.options = [
            discord.SelectOption(
                label='Kick User',
                value='kick',
                emoji=Emojis.leave,
                description='Kick the member if they talk before verifying.',
            ),
            discord.SelectOption(
                label='Ban User',
                value='ban',
                emoji=Emojis.banhammer,
                description='Ban the member if they talk before verifying.',
            ),
        ]
        self.update_state(invalidate=False)

    def update_state(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self.cog.bot.db.get_guild_gatekeeper.invalidate(self.gatekeeper.id)

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

        if self.gatekeeper.starter_role:
            self.starter_role_select.default_values = [
                discord.SelectDefaultValue.from_role(self.gatekeeper.starter_role)]

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

    @discord.ui.select(
        cls=discord.ui.RoleSelect, min_values=1, max_values=1,
        placeholder='Choose the automatically assigned starter role', row=1
    )
    async def starter_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use this role as it is higher than my role in the hierarchy.', ephemeral=True)

        if role >= interaction.user.top_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use this role as it is higher than your role in the hierarchy.', ephemeral=True)

        if role == self.selected_role or role == self.created_role:
            return await interaction.response.send_message(
                f'{Emojis.error} Cannot use the same role for both the starter and the main role.', ephemeral=True)

        embed = discord.Embed(
            title='Gatekeeper Configuration - Starter Role',
            description=f'{Emojis.success} Successfully set the automatically assigned starter role to {role.mention}.',
            colour=helpers.Colour.lime_green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        self.selected_starter_role = role
        if self.selected_starter_role is not None:
            await self.gatekeeper.edit(starter_role_id=self.selected_starter_role.id)

        self.update_state()
        await interaction.message.edit(view=self)

    @discord.ui.select(placeholder='Select a bypass action...', row=2, min_values=1, max_values=1, options=[])
    async def setup_bypass_action(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.defer(ephemeral=True)
        value: Literal['ban', 'kick'] = select.values[0]  # type: ignore
        await self.gatekeeper.edit(bypass_action=value)
        await interaction.followup.send(f'{Emojis.success} Successfully set bypass action to {value}', ephemeral=True)

    @discord.ui.button(label='Set up Role', style=discord.ButtonStyle.blurple, row=3)
    async def setup_role(self, interaction: discord.Interaction, _) -> None:
        if not interaction.app_permissions.manage_roles:
            return await interaction.response.send_message(
                f'{Emojis.error} Bot requires Manage Roles permission for this to work.')

        view = GatekeeperSetupRoleView(self, self.selected_role, self.created_role, self.selected_starter_role)
        embed = discord.Embed(
            title='Gatekeeper Configuration - Role',
            description='Please either select a pre-existing role or create a new role to automatically assign to new members.',
            colour=helpers.Colour.light_grey()
        )
        view.message = await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await view.wait()
        self.created_role = view.created_role
        self.selected_role = view.selected_role
        if self.selected_role is not None:
            await self.gatekeeper.edit(role_id=self.selected_role.id)

            channel = self.gatekeeper.channel
            if channel is not None:
                await GatekeeperChannelSelect.request_permission_sync(channel, self.selected_role, interaction)

        with suppress(discord.HTTPException):
            await view.message.delete()
        self.update_state()
        await interaction.message.edit(view=self)

    @discord.ui.button(label='Send Starter Message', style=discord.ButtonStyle.blurple, row=3)
    async def setup_message(self, interaction: discord.Interaction, _) -> None:
        channel = self.gatekeeper.channel
        if self.gatekeeper.role is None:
            return await interaction.response.send_message(
                f'{Emojis.none} Somehow you managed to press this while no role is set up.', ephemeral=True)
        if self.gatekeeper.message is not None:
            return await interaction.response.send_message(
                f'{Emojis.none} Somehow you managed to press this while a message is already set up.', ephemeral=True)
        if channel is None:
            return await interaction.response.send_message(
                f'{Emojis.none} Somehow you managed to press this while no channel is set up.', ephemeral=True)

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
                 'This bot will never ask for your personal information, nor is it related to Discord')

        view = View(timeout=None).add_item(GatekeeperVerifyButton(self.config, self.gatekeeper))
        try:
            message = await channel.send(view=view, embed=embed)
        except discord.HTTPException as e:
            await interaction.followup.send(f'{Emojis.error} The message could not be sent: {e}', ephemeral=True)
        else:
            await self.gatekeeper.edit(message_id=message.id)
            await interaction.followup.send(f'{Emojis.success} Starter message successfully sent', ephemeral=True)

        self.update_state()
        await interaction.message.edit(view=self)

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

    @discord.ui.button(label='Auto', style=discord.ButtonStyle.blurple, row=4)
    async def setup_auto(self, interaction: discord.Interaction, _) -> None:
        rate = self.gatekeeper.rate
        if rate is not None:
            view = ConfirmationView(
                interaction.user,
                true='Update',
                false='Remove',
                hook=partial(self.__rate_limit_modal_response, rate),
                delete_after=True
            )
            await interaction.response.send_message(
                f'{Emojis.none} You already have auto gatekeeper set up, what would you like to do?',
                view=view, ephemeral=True
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
        await interaction.message.edit(view=self)

    @discord.ui.button(label='Enable', style=discord.ButtonStyle.green, row=4)
    async def toggle_flag(self, interaction: discord.Interaction, _) -> None:
        enabled = self.gatekeeper.started_at is not None
        if enabled:
            newest = await self.cog.bot.db.get_guild_gatekeeper(self.gatekeeper.id)
            if newest is not None:
                self.gatekeeper = newest

            members = self.gatekeeper.pending_members
            if members:
                confirm = ConfirmationView(interaction.user, timeout=180.0, delete_after=True)
                embed = discord.Embed(
                    title='Gatekeeper Configuration - Toggle',
                    description=(
                        f'There {pluralize(members):is|are!} still {pluralize(members):member} either waiting for their role '
                        'or still solving captcha.\n\n'
                        'Are you sure you want to remove the role from all of them? '
                        '**This has potential to be very slow and will be done in the background**'
                    ),
                    colour=helpers.Colour.light_grey()
                )
                confirm.message = await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
                await confirm.wait()
                if not confirm.value:
                    return
            else:
                await interaction.response.defer()

            await self.gatekeeper.disable()
            await interaction.followup.send(f'{Emojis.success} Successfully disabled gatekeeper.')
        else:
            try:
                await self.gatekeeper.enable()
            except asyncpg.IntegrityConstraintViolationError:
                await interaction.response.send_message(
                    f'{Emojis.error} Could not enable gatekeeper due to either a role or channel being unset or the message failing to send'
                )
            except Exception as e:
                await interaction.response.send_message(f'{Emojis.error} Could not enable gatekeeper: {e}')
            else:
                await interaction.response.send_message(f'{Emojis.success} Successfully enabled gatekeeper.')

        self.update_state()
        await interaction.message.edit(view=self)


class GatekeeperVerifyButton(discord.ui.DynamicItem[discord.ui.Button], template='gatekeeper:verify:captcha'):
    """A dynamic button that is used to verify a user in the gatekeeper."""

    def __init__(self, config: GuildConfig | None, gatekeeper: Gatekeeper | None) -> None:
        super().__init__(
            discord.ui.Button(label='Verify', style=discord.ButtonStyle.blurple, custom_id='gatekeeper:verify:captcha')
        )
        self.config = config
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> GatekeeperVerifyButton | None:
        cog: Moderation | None = interaction.client.get_cog('Moderation')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True)
            raise AppBadArgument(f'{Emojis.error} Moderation cog is not loaded')

        config = await cog.bot.db.get_guild_config(interaction.guild_id)
        if config is None:
            return cls(None, None)

        gatekeeper = await cog.bot.db.get_guild_gatekeeper(interaction.guild_id)
        return cls(config, gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.config is None or not self.config.flags.gatekeeper:
            await interaction.response.send_message(f'{Emojis.error} Gatekeeper is not enabled.', ephemeral=True)
            return False

        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f'{Emojis.error} Gatekeeper is not enabled.', ephemeral=True)
            return False

        if not self.gatekeeper.is_blocked(interaction.user.id):
            if self.gatekeeper.has_role(interaction.user):
                # Add the user manually to the queue
                # This is used if the member somehow still has the gatekeeper role but is not in the queue
                await self.gatekeeper.block(interaction.user)
                return True

            await interaction.response.send_message(f'{Emojis.error} You are already verified.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
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
                'message',
                check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id,
                timeout=90.0
            )
        except TimeoutError:
            return await message.edit(
                content=f'{Emojis.error} You took too long to enter the captcha, please try again.',
                embed=None,
                attachments=[])
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
                content=f'{Emojis.error} The captcha you entered is incorrect, please try again.',
                embed=None,
                attachments=[])

        await self.gatekeeper.unblock(interaction.user)
        await interaction.followup.send(f'{Emojis.success} You have successfully verified yourself.', ephemeral=True)


class GatekeeperAlertResolveButton(discord.ui.DynamicItem[discord.ui.Button], template='gatekeeper:alert:resolve'):
    """A dynamic button that is used to resolve the gatekeeper alert.

    This button is only shown if there are pending members in the gatekeeper.
    There should be a message from the `alerts` webhook.
    """

    def __init__(self, gatekeeper: Gatekeeper | None) -> None:
        super().__init__(
            discord.ui.Button(label='Resolve', style=discord.ButtonStyle.blurple, custom_id='gatekeeper:alert:resolve')
        )
        self.gatekeeper = gatekeeper

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> GatekeeperAlertResolveButton | None:
        cog: Moderation | None = interaction.client.get_cog('Moderation')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Moderation cog is not loaded')

        gatekeeper = await cog.bot.db.get_guild_gatekeeper(interaction.guild_id)
        return cls(gatekeeper)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False
        if self.gatekeeper is None or self.gatekeeper.started_at is None:
            await interaction.response.send_message(f'{Emojis.error} Gatekeeper is not enabled anymore.',
                                                    ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> Any:
        members = self.gatekeeper.pending_members
        if members:
            confirm = ConfirmationView(interaction.user, timeout=180.0)
            embed = discord.Embed(
                title='Gatekeeper Configuration - Alert Resolve',
                description=(
                    f'There {pluralize(members):is|are!} still {pluralize(members):member} either waiting for their role '
                    'or still solving captcha.\n\n'
                    'Are you sure you want to remove the role from all of them? '
                    '**This has potential to be very slow and will be done in the background**'
                ),
                colour=helpers.Colour.light_grey()
            )
            await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)
            await confirm.wait()
            if not confirm.value:
                return
        else:
            await interaction.response.defer()

        await self.gatekeeper.disable()
        await interaction.followup.send(f'{Emojis.success} Successfully disabled gatekeeper.', ephemeral=True)
        await interaction.message.edit(view=None)


class GatekeeperAlertMassbanButton(discord.ui.DynamicItem[discord.ui.Button], template='gatekeeper:alert:massban'):
    """A dynamic button that is used to mass ban the detected raiders.

    This button is only shown if there are detected raiders in the gatekeeper.
    There should be a message from the `alerts` webhook.
    """

    def __init__(self, cog: Moderation) -> None:
        super().__init__(
            discord.ui.Button(
                label='Ban Raiders', style=discord.ButtonStyle.red, custom_id='gatekeeper:alert:massban'
            )
        )
        self.cog: Moderation = cog

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, __, /
    ) -> GatekeeperAlertMassbanButton | None:
        cog: Moderation | None = interaction.client.get_cog('Moderation')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Moderation cog is not loaded')
        return cls(cog)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if not interaction.app_permissions.ban_members:
            await interaction.response.send_message(f'{Emojis.error} I do not have permissions to ban these members.')
            return False

        if not interaction.permissions.ban_members:
            await interaction.response.send_message(f'{Emojis.error} You do not have permissions to ban these members.')
            return False

        return True

    async def callback(self, interaction: discord.Interaction[Bot]) -> None:
        assert interaction.guild_id is not None
        assert interaction.guild is not None
        assert interaction.message is not None

        members = self.cog._spam_check[interaction.guild_id].flagged_users
        if not members:
            return await interaction.response.send_message(f'{Emojis.none} No detected raiders found at the moment.')

        now = interaction.created_at
        members = sorted(members.values(), key=lambda m: m.joined_at or now)
        fmt = "\n".join(f'{m.id}\tJoined: {m.joined_at}\tCreated: {m.created_at}\t{m}' for m in members)
        content = f'Current Time: {discord.utils.utcnow()}\nTotal members: {len(members)}\n{fmt}'
        file = discord.File(io.BytesIO(content.encode('utf-8')), filename='members.txt')
        confirm = ConfirmationView(interaction.user, timeout=180.0)
        await interaction.response.send_message(
            f'This will ban the following **{pluralize(len(members)):member}**. Are you sure?', view=confirm, file=file)
        await confirm.wait()
        if not confirm.value:
            return

        count = 0
        reason = f'{interaction.user} (ID: {interaction.user.id}): Raid detected'
        for member in members:
            try:
                await interaction.guild.ban(member, reason=reason)
            except discord.HTTPException:
                pass
            else:
                count += 1

        await interaction.followup.send(f'{Emojis.success} Banned {count}/{len(members)}')


class PurgeFlags(Flags):
    user: discord.User | None = flag(description='Remove messages from this user', default=None)
    contains: str | None = flag(description='Remove messages that contains this string (case sensitive)', default=None)
    prefix: str | None = flag(description='Remove messages that start with this string (case sensitive)', default=None)
    suffix: str | None = flag(description='Remove messages that end with this string (case sensitive)', default=None)
    after: int | None = flag(description='Search for messages that come after this message ID',
                             default=None)
    before: int | None = flag(description='Search for messages that come before this message ID',
                              default=None)
    delete_pinned: bool = store_true(description='Whether to delete messages that are pinned. Defaults to True.')
    bot: bool = store_true(description='Remove messages from bots (not webhooks!)')
    webhooks: bool = store_true(description='Remove messages from webhooks')
    embeds: bool = store_true(description='Remove messages that have embeds')
    files: bool = store_true(description='Remove messages that have attachments')
    emoji: bool = store_true(description='Remove messages that have custom emoji')
    reactions: bool = store_true(description='Remove messages that have reactions')
    require: Literal['any', 'all'] = flag(
        description='Whether any or all of the flags should be met before deleting messages. Defaults to "all"',
        default='all')


# VIEWS


class PreExistingMuteRoleView(View):
    def __init__(self, member: discord.Member) -> None:
        super().__init__(timeout=120.0, members=member)
        self.merge: bool | None = None

    @discord.ui.button(label='Merge', style=discord.ButtonStyle.blurple)
    async def merge_button(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = True

    @discord.ui.button(label='Replace', style=discord.ButtonStyle.grey)
    async def replace_button(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = False

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def abort_button(self, _, __) -> None:
        self.merge = None
        await self.message.delete()


class FlaggedMember:
    __slots__ = ('id', 'joined_at', 'display_name', 'messages')

    def __init__(self, user: discord.abc.User | discord.Member, joined_at: datetime.datetime) -> None:
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

    def __init__(self) -> None:
        self.by_content = RateLimit(5, 15.0, key=lambda msg: (msg.channel.id, msg.content))
        self.by_user = RateLimit(10, 12.0, key=lambda msg: msg.author.id)
        self.new_user = RateLimit(30, 35.0, key=lambda msg: msg.channel.id)

        self.last_join: datetime.datetime | None = None
        self.last_member: discord.Member | None = None

        self._by_mentions: commands.CooldownMapping | None = None
        self._by_mentions_rate: int | None = None

        self._join_rate: tuple[int, int] | None = None
        self.auto_gatekeeper: ListedRateLimit | None = None
        # Enabled if alerts are on but gatekeeper isn't
        self._default_join_spam = ListedRateLimit(10, 5, key=attrgetter('joined_at'))

        self.last_created: datetime.datetime | None = None

        self.flagged_users: MutableMapping[int, FlaggedMember] = cache.ExpiringCache(seconds=2700.0)
        self.hit_and_run = RateLimit(5, 15, key=lambda msg: msg.channel.id, tagger=lambda msg: msg.author)

    def get_flagged_member(self, user_id: int, /) -> FlaggedMember | None:
        """Get a flagged member."""
        return self.flagged_users.get(user_id)

    def is_flagged(self, user_id: int, /) -> bool:
        """Check if a user is flagged."""
        return user_id in self.flagged_users

    def flag_member(self, member: discord.Member, /) -> None:
        """Flag a member."""
        self.flagged_users[member.id] = FlaggedMember(member, member.joined_at or discord.utils.utcnow())

    def by_mentions(self, config: GuildConfig) -> commands.CooldownMapping | None:
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

    def is_spamming(self, message: discord.Message) -> SpamCheckerResult | None:
        """Check if a message is spamming.

        This will return a :class:`SpamCheckerResult` if the message is spamming, otherwise None.

        Parameters
        ----------
        message: :class:`discord.Message`
            The message to check.
        """
        if message.guild is None:
            return None

        flagged = self.flagged_users.get(message.author.id)
        if flagged is not None:
            flagged.messages += 1
            spammers = self.hit_and_run.is_ratelimited(message)
            if spammers:
                return SpammerSequence(spammers)

            if (
                    flagged.messages <= 10
                    and message.raw_mentions
                    or '@everyone' in message.content
                    or '@here' in message.content
            ):
                return SpamCheckerResult.flagged_mention()

        if self.is_new(message.author) and self.new_user.is_ratelimited(message):
            return SpamCheckerResult.spammer()

        if self.by_user.is_ratelimited(message):
            return SpamCheckerResult.spammer()

        if self.by_content.is_ratelimited(message):
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

    def get_join_type(self, member: discord.Member) -> MemberJoinType | None:
        """Get the join type of member.

        This will return the join type of member, if any.

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
            self.auto_gatekeeper = ListedRateLimit(rate[0], rate[1], key=attrgetter('joined_at'))
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


# noinspection PyProtectedMember
class Moderation(Cog):
    """Utility commands for moderation."""

    emoji = '<:mod_badge:1322337933428260874>'

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

    async def cog_before_invoke(self, ctx: ModGuildContext) -> None:
        ctx.guild_config = await self.bot.db.get_guild_config(ctx.guild.id)

    async def bot_check(self, ctx: ModGuildContext) -> bool:
        if ctx.guild is None:
            return True

        full_bypass = ctx.permissions.manage_guild or await self.bot.is_owner(ctx.author)
        if full_bypass:
            return True

        guild_id = ctx.guild.id
        config = await self.bot.db.get_guild_config(guild_id)
        if config is None or not config.flags.value:
            return True

        checker = self._spam_check[guild_id]
        return not checker.is_flagged(ctx.author.id)

    @tasks.loop(seconds=15.0)
    @lock('Moderation', 'mute_batch', wait=True)
    async def bulk_mute_insert(self) -> None:
        """|coro|

        Bulk insert the mute data into the database.
        """
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
            config = await self.bot.db.get_guild_config(guild_id)

            if config is None:
                continue

            as_set: set[int] = config.muted_members
            for member_id, insertion in data:
                func = as_set.add if insertion else as_set.discard
                func(member_id)

            final_data.append({'guild_id': guild_id, 'result_array': list(as_set)})
            self.bot.db.get_guild_config.invalidate(guild_id)

        await self.bot.db.execute(query, final_data)
        self._mute_data_batch.clear()

    async def check_raid(
            self, config: GuildConfig, guild: discord.Guild, member: discord.Member, message: discord.Message
    ) -> None:
        """|coro|

        Check if a member is raiding the server and ban them if they are.

        Parameters
        ----------
        config: :class:`GuildConfig`
            The guild configuration to check.
        guild: :class:`discord.Guild`
            The guild to check.
        member: :class:`discord.Member`
            The member to check.
        message: :class:`discord.Message`
            The message to check.
        """
        if not config.flags.raid:
            return

        guild_id = guild.id
        checker = self._spam_check[guild_id]
        result = checker.is_spamming(message)
        if result is None:
            return

        members = result.members if isinstance(result, SpammerSequence) else [member]

        for user in members:
            try:
                await guild.ban(user, reason=result.reason)
            except discord.HTTPException:
                log.info('[Moderation] Failed to ban %s (ID: %s) from server %s.', member, member.id, member.guild)
            else:
                log.info('[Moderation] Banned %s (ID: %s) from server %s.', member, member.id, member.guild)

    @staticmethod
    async def mention_spam_ban(
            mention_count: int,
            guild_id: int,
            member: discord.Member,
            multiple: bool = False,
    ) -> AsyncIterator[str]:
        """|coro|

        Ban a member for mention spamming.
        This asynchronusly yields a result message for the performed ban on the a user.

        Parameters
        ----------
        mention_count: :class:`int`
            The number of mentions the member has made.
        guild_id: :class:`int`
            The guild ID to ban the member from.
        member: :class:`discord.Member`
            The member to ban.
        multiple: :class:`bool`
            Whether the member has spammed over multiple messages.

        Yields
        ------
        :class:`str`
            The result message for the performed ban on the a user.
        """
        if multiple:
            reason = f'Spamming mentions over multiple messages ({mention_count} mentions)'
        else:
            reason = f'Spamming mentions ({mention_count} mentions)'

        try:
            await member.ban(reason=reason)
        except:
            log.info('[Mention Spam] Failed to ban member %s (ID: %s) in guild ID %s', member, member.id, guild_id)
        else:
            yield f'{Emojis.info} Banned **{member}** (ID: `{member.id}`) for spamming `{mention_count}` mentions.'
            log.info('[Mention Spam] Member %s (ID: %s) has been banned from guild ID %s', member, member.id, guild_id)

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
                author.id in (self.bot.user.id, self.bot.owner_id)
                or message.guild is None
                or not isinstance(author, discord.Member)
                or author.bot
                or author.guild_permissions.manage_messages
        ):
            return

        if message.is_system():
            return

        config: GuildConfig = await self.bot.db.get_guild_config(message.guild.id)
        if config is None:
            return

        if (
                message.channel.id in config.safe_automod_entity_ids
                or author.id in config.safe_automod_entity_ids
                or any(i in config.safe_automod_entity_ids for i in author._roles)
        ):
            return

        await self.check_raid(config, message.guild, author, message)

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(message.guild.id)
            if (
                    gatekeeper is not None and gatekeeper.is_bypassing(author)
                    and message.channel.id != gatekeeper.channel_id
            ):
                reason = 'Bypassing gatekeeper by messaging early'
                coro = author.ban if gatekeeper.bypass_action == 'ban' else author.kick
                with suppress(discord.HTTPException):
                    await coro(reason=reason)
                return

        if not config.mention_count:
            return

        checker = self._spam_check[message.guild.id]
        if checker.is_mention_spam(message, config):
            responses = self.mention_spam_ban(config.mention_count, message.guild.id, author, multiple=True)
            pages = TextSource(prefix='', suffix='').add_lines([x async for x in responses]).pages
            for page in pages:
                await config.send_alert(page)
            return

        if len(message.mentions) <= 3:
            return

        mention_count = sum(not m.bot and m.id != author.id for m in message.mentions)
        if mention_count < config.mention_count:
            return

        responses = self.mention_spam_ban(mention_count, message.guild.id, author)
        pages = TextSource(prefix='', suffix='').add_lines([x async for x in responses]).pages
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

        config = await self.bot.db.get_guild_config(member.guild.id)
        if config is None:
            return

        if config.is_muted(member):
            return await config.apply_mute(member, 'Member was previously muted.')

        if not config.flags.gatekeeper:
            return

        checker = self._spam_check[member.guild.id]

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(member.guild.id)
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
                                    f'Detected {pluralize(len(spammers)):member} joining in rapid succession. '
                                    'The following actions have been automatically taken:\n'
                                    '- Enabled Gatekeeper to block them from participating.\n'
                                ),
                                colour=helpers.Colour.light_orange()
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
                    f'Detected **{pluralize(len(spammers)):member}** joining in rapid succession. **Please review!**',
                    view=view
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

        config = await self.bot.db.get_guild_config(after.guild.id)
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
        config: GuildConfig = await self.bot.db.get_guild_config(role.guild.id)
        if config is None:
            return

        if role.id == config.poll_ping_role_id:
            await config.update(poll_ping_role_id=None)
            return await config.send_alert('Poll ping role has been deleted, therefore it\'s been automatically reset.')

        if role.id == config.mute_role_id:
            await config.update(mute_role_id=None, muted_members=[])
            return await config.send_alert('Mute role has been deleted, therefore it\'s been automatically reset.')

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(role.guild.id)
            if gatekeeper is not None and gatekeeper.role_id == role.id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        'Gatekeeper **role** has been deleted while it\'s active, '
                        'therefore it\'s been automatically disabled.'
                    )
                return await gatekeeper.edit(started_at=None, role_id=None)

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
        config: GuildConfig = await self.bot.db.get_guild_config(channel.guild.id)
        if config is None:
            return

        me = channel.guild.me._user

        if config.mute_role is not None:
            _, failed, _ = await self.update_role_permissions(
                config.mute_role, channel.guild, me, channels=[channel])
            if failed:
                await config.send_alert(
                    f'Failed to update permissions for the **mute role** on channel creation. [{channel.mention}]'
                )

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(channel.guild.id)
            if gatekeeper is not None and gatekeeper.role_id:
                role = channel.guild.get_role(gatekeeper.role_id)
                if role is not None:
                    _, failed, _ = await self.update_role_permissions(
                        role, channel.guild, me, update_read_permissions=True, channels=[channel])
                    if failed:
                        await config.send_alert(
                            f'Failed to update permissions for the **gatekeeper role** on channel creation. [{channel.mention}]'
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
        config: GuildConfig = await self.bot.db.get_guild_config(channel.guild.id)
        if config is None:
            return

        if config.music_panel_channel_id and config.music_panel_message_id and channel.id == config.music_panel_channel_id:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            return await config.send_alert(
                'Music panel channel has been deleted, therefore it\'s been automatically disabled.')

        if config.poll_channel_id and channel.id == config.poll_channel_id:
            await config.update(poll_channel_id=None)
            return await config.send_alert(
                'Poll channel has been deleted, therefore it\'s been automatically disabled.')

        if config.poll_reason_channel_id and channel.id == config.poll_reason_channel_id:
            await config.update(poll_reason_channel_id=None)
            return await config.send_alert(
                'Poll reason channel has been deleted, therefore it\'s been automatically disabled.')

        if config.alert_channel_id and channel.id == config.alert_channel_id:
            await config.update(alert_channel_id=None)
            return await config.send_alert(
                'Alert channel has been deleted, therefore it\'s been automatically disabled.')

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(channel.guild.id)
            if gatekeeper is not None and gatekeeper.channel_id == channel.id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        'Gatekeeper **channel** has been deleted while it\'s active, '
                        'therefore it\'s been automatically disabled.'
                    )
                return await gatekeeper.edit(started_at=None, channel_id=None)

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
        config: GuildConfig = await self.bot.db.get_guild_config(payload.guild_id)
        if config is None:
            return

        if config.music_panel_message_id and payload.message_id == config.music_panel_message_id:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            return await config.send_alert(
                'Music panel message has been deleted, therefore it\'s been automatically disabled.')

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(payload.guild_id)
            if gatekeeper is not None and gatekeeper.message_id == payload.message_id:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        'Gatekeeper **starter message** has been deleted while it\'s active, '
                        'therefore it\'s been automatically disabled.'
                    )
                return await gatekeeper.edit(started_at=None, message_id=None)

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
        config: GuildConfig = await self.bot.db.get_guild_config(payload.guild_id)
        if config is None:
            return

        if config.music_panel_message_id and config.music_panel_message_id in payload.message_ids:
            await config.update(music_panel_channel_id=None, music_panel_message_id=None)
            return await config.send_alert(
                'Music panel message has been deleted, therefore it\'s been automatically disabled.')

        if config.flags.gatekeeper:
            gatekeeper = await self.bot.db.get_guild_gatekeeper(payload.guild_id)
            if gatekeeper is not None and gatekeeper.message_id in payload.message_ids:
                if gatekeeper.started_at is not None:
                    await config.send_alert(
                        'Gatekeeper starter message has been deleted while it\'s active, therefore it\'s been automatically disabled.'
                    )
                return await gatekeeper.edit(started_at=None, message_id=None)

    @Cog.listener()
    async def on_voice_state_update(
            self,
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState
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

        config = await self.bot.db.get_guild_config(member.guild.id)
        if config is None:
            return

        if not config.flags.gatekeeper:
            return

        gatekeeper = await self.bot.db.get_guild_gatekeeper(member.guild.id)
        # Joined VC and is bypassing gatekeeper
        if gatekeeper is not None and gatekeeper.is_bypassing(member):
            reason = 'Bypassing gatekeeper by joining a voice channel early'
            coro: Coro = member.ban if gatekeeper.bypass_action == 'ban' else member.kick
            with suppress(discord.HTTPException):
                await coro(reason=reason)

    @command(
        'slowmode',
        aliases=['sm'],
        description='Applies slowmode to this channel.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['manage_channels'],
        user_permissions=['manage_channels']
    )
    @describe(duration='The slowmode duration or 0s to disable')
    async def slowmode(self, ctx: ModGuildContext, *, duration: timetools.ShortTime) -> Any:
        """Applies slowmode to this channel"""
        delta = duration.dt - ctx.message.created_at
        slowmode_delay = int(delta.total_seconds())

        if slowmode_delay > 21600:
            await ctx.send_error('Provided slowmode duration is too long!', ephemeral=True)
        else:
            reason = f'Slowmode changed by {ctx.author} (ID: {ctx.author.id})'
            await ctx.channel.edit(slowmode_delay=slowmode_delay, reason=reason)
            if slowmode_delay > 0:
                fmt = timetools.human_timedelta(duration.dt, source=ctx.message.created_at, accuracy=2)
                await ctx.send_error(f'Configured slowmode to {fmt}', ephemeral=True)
            else:
                await ctx.send_success('Disabled slowmode', ephemeral=True)

    @group(
        'moderation',
        aliases=['mod'],
        fallback='info',
        description='Show the current Bot-Automatic-Moderation behaviour on the server.',
        guild_only=True,
        hybrid=True,
        user_permissions=PermissionTemplate.mod
    )
    async def moderation(self, ctx: ModGuildContext) -> Any:
        """Show current Bot-Automatic-Moderation behavior on the server."""
        if ctx.guild_config is None:
            return await ctx.send_error('This server does not have moderation enabled.')

        embed = discord.Embed(
            title=f'{ctx.guild.name} Moderation Configuration',
            description=(
                'This is the current Bot-Automatic-Moderation configuration for this server.\n'
                'You can use the commands in this category to modify these settings.'
            ),
            timestamp=discord.utils.utcnow(),
            color=helpers.Colour.white())
        embed.set_thumbnail(url=get_asset_url(ctx.guild))

        enabled = 0

        if ctx.guild_config.flags.audit_log:
            channel = f'<#{ctx.guild_config.audit_log_channel_id}>'
            audit_log_broadcast = f'Bound to {channel}'
            enabled += 1
        else:
            audit_log_broadcast = '*Disabled*'

        embed.add_field(name='\N{IDENTIFICATION CARD} Audit Log', value=audit_log_broadcast)

        if ctx.guild_config.flags.alerts:
            alerts = f'Bound to <#{ctx.guild_config.alert_channel_id}>'
            enabled += 1
        else:
            alerts = 'Disabled'

        embed.add_field(name='⚠️ Mod Alerts', value=alerts)

        if ctx.guild_config.flags.raid:
            raid = 'Enabled'
            enabled += 1
        else:
            raid = '*Disabled*'

        embed.add_field(name='\N{SHIELD} Raid Protection', value=raid)

        if ctx.guild_config.mention_count:
            mention_spam = f'Set to **{ctx.guild_config.mention_count}** mentions'
            enabled += 1
        else:
            mention_spam = '*Disabled*'

        embed.add_field(name='\N{PUBLIC ADDRESS LOUDSPEAKER} Mention Spam Protection', value=mention_spam)

        if ctx.guild_config.flags.gatekeeper:
            enabled += 1
            gatekeeper = await self.bot.db.get_guild_gatekeeper(ctx.guild.id)
            if gatekeeper is not None:
                gatekeeper_status = gatekeeper.status
            else:
                gatekeeper_status = 'Partially Disabled (Configuration Setup, but not enabled)'
        else:
            gatekeeper_status = 'Completely Disabled'

        embed.add_field(name='\N{LOCK} Gatekeeper', value=gatekeeper_status, inline=False)

        if ctx.guild_config.safe_automod_entity_ids:
            resolved = [resolve_entity_id(c, guild=ctx.guild) for c in ctx.guild_config.safe_automod_entity_ids]

            if len(ctx.guild_config.safe_automod_entity_ids) <= 5:
                ignored = '\n'.join(resolved)
            else:
                entities = '\n'.join(resolved[:5])
                ignored = f'{entities}\n(*{len(ctx.guild_config.safe_automod_entity_ids) - 5} more...*)'
        else:
            ignored = '*N/A*'

        embed.add_field(name='\N{BUSTS IN SILHOUETTE} Ignored Entities', value=ignored, inline=False)

        embed.set_footer(text=f'Enabled Features: {enabled}/5')
        await ctx.send(embed=embed)

    @moderation.command(
        'alerts',
        description='Toggles alert message logging on the server.',
        guild_only=True,
        bot_permissions=['manage_webhooks'],
        user_permissions=PermissionTemplate.mod
    )
    @describe(
        channel='The channel to send alert messages to. The bot must be able to create webhooks in it.')
    async def moderation_alerts(self, ctx: ModGuildContext, *, channel: discord.TextChannel) -> Any:
        """Toggles alert message logging on the server.

        The bot must have the ability to create webhooks in the given channel.
        """
        await ctx.defer()
        if ctx.guild_config and ctx.guild_config.flags.alerts:
            return await ctx.send_info(
                f'You already have alert message logging enabled. To disable, use "{ctx.prefix}moderation disable alerts"')

        channel_id = channel.id

        reason = f'{ctx.author} enabled alert message logging (ID: {ctx.author.id})'

        try:
            webhook = await channel.create_webhook(
                name='Moderation Alerts', avatar=await self.bot.user.avatar.read(), reason=reason)
        except discord.Forbidden:
            return await ctx.send_error(f'The bot does not have permissions to create webhooks in {channel.mention}.')
        except discord.HTTPException:
            return await ctx.send_error(
                'An error occurred while creating the webhook. Note you can only have 10 webhooks per channel.')

        query = """
            INSERT INTO guild_config (id, flags, alert_channel_id, alert_webhook_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id)
                DO UPDATE SET flags             = guild_config.flags | EXCLUDED.flags,
                              alert_channel_id  = EXCLUDED.alert_channel_id,
                              alert_webhook_url = EXCLUDED.alert_webhook_url;
        """

        flags = AutoModFlags()
        flags.alerts = True
        await ctx.db.execute(query, ctx.guild.id, flags.value, channel_id, webhook.url)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        await ctx.send_success(f'Alert messages enabled. Sending alerts to <#{channel_id}>.')

    @moderation.group(
        'auditlog',
        fallback='set',
        description='Toggles audit text log on the server.',
        bot_permissions=['manage_webhooks'],
        user_permissions=PermissionTemplate.mod,
    )
    @describe(channel='The channel to broadcast audit log messages to.')
    async def moderation_auditlog(self, ctx: ModGuildContext, *, channel: discord.TextChannel) -> Any:
        """Toggles audit text log on the server.
        Audit Log sends a message to the log channel whenever a certain event is triggered.
        """
        await ctx.defer()
        reason = f'{ctx.author} enabled mod audit log (ID: {ctx.author.id})'

        query = "SELECT audit_log_webhook_url FROM guild_config WHERE id = $1;"
        wh_url: str | None = await self.bot.db.fetchval(query, ctx.guild.id)
        if wh_url is not None:
            # Delete the old webhook if it exists
            with suppress(discord.HTTPException):
                webhook = discord.Webhook.from_url(wh_url, session=self.bot.session)
                await webhook.delete(reason=reason)

        try:
            webhook = await channel.create_webhook(
                name='Moderation Audit Log', avatar=await self.bot.user.display_avatar.read(), reason=reason)
        except discord.Forbidden:
            return await ctx.send_error('I do not have permissions to create a webhook in that channel.')
        except discord.HTTPException:
            return await ctx.send_error('Failed to create a webhook in that channel. '
                                        'Note that the limit for webhooks in each channel is **10**.')

        query = """
            INSERT INTO guild_config (id, flags, audit_log_channel_id, audit_log_webhook_url)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id)
                DO UPDATE SET flags                 = guild_config.flags | $2,
                              audit_log_channel_id  = $3,
                              audit_log_webhook_url = $4;
        """

        await ctx.db.execute(query, ctx.guild.id, AutoModFlags.audit_log.flag, channel.id, webhook.url)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        await ctx.send_success(f'Audit log enabled. Broadcasting log events to <#{channel.id}>.')

    @moderation_auditlog.command(
        'alter',
        description='Configures the audit log events.',
        user_permissions=PermissionTemplate.mod,
    )
    @describe(
        flag='The flag you want to set.',
        value='The value you want to set the flag to.'
    )
    async def moderation_auditlog_alter(self, ctx: ModGuildContext, flag: str, value: bool) -> Any:
        """Configures the audit log events.
        You can set the Events you want to get notified about via the Audit Log Channel.
        """
        if ctx.guild_config is None:
            return await ctx.send_error('This server does not have moderation enabled.')

        if not ctx.guild_config.flags.audit_log:
            return await ctx.send_error('Audit log is not enabled on this server.')

        if flag == 'all':
            for key in ctx.guild_config.audit_log_flags:
                ctx.guild_config.audit_log_flags[key] = value
            content = f'Set all Audit Log Events to `{value}`.'
        else:
            if flag in ctx.guild_config.audit_log_flags:
                ctx.guild_config.audit_log_flags[flag] = value
                content = f'Set Audit Log Event **{flag}** to `{value}`.'
            else:
                raise commands.BadArgument(f'Unknown flag **{flag}**')

        query = "UPDATE guild_config SET audit_log_flags = $2 WHERE id = $1;"
        await ctx.db.execute(query, ctx.guild.id, ctx.guild_config.audit_log_flags)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        await ctx.send_success(content)

    @moderation_auditlog_alter.autocomplete('flag')
    async def moderation_auditlog_alter_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT audit_log_flags FROM guild_config WHERE id = $1;"
        flags = list((await self.bot.db.fetchval(query, interaction.guild_id)).items())

        results = fuzzy.finder(current, flags, key=lambda x: x[0])
        return [
            app_commands.Choice(name='All', value='all')
        ] + [app_commands.Choice(name=f'{flg} - {value}', value=flg) for (flg, value) in results]

    @moderation.command(
        'disable',
        description='Disables Moderation on the server.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(protection='The protection to disable')
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
            ctx: ModGuildContext,
            *,
            protection: Literal['all', 'raid', 'mentions', 'auditlog', 'alerts', 'gatekeeper'] = 'all'
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

        query = f"UPDATE guild_config SET {updates} WHERE id=$1 RETURNING audit_log_webhook_url, alert_webhook_url;"

        guild_id = ctx.guild.id
        records = await self.bot.db.fetchrow(query, guild_id)
        self._spam_check.pop(guild_id, None)
        self.bot.db.get_guild_config.invalidate(guild_id)

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
                    warnings.append(f'The webhook `{record[1]}` could not be deleted for some reason.')

        if protection in ('all', 'gatekeeper'):
            gatekeeper = await self.bot.db.get_guild_gatekeeper(guild_id)
            if gatekeeper is not None and gatekeeper.started_at is not None:
                await gatekeeper.disable()
                warnings.append('Gatekeeper was previously running and has been forcibly disabled.')
                members = gatekeeper.pending_members
                if members:
                    warnings.append(
                        f'There {pluralize(members):is|are!} still {pluralize(members):member} waiting in the role queue.'
                        ' **The queue will be paused until gatekeeper is re-enabled**'
                    )

        if warnings:
            warning = f'{Emojis.warning} **Warnings:**\n' + '\n'.join(warnings)
            message = f'{message}\n\n{warning}'

        await ctx.send_success(message)

    @moderation.command(
        'gatekeeper',
        description='Enables and shows the gatekeeper settings menu for the server.',
        guild_only=True,
        bot_permissions=['ban_members'],
        user_permissions=PermissionTemplate.mod
    )
    async def moderation_gatekeeper(self, ctx: ModGuildContext) -> None:
        """Enables and shows the gatekeeper settings menu for the server.

        Gatekeeper automatically assigns a role to members who join to prevent
        them from participating in the server until they verify themselves by
        pressing a button.
        """
        previous = self._gatekeeper_menus.pop(ctx.guild.id, None)
        if previous is not None:
            await previous.on_timeout()
            previous.stop()

        gatekeeper = await self.bot.db.get_guild_gatekeeper(ctx.guild.id)
        async with self.bot.db.acquire(timeout=300.0) as conn, conn.transaction():
            if gatekeeper is None:
                query = "INSERT INTO guild_gatekeeper(id) VALUES ($1) ON CONFLICT DO NOTHING RETURNING *;"
                record = await conn.fetchrow(query, ctx.guild.id)
                gatekeeper = Gatekeeper([], bot=self.bot, record=record)

            query = """
                INSERT INTO guild_config (id, flags)
                VALUES ($1, $2)
                ON CONFLICT (id)
                    DO UPDATE SET flags = guild_config.flags | $2
                RETURNING *;
            """
            record = await conn.fetchrow(query, ctx.guild.id, AutoModFlags.gatekeeper.flag)
            config = GuildConfig(bot=self.bot, record=record)

        self.bot.db.get_guild_config.invalidate(ctx.guild.id)

        embed = discord.Embed(
            title='Gatekeeper Configuration - Information',
            description=(
                'Gatekeeper is a feature that automatically assigns a role to a member when they join, '
                'for the sole purpose of blocking them from accessing the server.\n'
                'The user must press a button in order to verify themselves and have their role removed.\n\n'
                '**In order to set up gatekeeper, a few things are required:**\n'
                '- A channel that locked users will see but regular users will not.\n'
                '- A role that is assigned when users join.\n'
                '- A message that the bot sends in the channel with the verify button.\n\n'
                '**Optional Settings:**\n'
                '- A role that is assigned when users finish the verification. (Starter Role)\n\n'
                '**There are also settings to help configure some aspects of it:**\n'
                '- "Auto" automatically triggers the gatekeeper if N members join in a span of M seconds\n'
                '- "Bypass Action" configures what action is taken when a user talks or joins voice before verifying\n\n'
                'Note that once gatekeeper is enabled, even by auto, it must be manually disabled.\n\n'
                f'{Emojis.info} The Users can verify by solving an image captcha consisting of 6 random letters they need to type into the chat.'
            ),
            colour=helpers.Colour.white()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))

        self._gatekeeper_menus[ctx.guild.id] = view = GatekeeperSetUpView(self, ctx.author, config, gatekeeper)
        view.message = await ctx.send(embed=embed, view=view)

    @moderation.command(
        'raid',
        description='Toggles raid protection on the server.',
        guild_only=True,
        bot_permissions=['ban_members'],
        user_permissions=PermissionTemplate.mod
    )
    @describe(enabled='Whether raid protection should be enabled or not, toggles if not given.')
    async def moderation_raid(self, ctx: ModGuildContext, enabled: bool | None = None) -> None:
        """Toggles raid protection on the server.
        Raid protection automatically bans members that spam messages in your server.
        """
        query = """
            INSERT INTO guild_config (id, flags)
            VALUES ($1, $2)
            ON CONFLICT (id)
                DO UPDATE SET flags = CASE COALESCE($3, NOT (guild_config.flags & $2 = $2))
                                          WHEN TRUE THEN guild_config.flags | $2
                                          WHEN FALSE THEN guild_config.flags & ~$2
                END
            RETURNING COALESCE($3, (flags & $2 = $2));
        """

        enabled = await self.bot.db.fetchval(query, ctx.guild.id, AutoModFlags.raid.flag, enabled)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        fmt = '*enabled*' if enabled else '*disabled*'
        await ctx.send_success(f'Raid protection {fmt}.')

    @moderation.command(
        'mentions',
        description='Enables auto-banning accounts that spam more than \'count\' mentions.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(count='The maximum amount of mentions before banning.')
    async def moderation_mentions(self, ctx: ModGuildContext, count: commands.Range[int, 3]) -> None:
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
            ON CONFLICT (id)
                DO UPDATE SET mention_count = $2;
        """
        await ctx.db.execute(query, ctx.guild.id, count)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        await ctx.send_success(f'Mention spam protection threshold set to `{count}`.')

    @moderation_mentions.error
    async def moderation_mentions_error(self, ctx: ModGuildContext, error: commands.BadArgument) -> None:
        if isinstance(error, commands.RangeError):
            await ctx.send_error('Mention spam protection threshold must be greater than **3**.')

    @moderation.command(
        'ignore',
        description='Specifies what roles, members, or channels ignore Moderation Inspections.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(entities='Space separated list of roles, members, or channels to ignore')
    async def moderation_ignore(
            self,
            ctx: ModGuildContext,
            entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Adds roles, members, or channels to the ignore list for Moderation auto-bans."""
        if len(entities) == 0:
            raise commands.BadArgument('Missing entities to ignore.')

        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
                    ARRAY(SELECT DISTINCT * FROM unnest(COALESCE(safe_automod_entity_ids, '{}') || $2::bigint[]))
            WHERE id = $1;
        """
        ids = [c.id for c in entities]
        await ctx.db.execute(query, ctx.guild.id, ids)
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)

        embed = discord.Embed(title='New Ignored Entities', color=helpers.Colour.white())
        embed.description = '\n'.join(f'- {c.mention}' for c in entities)
        await ctx.send_success('Updated ignore list to ignore:', embed=embed)

    @moderation.command(
        'unignore',
        description='Specifies what roles, members, or channels to take off the ignore list.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(entities='Space separated list of roles, members, or channels to take off the ignore list')
    async def moderation_unignore(
            self,
            ctx: ModGuildContext,
            entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Remove roles, members, or channels from the ignore list for Moderation auto-bans."""
        if len(entities) == 0:
            raise commands.BadArgument('Missing entities to unignore.')

        query = """
            UPDATE guild_config
            SET safe_automod_entity_ids =
                    ARRAY(SELECT element
                          FROM unnest(safe_automod_entity_ids) AS element
                          WHERE NOT (element = ANY ($2::bigint[])))
            WHERE id = $1;
        """

        await ctx.db.execute(query, ctx.guild.id, [c.id for c in entities])
        self.bot.db.get_guild_config.invalidate(ctx.guild.id)
        embed = discord.Embed(title='Removed Ignored Entities', color=helpers.Colour.white())
        embed.description = '\n'.join(f'- {c.mention}' for c in entities)
        await ctx.send_success('Updated ignore list to no longer ignore:', embed=embed)

    @moderation.command(
        'ignored',
        description='Lists what channels, roles, and members are in the moderation ignore list.',
        guild_only=True
    )
    async def moderation_ignored(self, ctx: ModGuildContext) -> Any:
        """List all the channels, roles, and members that are in the Moderation ignore list."""

        if ctx.guild_config is None or not ctx.guild_config.safe_automod_entity_ids:
            return await ctx.send_error('This server does not have any ignored entities.')

        entities = [resolve_entity_id(x, guild=ctx.guild) for x in ctx.guild_config.safe_automod_entity_ids]
        entities = [f'- {e}' for e in entities]
        await LinePaginator.start(ctx, entries=entities, per_page=15, location='description')

    @command(
        'purge',
        description='Removes messages that meet a criteria.',
        aliases=['clear'],
        guild_only=True,
        hybrid=True,
        user_permissions=['manage_messages'],
        bot_permissions=['manage_messages'],
    )
    @describe(search='How many messages to search for')
    async def purge(
            self,
            ctx: ModGuildContext,
            search: commands.Range[int, 1, 2000] | None = None,
            *,
            flags: PurgeFlags
    ) -> Any:
        """Removes messages that meet a criteria.
        This command uses a syntax similar to Discord's search bar.
        The messages are only deleted if all options are met unless
        the `--require` flag is passed to override the behaviour.

        When the command is done doing its work, you will get a message
        detailing which users got removed and how many messages got removed.
        """
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
            EMOJI_REGEX = re.compile(r'<:(\w+):(\d+)>')
            predicates.append(lambda m: EMOJI_REGEX.search(m.content))

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

        if flags.after and search is None:
            search = 2000

        if search is None:
            search = 100

        if require_prompt:
            confirm = await ctx.confirm(
                f'{Emojis.warning} Are you sure you want to delete `{pluralize(search):message}`?',
                ephemeral=True,
                timeout=30)
            if not confirm:
                return

        async with ctx.channel.typing():
            before = discord.Object(id=flags.before) if flags.before else None
            after = discord.Object(id=flags.after) if flags.after else None

            try:
                deleted = await asyncio.wait_for(
                    ctx.channel.purge(limit=search, before=before, after=after, check=predicate), timeout=100
                )
            except discord.Forbidden:
                return await ctx.send_error('I do not have permissions to delete messages.')
            except discord.HTTPException as e:
                return await ctx.send_error(f'Failed to delete messages: {e}')

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

            embed = discord.Embed(title='Channel Purge', description=to_send, colour=helpers.Colour.lime_green())
            await ctx.send(embed=embed, delete_after=15)

    @group(
        'lockdown',
        fallback='start',
        description='Locks down specific channels.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['manage_roles'],
        user_permissions=PermissionTemplate.mod
    )
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(channels='A space-separated list of text or voice channels to lock down')
    async def lockdown(
            self,
            ctx: ModGuildContext,
            channels: commands.Greedy[discord.TextChannel | discord.VoiceChannel]
    ) -> Any:
        """Locks down channels by denying the default role to send messages or connect to voice channels."""
        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                return await ctx.send(embed=self._build_lockdown_error_embed())

            confirm = await ctx.confirm(
                f'{Emojis.warning} This will potentially lock the bot from sending messages.\n'
                'Would you like to resolve the permission issue?')
            if not confirm:
                return

        success, failures = await self.start_lockdown(ctx, channels)
        if failures:
            message = (
                f'Successfully locked down `{len(success)}`/`{len(failures)}` channels.\n'
                f'Failed channels: {", ".join(c.mention for c in failures)}\n\n'
                f'Give the bot Manage Roles permissions in those channels and try again.'
            )
        else:
            message = f'**{pluralize(len(success)):channel}** were successfully locked down.'

        embed = discord.Embed(title='Locked down', description=message, color=discord.Color.green())
        await ctx.send(embed=embed)

    @lockdown.command(
        'for',
        description='Locks down specific channels for a specified amount of time.',
        bot_permissions=['manage_roles'],
        user_permissions=PermissionTemplate.mod
    )
    @checks.requires_timer()
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(
        duration='A duration on how long to lock down for, e.g. 30m.',
        channels='A space-separated list of text or voice channels to lock down.',
    )
    async def lockdown_for(
            self,
            ctx: ModGuildContext,
            duration: timetools.ShortTime,
            channels: commands.Greedy[discord.TextChannel | discord.VoiceChannel]
    ) -> Any:
        """Locks down specific channels for a specified amount of time."""
        if ctx.channel in channels and self.is_potential_lockout(ctx.me, ctx.channel):
            parent = ctx.channel.parent if isinstance(ctx.channel, discord.Thread) else ctx.channel
            if parent is None:
                return await ctx.send(embed=self._build_lockdown_error_embed())

            confirm = await ctx.confirm(
                f'{Emojis.warning} This will potentially lock the bot from sending messages.\n'
                'Would you like to resolve the permission issue?')
            if not confirm:
                return

        success, failures = await self.start_lockdown(ctx, channels)
        timer = await self.bot.timers.create(
            duration.dt,
            'lockdown',
            ctx.guild.id,
            ctx.author.id,
            ctx.channel.id,
            [c.id for c in success],
            created=ctx.message.created_at,
        )

        long = timer.expires >= timer.created + datetime.timedelta(days=1)
        formatted_time = discord.utils.format_dt(timer.expires, 'f' if long else 'T')  # type: ignore

        if failures:
            message = (
                f'Successfully locked down `{len(success)}`/`{len(failures)}` channels until {formatted_time}.\n'
                f'Failed channels: {", ".join(c.mention for c in failures)}\n'
                f'Give the bot Manage Roles permissions in {pluralize(len(failures)):channel|those channels} and try '
                f'the lockdown command on the failed **{pluralize(len(failures)):channel}** again.'
            )
        else:
            message = f'**{pluralize(len(success)):Channel}** were successfully locked down until {formatted_time}.'

        embed = discord.Embed(title='Locked down', description=message, color=helpers.Colour.lime_green())
        await ctx.send(embed=embed)

    @lockdown.command(
        'end',
        description='Ends all lockdowns set.',
        bot_permissions=['manage_roles'],
        user_permissions=PermissionTemplate.mod
    )
    async def lockdown_end(self, ctx: ModGuildContext) -> Any:
        """Ends all set lockdowns.
        To use this command, you must have Manage Roles and Ban Members permissions.
        The bot must also have Manage Members permissions.
        """
        if not await self.is_cooldown_active(ctx.guild, ctx.channel):
            return await ctx.send_error('There is no active lockdown.')

        reason = f'Lockdown ended by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            failures = await self.end_lockdown(ctx.guild, reason=reason)

        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1;"
        await ctx.db.execute(query, ctx.guild.id)
        if failures:
            await ctx.send_info(
                f'Lockdown ended. Failed to edit {human_join([c.mention for c in failures], final='and')}')
        else:
            await ctx.send_success('Lockdown successfully ended.')

    @Cog.listener()
    async def on_lockdown_timer_complete(self, timer: Timer) -> Any:
        await self.bot.wait_until_ready()
        guild_id, mod_id, channel_id, channel_ids = timer.args

        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            return

        member = await self.bot.get_or_fetch_member(guild, mod_id)
        moderator = f'Mod ID {mod_id}' if member is None else f'{member} (ID: {mod_id})'

        reason = f'Automatic lockdown ended from timer made on {timer.created} by {moderator}'
        failures = await self.end_lockdown(guild, channel_ids=channel_ids, reason=reason)

        query = "DELETE FROM guild_lockdowns WHERE guild_id=$1 AND channel_id = ANY($2::bigint[]);"
        await self.bot.db.execute(query, guild_id, channel_ids)

        channel = guild.get_channel_or_thread(channel_id)
        if channel is not None:
            assert isinstance(channel, discord.abc.Messageable)
            if failures:
                formatted = [c.mention for c in failures]
                await channel.send(
                    f'{Emojis.info} Lockdown ended. Failed to edit {human_join(formatted, final='and')}.')
            else:
                valid = [f'<#{c}>' for c in channel_ids]
                await channel.send(
                    f'{Emojis.success} Lockdown successfully ended for {human_join(valid, final='and')}.')

    @staticmethod
    def _build_lockdown_error_embed() -> discord.Embed:
        return discord.Embed(
            title='Failed to perform Lockdown',
            description='For some reason, I could not find an appropriate channel to edit overwrites for.'
                        'Note that this lockdown will potentially lock the bot from sending messages. '
                        'Please explicitly give the bot permissions to **send messages** in threads and channels.',
            color=helpers.Colour.light_red(),
        )

    async def get_lockdown_information(
            self, guild_id: int, channel_ids: list[int] | None = None
    ) -> dict[int, discord.PermissionOverwrite]:
        """Gets the lockdown information for the given guild."""
        rows: list[tuple[int, int, int]]
        if channel_ids is None:
            query = "SELECT channel_id, allow, deny FROM guild_lockdowns WHERE guild_id=$1;"
            rows = await self.bot.db.fetch(query, guild_id)
        else:
            query = """
                SELECT channel_id, allow, deny
                FROM guild_lockdowns
                WHERE guild_id = $1
                  AND channel_id = ANY ($2::bigint[]);
            """
            rows = await self.bot.db.fetch(query, guild_id, channel_ids)

        return {
            channel_id: discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
            for channel_id, allow, deny in rows
        }

    async def start_lockdown(
            self, ctx: ModGuildContext, channels: list[discord.TextChannel | discord.VoiceChannel]
    ) -> tuple[list[discord.TextChannel | discord.VoiceChannel], list[discord.TextChannel | discord.VoiceChannel]]:
        """Starts a lockdown in the given channels."""
        guild_id = ctx.guild.id

        records = []
        success, failures = [], []
        reason = f'Lockdown request by {ctx.author} (ID: {ctx.author.id})'
        async with ctx.typing():
            for channel in channels:
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

                try:
                    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=reason)
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
            ON CONFLICT (guild_id, channel_id) DO NOTHING;
        """
        await self.bot.db.execute(query, records)
        return success, failures

    async def end_lockdown(
            self,
            guild: discord.Guild,
            *,
            channel_ids: list[int] | None = None,
            reason: str | None = None,
    ) -> list[discord.abc.GuildChannel]:
        """Ends a lockdown in the given guild."""
        channel_fallback: dict[int, discord.abc.GuildChannel] | None = None
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
        """Checks if the given channel is currently in a lockdown."""
        query = "SELECT * FROM guild_lockdowns WHERE guild_id=$1 AND channel_id=$2;"
        record = await self.bot.db.fetchrow(query, guild.id, channel.id)
        if record:
            return True
        return False

    @staticmethod
    def is_potential_lockout(
            me: discord.Member, channel: discord.Thread | discord.VoiceChannel | discord.TextChannel
    ) -> bool:
        """Checks if the bot is potentially locked out from sending messages in the given channel."""
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

    @command(
        'kick',
        description='Kicks a member from the server.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['kick_members'],
        user_permissions=['kick_members']
    )
    @describe(
        member='The member to ban. You can also pass in an ID to ban.',
        reason='The reason for banning the member.'
    )
    async def kick(
            self,
            ctx: ModGuildContext,
            member: Annotated[MaybeMember, MemberID],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ):
        """Kicks a member from the server."""
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if ctx.author.id == member.id:
            return await ctx.send_error('You cannot kick yourself.')

        if member.id == ctx.guild.owner_id:
            return await ctx.send_error('You cannot kick the server owner.')

        if isinstance(member, discord.Member):
            if ctx.author.top_role < member.top_role:
                return await ctx.send_error('You cannot kick a member with a role equal to or higher than yours.')

            if ctx.me.top_role < member.top_role:
                return await ctx.send_error('I cannot kick a member with a role equal to or higher than mine.')

        await ctx.guild.kick(member, reason=reason)
        await ctx.send_success(f'Kicked {member}.')

    @command(
        'ban',
        description='Bans a member from the server.',
        guild_only=True,
        bot_permissions=['ban_members'],
        user_permissions=['ban_members']
    )
    @describe(
        member='The member to ban. You can also pass in an ID to ban regardless of whether they\'re in the server or not.',
        reason='The reason for banning the member.'
    )
    async def ban(
            self,
            ctx: ModGuildContext,
            member: Annotated[MaybeMember, MemberID],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
        """Bans a member from the server.
        You can also ban from ID to ban regardless of whether they're
        in the server or not.
        """
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if ctx.author.id == member.id:
            return await ctx.send_error('You cannot ban yourself.')

        if member.id == ctx.guild.owner_id:
            return await ctx.send_error('You cannot ban the server owner.')

        if isinstance(member, discord.Member):
            if ctx.author.top_role < member.top_role:
                return await ctx.send_error('You cannot ban a member with a role equal to or higher than yours.')

            if ctx.me.top_role < member.top_role:
                return await ctx.send_error('I cannot ban a member with a role equal to or higher than mine.')

        await ctx.guild.ban(member, reason=reason)
        await ctx.send_success(f'Successfully banned `{member}`.')

    @command(
        'multiban',
        description='Bans multiple members by ID from the server.',
        guild_only=True,
        bot_permissions=['ban_members'],
        user_permissions=['ban_members', 'kick_members']
    )
    @describe(
        members='The members to ban. You can also pass in IDs to ban regardless of whether they\'re in the server or not.',
        reason='The reason for banning the members.'
    )
    async def multiban(
            self,
            ctx: ModGuildContext,
            members: Annotated[list[MaybeMember], commands.Greedy[MemberID]],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
        """Bans multiple members from the server.
        This only works through banning via ID.
        """
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        total_members = len(members)
        if total_members == 0:
            raise commands.BadArgument('No members were passed to ban.')

        confirm = await ctx.confirm(
            f'{Emojis.warning} This will ban **{pluralize(total_members):member}**. Are you sure?')
        if not confirm:
            return

        failed = 0
        for member in members:
            try:
                await ctx.guild.ban(member, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.send_success(f'Successfully banned [`{total_members - failed}`/`{total_members}`] members.')

    @command(
        'softban',
        description='Soft bans a member from the server.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['ban_members'],
        user_permissions=['kick_members']
    )
    @app_commands.describe(
        member='The member to softban.',
        reason='The reason for softbanning the member.')
    async def softban(
            self,
            ctx: ModGuildContext,
            member: Annotated[MaybeMember, MemberID],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
        """Soft bans a member from the server.

        A softban is basically banning the member from the server but
        then unbanning the member as well. This allows you to essentially
        kick the member while removing their messages.
        """
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if ctx.author.id == member.id:
            return await ctx.send_error('You cannot soft-ban yourself.')

        if member.id == ctx.guild.owner_id:
            return await ctx.send_error('You cannot soft-ban the server owner.')

        if isinstance(member, discord.Member):
            if ctx.author.top_role < member.top_role:
                return await ctx.send_error('You cannot soft-ban a member with a role equal to or higher than yours.')

            if ctx.me.top_role < member.top_role:
                return await ctx.send_error('I cannot soft-ban a member with a role equal to or higher than mine.')

        await ctx.guild.ban(member, reason=reason)
        await ctx.guild.unban(member, reason=reason)
        await ctx.send_success(f'Successfully soft-banned **{member}**.')

    @command(
        'unban',
        description='Unbans a member from the server.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['ban_members'],
        user_permissions=['ban_members']
    )
    @describe(
        member='The member to unban.',
        reason='The reason for unbanning the member.')
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
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        await ctx.guild.unban(member.user, reason=reason)
        if member.reason:
            await ctx.send_success(
                f'Unbanned {member.user} (ID: `{member.user.id}`); Previously banned for **{member.reason}**.')
        else:
            await ctx.send_success(f'Unbanned {member.user} (ID: `{member.user.id}`).')

    @command(
        'tempban',
        description='Temporarily bans a member for the specified duration.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['ban_members'],
        user_permissions=['ban_members']
    )
    @checks.requires_timer()
    @describe(
        duration='The duration to ban the member for. Must be a future Time.',
        member='The member to ban.',
        reason='The reason for banning the member.')
    async def tempban(
            self,
            ctx: ModGuildContext,
            duration: timetools.FutureTime,
            member: Annotated[MaybeMember, MemberID],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
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
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if ctx.author.id == member.id:
            return await ctx.send_error('You cannot ban yourself.')

        if member.id == ctx.guild.owner_id:
            return await ctx.send_error('You cannot ban the server owner.')

        if isinstance(member, discord.Member):
            if ctx.author.top_role < member.top_role:
                return await ctx.send_error('You cannot ban a member with a role equal to or higher than yours.')

            if ctx.me.top_role < member.top_role:
                return await ctx.send_error('I cannot ban a member with a role equal to or higher than mine.')

        until = f'until {discord.utils.format_dt(duration.dt, 'F')}'

        try:
            await member.send(f'{Emojis.info} You have been banned from {ctx.guild.name} {until}. Reason: {reason}')
        except (AttributeError, discord.HTTPException):
            pass

        reason = safe_reason_append(reason, until)
        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await ctx.guild.ban(member, reason=reason)
        await self.bot.timers.create(
            duration.dt,
            'tempban',
            ctx.guild.id,
            ctx.author.id,
            member.id,
            created=ctx.message.created_at,
            timezone=zone or 'UTC',
        )
        await ctx.send_success(f'Temporarily banned **{member}** until {discord.utils.format_dt(duration.dt, 'R')}.')

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
            except:
                moderator = f'Mod ID {mod_id}'
            else:
                moderator = f'{moderator} (ID: {mod_id})'
        else:
            moderator = f'{moderator} (ID: {mod_id})'

        reason = f'Automatic unban from timer made on {timer.created} by {moderator}.'
        await guild.unban(discord.Object(id=member_id), reason=reason)

    # MUTE

    @group(
        'mute',
        description='Mutes members using the configured mute role.',
        hybrid=True,
        iwc=True,
        guild_only=True,
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles']
    )
    @checks.can_mute()
    @describe(members='The members to mute.')
    async def _mute(
            self,
            ctx: ModGuildContext,
            members: commands.Greedy[discord.Member],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
        """Mutes members using the configured mute role.
        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.
        To use this command, you need to be higher than the
        mute role in the hierarchy.
        """
        if (total := len(members)) == 0:
            raise BadArgument('Missing members to mute.', 'members')

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        role = discord.Object(id=ctx.guild_config.mute_role_id)

        if ctx.me.top_role < role:
            return await ctx.send_error('I cannot mute a member with a role equal to or higher than the mute role.')

        failed = 0
        for member in members:
            try:
                await member.add_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.send_success(f'Muted [`{abs(total - failed)}`/`{total}`] members.')

    @command(
        'unmute',
        description='Unmutes members using the configured mute role.',
        guild_only=True,
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles']
    )
    @checks.can_mute()
    @describe(members='The members to unmute.', reason='The reason for unmuting the members.')
    async def _unmute(
            self,
            ctx: ModGuildContext,
            members: commands.Greedy[discord.Member],
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
        """Unmutes members using the configured mute role.
        The bot must have Manage Roles permission and be
        above the muted role in the hierarchy.
        To use this command, you need to be higher than the
        mute role in the hierarchy.
        """
        if (total := len(members)) == 0:
            raise BadArgument('Missing members to unmute.', 'members')

        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        role = discord.Object(id=ctx.guild_config.mute_role_id)

        if ctx.me.top_role < role:
            return await ctx.send_error('I cannot mute a member with a role equal to or higher than the mute role.')

        failed = 0
        for member in members:
            try:
                await member.remove_roles(role, reason=reason)
            except discord.HTTPException:
                failed += 1

        await ctx.send_success(f'Unmuted [`{total - failed}`/`{total}`] members.')

    @command(
        'tempmute',
        description='Temporarily mutes a member for the specified duration.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles']
    )
    @checks.requires_timer()
    @checks.can_mute()
    @describe(
        duration='The duration to mute the member for. Must be a future Time.',
        member='The member to mute.',
        reason='The reason for muting the member.')
    async def tempmute(
            self,
            ctx: ModGuildContext,
            duration: timetools.FutureTime,
            member: discord.Member,
            *,
            reason: Annotated[str | None, ActionReason] = None,
    ) -> Any:
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
        if reason is None:
            reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if ctx.author.id == member.id:
            return await ctx.send_error('You cannot mute yourself.')

        if ctx.author.top_role < member.top_role:
            return await ctx.send_error('You cannot mute a member with a role equal to or higher than yours.')

        if ctx.me.top_role < member.top_role:
            return await ctx.send_error('I cannot mute a member with a role equal to or higher than mine.')

        role_id = ctx.guild_config.mute_role_id

        if ctx.me.top_role < discord.Object(id=role_id):
            return await ctx.send_error('I cannot mute a member with a role equal to or higher than the mute role.')

        await member.add_roles(discord.Object(id=role_id), reason=reason)

        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await self.bot.timers.create(
            duration.dt,
            'tempmute',
            ctx.guild.id,
            ctx.author.id,
            member.id,
            role_id,
            created=ctx.message.created_at,
            timezone=zone or 'UTC',
        )
        await ctx.send_success(f'Temporarily muted {member} until {discord.utils.format_dt(duration.dt, 'F')}.')

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
                except:
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
            self._mute_data_batch[guild_id].append((member_id, False))

    @_mute.group(
        'role',
        description='Shows configuration of the mute role.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles', 'manage_channels']
    )
    async def _mute_role(self, ctx: ModGuildContext) -> None:
        """Shows configuration of the mute role."""
        role = ctx.guild_config and ctx.guild_config.mute_role
        total = 0
        if role is not None:
            members = ctx.guild_config.muted_members.copy()
            members.update((r.id for r in role.members))
            total = len(members)
            role = f'{role} (ID: {role.id})'

        await ctx.send_success(f'Role: {role}\nMembers Muted: {total}')

    @_mute_role.command(
        'set',
        description='Sets the mute role to a pre-existing role.',
        guild_only=True,
        bot_permissions=['manage_roles'],
        user_permissions=['manage_roles', 'manage_channels']
    )
    @cooldown(1, 30.0, commands.BucketType.guild)
    @describe(role='The role to set as the mute role.')
    async def mute_role_set(self, ctx: ModGuildContext, *, role: discord.Role) -> None:
        """Sets the mute role to a pre-existing role."""
        if role.is_default():
            raise commands.BadArgument('You cannot set the default role as the mute role.')

        if role > ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            raise commands.BadArgument('You cannot set a role higher than your top role as the mute role.')

        if role > ctx.me.top_role:
            raise commands.BadArgument('I cannot set a role higher than my top role as the mute role.')

        has_pre_existing = ctx.guild_config is not None and ctx.guild_config.mute_role is not None
        merge: bool = False

        if has_pre_existing:
            view = PreExistingMuteRoleView(ctx.author)
            view.message = await ctx.send_warning(
                '**There seems to be a pre-existing mute role set up.**\n\n'
                'If you want to merge the pre-existing member data with the new member data press the Merge button.\n'
                'If you want to replace pre-existing member data with the new member data press the Replace button.\n\n'
                '**Note: Merging is __slow__. It will also add the role to every possible member that needs it.**',
                view=view
            )
            await view.wait()
            if view.merge is None:
                return
            merge = view.merge
        else:
            muted_members = len(role.members)
            if muted_members > 0:
                msg = f'{Emojis.warning} Are you sure you want to make this the mute role? It has {pluralize(muted_members):member}.'
                confirm = await ctx.confirm(msg)
                if not confirm:
                    return

        async with ctx.typing():
            members = set()

            if ctx.guild_config and merge:
                members |= ctx.guild_config.muted_members
                reason = f'Action done by {ctx.author} (ID: {ctx.author.id}): Merging mute roles'
                async for member in self.bot.resolve_member_ids(ctx.guild, members):
                    if not member._roles.has(role.id):
                        try:
                            await member.add_roles(role, reason=reason)
                        except discord.HTTPException:
                            pass

            members.update((m.id for m in role.members))
            query = """
                INSERT INTO guild_config (id, mute_role_id, muted_members)
                VALUES ($1, $2, $3::bigint[])
                ON CONFLICT (id)
                    DO UPDATE SET mute_role_id  = EXCLUDED.mute_role_id,
                                  muted_members = EXCLUDED.muted_members;
            """
            await self.bot.db.execute(query, ctx.guild.id, role.id, list(members))
            self.bot.db.get_guild_config.invalidate(ctx.guild.id)

            escaped = discord.utils.escape_mentions(role.name)
            await ctx.send_success(f'Successfully set the {escaped} role as the mute role.\n\n'
                                   '**Note: Permission overwrites have not been changed.**')

    @_mute_role.command(
        'update',
        description='Updates the permission overwrites of the mute role.',
        aliases=['sync'],
        guild_only=True,
        bot_permissions=['manage_roles', 'manage_channels'],
        user_permissions=['manage_roles']
    )
    @checks.can_mute()
    async def mute_role_update(self, ctx: ModGuildContext) -> None:
        """Automatically updates the permission overwrites of the mute role on the server."""
        async with ctx.typing():
            success, failure, skipped = await self.update_role_permissions(
                role, ctx.guild, ctx.author._user  # noqa
            )
            total = success + failure + skipped
            await ctx.send_info(f'Attempted to update {total} channel permissions. '
                                f'[Updated: `{success}`, Failed: `{failure}`, Skipped (*no permissions*): `{skipped}`]')

    @_mute_role.command(
        'create',
        description='Creates a mute role with the given name.',
        guild_only=True,
        bot_permissions=['manage_roles', 'manage_channels'],
        user_permissions=['manage_roles']
    )
    @describe(name='The name of the mute role to create.')
    async def mute_role_create(self, ctx: ModGuildContext, *, name: str) -> Any:
        """Creates a mute role with the given name.
        This also updates the channels' permission overwrites accordingly if needed.
        """
        guild_id = ctx.guild.id
        if ctx.guild_config is not None and ctx.guild_config.mute_role is not None:
            return await ctx.send_error('A mute role has already been set up.')

        try:
            role = await ctx.guild.create_role(
                name=name, reason=f'Mute Role Created By {ctx.author} (ID: {ctx.author.id})')
        except discord.HTTPException as e:
            return await ctx.send_error(f'Failed to create role: {e}')

        query = """
            INSERT INTO guild_config (id, mute_role_id)
            VALUES ($1, $2)
            ON CONFLICT (id)
                DO UPDATE SET mute_role_id = EXCLUDED.mute_role_id;
        """
        await ctx.db.execute(query, guild_id, role.id)
        self.bot.db.get_guild_config.invalidate(guild_id)

        confirm = await ctx.confirm(f'{Emojis.warning} Would you like to update the channel overwrites as well?')
        if not confirm:
            return await ctx.send_success('Mute role successfully created.')

        async with ctx.typing():
            success, failure, skipped = await self.update_role_permissions(
                role, ctx.guild, ctx.author._user)
            await ctx.send_success(f'Mute role successfully created. Overwrites: '
                                   f'[Updated: {success}, Failed: {failure}, Skipped: {skipped}]')

    @_mute_role.command(
        'unbind',
        aliases=['delete'],
        description='Unbinds a mute role without deleting it.',
        guild_only=True,
        user_permissions=['manage_roles']
    )
    async def mute_role_unbind(self, ctx: ModGuildContext) -> None:
        """Unbinds a mute role without deleting it."""
        guild_id = ctx.guild.id
        if ctx.guild_config is None or ctx.guild_config.mute_role is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        muted_members = len(ctx.guild_config.muted_members)
        if muted_members > 0:
            msg = f'Are you sure you want to unbind and unmute {pluralize(muted_members):member}?'
            confirm = await ctx.confirm(msg)
            if not confirm:
                return

        query = "UPDATE guild_config SET (mute_role_id, muted_members) = (NULL, '{}'::bigint[]) WHERE id=$1;"
        await self.bot.db.execute(query, guild_id)
        self.bot.db.get_guild_config.invalidate(guild_id)
        await ctx.send_success('Successfully unbound mute role.')

    @command(
        'selfmute',
        description='Temporarily mutes yourself for the specified duration.',
        guild_only=True,
        hybrid=True,
        bot_permissions=['manage_roles']
    )
    @checks.requires_timer()
    @describe(duration='The duration to mute yourself for. Must be in a short time form e.g., 4h.')
    async def selfmute(self, ctx: ModGuildContext, *, duration: timetools.ShortTime) -> Any:
        """Temporarily mutes yourself for the specified duration.
        The duration must be in a short time form e.g., 4h. Can
        only mute yourself for a maximum of 24 hours and a minimum
        of 5 minutes.

        **Don't ask a moderator to unmute you.**
        """
        role_id = ctx.guild_config and ctx.guild_config.mute_role_id
        if role_id is None:
            raise commands.BadArgument('This server does not have a mute role set up.')

        if ctx.author._roles.has(role_id):
            return await ctx.send_error('You are already muted.')

        if ctx.me.top_role < discord.Object(id=role_id):
            return await ctx.send_error('I cannot mute you with a role equal to or higher than the mute role.')

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

    @staticmethod
    async def update_role_permissions(
            role: discord.Role,
            guild: discord.Guild,
            invoker: discord.abc.User,
            update_read_permissions: bool = False,
            channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None = None,
            **permissions: bool | None
    ) -> tuple[int, int, int]:
        r"""|coro|

        Updates the permission overwrites of a specified role on the server.

        Notes
        -----
        This method should only be used to restrict permissions for the role in the channels.

        Parameters
        ----------
        role: discord.Role
            The role to update the permission overwrites for.
        guild: discord.Guild
            The guild to update the permission overwrites in.
        invoker: discord.abc.User
            The user who invoked the action.
        update_read_permissions: bool
            Whether to update the read permissions as well.
        channels: Sequence[discord.abc.GuildChannel] | list[discord.abc.Messageable] | None
            The channels to update the permission overwrites in.
        \*\*permissions: bool | None
            The permissions to update the permission overwrites with.
            Those are extras.

        Returns
        -------
        tuple[int, int, int]
            A tuple containing the number of successful, failed, and skipped updates.
        """
        success, failure, skipped = 0, 0, 0
        reason = f'Action done by {invoker} (ID: {invoker.id})'
        channels: list[discord.abc.GuildChannel | discord.abc.Messageable] | None
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
                    'connect': False,
                    'speak': False,
                }
                if update_read_permissions:
                    perms['read_messages'] = False

                if permissions:
                    merge_perms(overwrite, guild_perms, **permissions)

                merge_perms(overwrite, guild_perms, **perms)
                try:
                    await channel.set_permissions(role, overwrite=overwrite, reason=reason)
                except discord.HTTPException:
                    failure += 1
                else:
                    success += 1
            else:
                skipped += 1
        return success, failure, skipped


async def setup(bot: Bot) -> None:
    await bot.add_cog(Moderation(bot))
