from __future__ import annotations

import time
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.core.views import ConfirmationView, LayoutView
from app.utils import get_asset_url, pluralize
from app.utils.helpers import Colour
from config import Emojis

from .infractions import update_role_permissions

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.database.base import GuildConfig

    from .cog import Moderation


class PreExistingMuteRoleView(LayoutView):
    def __init__(self, member: discord.Member, *, content: str | None = None) -> None:
        super().__init__(timeout=120.0, members=member, delete_on_timeout=True)
        self.merge: bool | None = None

        self._merge_btn = discord.ui.Button(label="Merge", style=discord.ButtonStyle.blurple)
        self._merge_btn.callback = self._on_merge  # type: ignore[assignment]

        self._replace_btn = discord.ui.Button(label="Replace", style=discord.ButtonStyle.grey)
        self._replace_btn.callback = self._on_replace  # type: ignore[assignment]

        self._cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)
        self._cancel_btn.callback = self._on_cancel  # type: ignore[assignment]

        container = discord.ui.Container(accent_colour=Colour.warning_accent())
        if content:
            container.add_item(discord.ui.TextDisplay(content))
            container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(self._merge_btn, self._replace_btn, self._cancel_btn))
        self.add_item(container)

    async def _on_merge(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = True
        self.stop()

    async def _on_replace(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.merge = False
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        del interaction
        self.merge = None
        if self.message is not None:
            await self.message.delete()
        self.stop()


class MuteRoleCreateModal(discord.ui.Modal, title="Create Mute Role"):
    """Collects the name for a freshly created mute role."""

    name = discord.ui.TextInput(
        label="Role Name",
        placeholder="Muted",
        default="Muted",
        max_length=100,
    )

    def __init__(self) -> None:
        super().__init__()
        self.value: str | None = None

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        self.value = self.name.value
        await interaction.response.defer(ephemeral=True)
        self.stop()


class MuteRoleSetUpView(LayoutView):
    """The mute role setup dashboard — a single CV2 container.

    Mirrors the gatekeeper menu: every control (role select, buttons, status
    text) lives inside one Container so it renders as a continuous card. The
    layout is rebuilt on every state change. A single ``?muterole`` command
    surfaces all of bind / create / sync / unbind from one place.
    """

    def __init__(self, cog: Moderation, member: discord.Member, config: GuildConfig) -> None:
        super().__init__(timeout=900.0, members=member, delete_on_timeout=True)
        self.cog = cog
        self.config = config

        guild = cog.bot.get_guild(config.id)
        assert guild is not None
        self.guild: discord.Guild = guild

        # -- interactive components (stable instances, mutated by update_state)
        self.role_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            min_values=1, max_values=1, placeholder="Bind an existing role..."
        )
        self.role_select.callback = self._on_role_select  # type: ignore[assignment]

        self.create_btn: discord.ui.Button = discord.ui.Button(
            label="Create New Role", style=discord.ButtonStyle.green
        )
        self.create_btn.callback = self._on_create  # type: ignore[assignment]

        self.sync_btn: discord.ui.Button = discord.ui.Button(
            label="Sync Permissions", style=discord.ButtonStyle.blurple
        )
        self.sync_btn.callback = self._on_sync  # type: ignore[assignment]

        self.unbind_btn: discord.ui.Button = discord.ui.Button(
            label="Unbind Role", style=discord.ButtonStyle.red
        )
        self.unbind_btn.callback = self._on_unbind  # type: ignore[assignment]

        self.update_state()

    # -- rendering --------------------------------------------------------

    def _muted_total(self, role: discord.Role | None) -> int:
        if role is None:
            return 0
        members = set(self.config.muted_members)
        members.update(r.id for r in role.members)
        return len(members)

    def _rebuild_layout(self) -> None:
        self.clear_items()
        role = self.config.mute_role
        total = self._muted_total(role)

        container = discord.ui.Container(accent_colour=Colour.brand())

        # --- Header ---
        container.add_item(
            discord.ui.Section(
                "## Mute Role\n-# Restrict members from speaking across the server",
                accessory=discord.ui.Thumbnail(get_asset_url(self.guild)),
            )
        )
        container.add_item(discord.ui.Separator())
        if role is not None:
            status = f"**CONFIGURED** — {pluralize(total):member} currently muted"
        else:
            status = "**NOT SET** — `mute`/`unmute` are unavailable until a role is bound"
        role_display = role.mention if role else "`not set`"
        container.add_item(discord.ui.TextDisplay(f"-# Status: {status}\n-# Role: {role_display}"))

        # --- Bind Existing Role ---
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "### Bind Existing Role\n"
            "Pick a role to use as the mute role. Members holding it are treated as muted.\n"
            "-# Overwrites are not changed automatically — use **Sync Permissions** afterwards."
        ))
        container.add_item(discord.ui.ActionRow(self.role_select))

        # --- Create New Role ---
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "### Create New Role\n"
            "Spin up a fresh role and optionally apply restrictive overwrites to every channel."
        ))
        container.add_item(discord.ui.ActionRow(self.create_btn))

        # --- Maintenance ---
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            "### Maintenance\n"
            "**Sync Permissions** — reapply the mute overwrites across all channels.\n"
            "**Unbind Role** — detach the role without deleting it and clear muted members."
        ))
        container.add_item(discord.ui.ActionRow(self.sync_btn, self.unbind_btn))

        self.add_item(container)

    def update_state(self) -> None:
        role = self.config.mute_role
        has_role = role is not None

        if role is not None:
            self.role_select.default_values = [discord.SelectDefaultValue.from_role(role)]
        else:
            self.role_select.default_values = []

        self.create_btn.disabled = has_role
        self.sync_btn.disabled = not has_role
        self.unbind_btn.disabled = not has_role

        self._rebuild_layout()

    async def _refresh(self) -> None:
        """Re-pull the cached guild config after a mutation so the card stays in sync."""
        self.config = await self.cog.bot.db.get_guild_config(self.guild.id)

    async def _sync_with_progress(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        *,
        channels: Sequence[discord.abc.GuildChannel] | None = None,
    ) -> tuple[int, int, int]:
        """Apply the mute overwrites, editing a live ephemeral progress message as it goes.

        The interaction must already be deferred or responded to. Edits to the status
        message are throttled to roughly one per 1.5s (plus a final tick) so syncing a
        large server doesn't trip Discord's edit rate limits.
        """
        status = await interaction.followup.send(
            f"{Emojis.loading} Syncing channel permissions...", ephemeral=True, wait=True
        )

        last_edit = 0.0

        async def on_progress(done: int, total: int) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if done != total and now - last_edit < 1.5:
                return
            last_edit = now
            with suppress(discord.HTTPException):
                await status.edit(
                    content=f"{Emojis.loading} Syncing channel permissions... `{done}/{total}`"
                )

        result = await update_role_permissions(
            role, self.guild, interaction.user, channels=channels, progress=on_progress
        )
        with suppress(discord.HTTPException):
            await status.delete()
        return result

    # -- callbacks --------------------------------------------------------

    async def _on_role_select(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        role = self.role_select.values[0]
        guild = self.guild

        if role.is_default():
            await interaction.response.send_message(
                f"{Emojis.error} You cannot set the default role as the mute role.", ephemeral=True
            )
            return
        if role >= interaction.user.top_role and interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits at or above your top role.", ephemeral=True
            )
            return
        if role >= guild.me.top_role:
            await interaction.response.send_message(
                f"{Emojis.error} That role sits at or above mine in the hierarchy.", ephemeral=True
            )
            return
        if not interaction.app_permissions.manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Roles permission.", ephemeral=True
            )
            return

        await self._refresh()
        merge = False

        if self.config.mute_role is not None:
            picker = PreExistingMuteRoleView(
                interaction.user,
                content=(
                    "**There is already a mute role set up.**\n\n"
                    "Press **Merge** to combine the existing muted-member data with the new role "
                    "(this also adds the role to everyone who needs it — it is __slow__).\n"
                    "Press **Replace** to swap in the new role and discard the old member data."
                ),
            )
            await interaction.response.send_message(view=picker, ephemeral=True)
            picker.message = await interaction.original_response()
            await picker.wait()
            if picker.merge is None:
                return
            merge = picker.merge
        else:
            with_role = len(role.members)
            if with_role > 0:
                confirm = ConfirmationView(
                    interaction.user, timeout=180.0, delete_after=True,
                    content=(
                        f"{Emojis.warning} That role already has {pluralize(with_role):member}. "
                        "Make it the mute role anyway?"
                    ),
                )
                await interaction.response.send_message(view=confirm, ephemeral=True)
                confirm.message = await interaction.original_response()
                await confirm.wait()
                if not confirm.value:
                    return
            else:
                await interaction.response.defer(ephemeral=True)

        members: set[int] = set()
        if merge:
            members |= self.config.muted_members
            reason = f"Action done by {interaction.user} (ID: {interaction.user.id}): Merging mute roles"
            with suppress(discord.HTTPException):
                async with interaction.channel.typing():  # type: ignore[union-attr]
                    async for member in self.cog.bot.resolve_member_ids(guild, members):  # type: ignore[arg-type]
                        if not member._roles.has(role.id):
                            with suppress(discord.HTTPException):
                                await member.add_roles(role, reason=reason)

        members.update(m.id for m in role.members)
        await self.cog.bot.db.moderation.set_mute_role(guild.id, role.id, list(members))

        escaped = discord.utils.escape_mentions(role.name)
        await interaction.followup.send(
            f"{Emojis.success} Set **{escaped}** as the mute role.\n"
            "-# Permission overwrites were not changed — use **Sync Permissions** to apply them.",
            ephemeral=True,
        )

        await self._refresh()
        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_create(self, interaction: discord.Interaction) -> None:
        if not interaction.app_permissions.manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Roles permission.", ephemeral=True
            )
            return

        await self._refresh()
        if self.config.mute_role is not None:
            await interaction.response.send_message(
                f"{Emojis.error} A mute role is already set up. Unbind it first.", ephemeral=True
            )
            return

        modal = MuteRoleCreateModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        if modal.value is None:
            return

        try:
            role = await self.guild.create_role(
                name=modal.value,
                reason=f"Mute role created by {interaction.user} (ID: {interaction.user.id})",
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"{Emojis.error} Failed to create role: {e}", ephemeral=True)
            return

        await self.cog.bot.db.moderation.create_mute_role(self.guild.id, role.id)

        confirm = ConfirmationView(
            interaction.user, timeout=180.0, delete_after=True,
            content=f"Created {role.mention}. Apply restrictive permission overwrites to every channel?",
        )
        confirm.message = await interaction.followup.send(view=confirm, ephemeral=True)
        await confirm.wait()
        if confirm.value:
            success, failure, skipped = await self._sync_with_progress(interaction, role)
            await interaction.followup.send(
                f"{Emojis.success} Created {role.mention}. "
                f"Overwrites — updated `{success}`, failed `{failure}`, skipped `{skipped}`.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"{Emojis.success} Created {role.mention}.\n"
                "-# Overwrites unchanged — use **Sync Permissions** when ready.",
                ephemeral=True,
            )

        await self._refresh()
        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

    async def _on_sync(self, interaction: discord.Interaction) -> None:
        await self._refresh()
        role = self.config.mute_role
        if role is None:
            await interaction.response.send_message(
                f"{Emojis.error} No mute role is set up.", ephemeral=True
            )
            return
        if not interaction.app_permissions.manage_roles:
            await interaction.response.send_message(
                f"{Emojis.error} I need Manage Roles permission.", ephemeral=True
            )
            return
        if (
            isinstance(interaction.user, discord.Member)
            and role >= interaction.user.top_role
            and interaction.user.id != self.guild.owner_id
        ):
            await interaction.response.send_message(
                f"{Emojis.error} The mute role sits at or above your top role.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        success, failure, skipped = await self._sync_with_progress(interaction, role)
        total = success + failure + skipped
        await interaction.followup.send(
            f"{Emojis.success} Attempted to update {total} channels — "
            f"updated `{success}`, failed `{failure}`, skipped (*no permissions*) `{skipped}`.",
            ephemeral=True,
        )

    async def _on_unbind(self, interaction: discord.Interaction) -> None:
        await self._refresh()
        if self.config.mute_role is None:
            await interaction.response.send_message(
                f"{Emojis.error} No mute role is set up.", ephemeral=True
            )
            return

        muted = len(self.config.muted_members)
        if muted > 0:
            confirm = ConfirmationView(
                interaction.user, timeout=180.0, delete_after=True,
                content=f"{Emojis.warning} Unbind the mute role and unmute {pluralize(muted):member}?",
            )
            await interaction.response.send_message(view=confirm, ephemeral=True)
            confirm.message = await interaction.original_response()
            await confirm.wait()
            if not confirm.value:
                return
        else:
            await interaction.response.defer(ephemeral=True)

        await self.cog.bot.db.moderation.unbind_mute_role(self.guild.id)
        await interaction.followup.send(f"{Emojis.success} Mute role unbound.", ephemeral=True)

        await self._refresh()
        self.update_state()
        if interaction.message is not None:
            await interaction.message.edit(view=self)
