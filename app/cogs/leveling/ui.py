from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.leveling.models import _MAX_LEVEL
from app.core import LayoutView
from app.rendering.card_style import CardStyle, resolve_style, style_options
from app.utils import get_asset_url
from app.utils.helpers import Colour
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from app.cogs.leveling.models import GuildLevelConfig
    from app.core.models import Context

__all__ = (
    "AddLevelRoleModal",
    "AddMultiplierRoleModal",
    "InteractiveLevelRolesView",
    "InteractiveMultiplierView",
    "RankCardEditor",
    "RemoveLevelRolesSelect",
    "RemoveMultiplierRolesSelect",
)


def _roles_summary(roles: dict[int, int]) -> str:
    """Render the configured level → role mapping as a markdown list."""
    if not roles:
        return "### Reward Roles\n-# No reward roles configured yet — add one with the select below."

    body = "\n".join(
        f"- **Level {level}** → <@&{role_id}>"
        for role_id, level in sorted(roles.items(), key=lambda pair: pair[1])
    )
    return f"### Reward Roles `({len(roles)}/25)`\n{body}"


def _multipliers_summary(multipliers: dict[int, float]) -> str:
    """Render the configured role → XP-multiplier mapping as a markdown list."""
    if not multipliers:
        return "### Multiplier Roles\n-# No multiplier roles configured yet — add one with the select below."

    body = "\n".join(
        f"- <@&{role_id}> → **+{value:g}×** XP"
        for role_id, value in sorted(multipliers.items(), key=lambda pair: pair[1], reverse=True)
    )
    return f"### Multiplier Roles `({len(multipliers)}/25)`\n{body}"


class AddLevelRoleModal(discord.ui.Modal):
    level = discord.ui.TextInput(
        label="Level",
        placeholder="Enter a level, e.g. 10",
        required=True,
        min_length=1,
        max_length=3,
    )

    def __init__(self, view: InteractiveLevelRolesView, *, role: discord.Role) -> None:
        self.view = view
        self.role = role
        self.level.label = "At what level will this role be assigned at?"
        super().__init__(title="Configure Level Role", timeout=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            level = int(self.level.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Level must be a number.", ephemeral=True)
            return

        if not 1 <= level <= _MAX_LEVEL:
            await interaction.response.send_message(
                f"{Emojis.error} Level must be between 1 and {_MAX_LEVEL}.", ephemeral=True
            )
            return

        self.view._roles[self.role.id] = level
        self.view._rebuild()
        await interaction.response.edit_message(view=self.view)


class RemoveLevelRolesSelect(discord.ui.RoleSelect["InteractiveLevelRolesView | InteractiveMultiplierView"]):
    def __init__(self, roles_ref: dict[int, int], ctx: Context) -> None:
        self._roles_ref = roles_ref
        self._ctx = ctx
        super().__init__(placeholder="Remove level roles...", max_values=25)
        self.update()

    def update(self) -> None:
        assert self._ctx.guild is not None
        self.options = [
            discord.SelectOption(
                label=f"Level {level}",
                description=f"@{self._ctx.guild.get_role(int(role_id))}",
                value=str(role_id),
                emoji=Emojis.trash,
            )
            for role_id, level in sorted(self._roles_ref.items(), key=lambda pair: pair[1])
        ]
        self.disabled = not self.options
        if self.disabled:
            self.options = [discord.SelectOption(label=".")]
        self.max_values = len(self.options)

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert self.view is not None
        try:
            for value in self.values:
                self._roles_ref.pop(int(value))  # type: ignore
        except KeyError:
            pass

        self.view._rebuild()
        await interaction.response.edit_message(view=self.view)


class InteractiveLevelRolesView(LayoutView):
    """Components V2 dashboard for managing level-up reward roles.

    Mirrors the sentinel/mute-role setup cards: a single brand-accented container
    holds the live role list, the role-stacking status and every control, rebuilt on
    each interaction.
    """

    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()
        self._role_stack = config.role_stack
        self._status: str | None = None

        self.add_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder="Add a new level role reward...", min_values=1, max_values=1
        )
        self.add_select.callback = self._on_add_role  # type: ignore[assignment]

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)

        self.stack_toggle: discord.ui.Button = discord.ui.Button(style=discord.ButtonStyle.blurple)
        self.stack_toggle.callback = self._on_toggle_stack  # type: ignore[assignment]

        self.save_btn: discord.ui.Button = discord.ui.Button(label="Save", style=discord.ButtonStyle.success)
        self.save_btn.callback = self._on_save  # type: ignore[assignment]

        self.cancel_btn: discord.ui.Button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        self.cancel_btn.callback = self._on_cancel  # type: ignore[assignment]

        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        self.remove_select.update()
        self.stack_toggle.label = "Disable Role Stack" if self._role_stack else "Enable Role Stack"

        container = discord.ui.Container(accent_colour=Colour.brand())
        container.add_item(
            discord.ui.Section(
                "## Level Role Rewards\n-# Roles handed out automatically as members level up",
                accessory=discord.ui.Thumbnail(get_asset_url(self.ctx.guild)),
            )
        )

        container.add_item(discord.ui.Separator())
        stack_state = (
            f"{Emojis.success} **ON** — members keep every role they earn"
            if self._role_stack
            else f"{Emojis.error} **OFF** — members keep only their highest role"
        )
        container.add_item(discord.ui.TextDisplay(f"### Role Stacking\n{stack_state}"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(_roles_summary(self._roles)))

        container.add_item(discord.ui.ActionRow(self.add_select))
        container.add_item(discord.ui.ActionRow(self.remove_select))
        container.add_item(discord.ui.ActionRow(self.stack_toggle, self.save_btn, self.cancel_btn))

        container.add_item(discord.ui.Separator())
        footer = self._status or "Press **Save** to apply your changes."
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))

        self.add_item(container)

    def _disable_controls(self) -> None:
        self.add_select.disabled = True
        self.remove_select.disabled = True
        self.stack_toggle.disabled = True
        self.save_btn.disabled = True
        self.cancel_btn.disabled = True

    async def _on_add_role(self, interaction: discord.Interaction) -> None:
        role = self.add_select.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "That role is a default or managed role, which means I am unable to assign it.\n"
                "Try using a different role or creating a new one.",
                ephemeral=True,
            )
            return

        assert self.ctx.guild is not None
        if not role.is_assignable():
            await interaction.response.send_message(
                f"That role is higher than or equal to my top role ({self.ctx.guild.me.top_role.mention}) in the "
                f"hierarchy, which means I am unable to assign it.\nMove the role below {self.ctx.guild.me.top_role.mention} "
                "and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AddLevelRoleModal(self, role=role))

    async def _on_toggle_stack(self, interaction: discord.Interaction) -> None:
        self._role_stack = not self._role_stack
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_save(self, interaction: discord.Interaction) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        self._disable_controls()
        self._status = f"{Emojis.loading} Updating roles across the server..."
        self._rebuild()
        await interaction.response.edit_message(view=self)

        await self.config.update_all_roles()

        self._status = f"{Emojis.success} Saved and updated level roles."
        self._rebuild()
        await interaction.edit_original_response(view=self)
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self._disable_controls()
        self._status = f"{Emojis.error} Cancelled — changes were discarded."
        self._rebuild()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        await self.config.update_all_roles()


class AddMultiplierRoleModal(discord.ui.Modal):
    multiplier = discord.ui.TextInput(
        label="XP Multiplier",
        placeholder="e.g. 0.5 for +50% XP, 1 for double XP",
        required=True,
        min_length=1,
        max_length=6,
    )

    def __init__(self, view: InteractiveMultiplierView, *, role: discord.Role) -> None:
        self.view = view
        self.role = role
        super().__init__(title="Configure Multiplier Role", timeout=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = float(self.multiplier.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Multiplier must be a number.", ephemeral=True)
            return

        if value <= 0:
            await interaction.response.send_message(
                f"{Emojis.error} Multiplier must be greater than 0.", ephemeral=True
            )
            return

        self.view._multipliers[self.role.id] = value
        self.view._rebuild()
        await interaction.response.edit_message(view=self.view)


class RemoveMultiplierRolesSelect(discord.ui.RoleSelect["InteractiveMultiplierView"]):
    def __init__(self, multipliers_ref: dict[int, float], ctx: Context) -> None:
        self._multipliers_ref = multipliers_ref
        self._ctx = ctx
        super().__init__(placeholder="Remove multiplier roles...", max_values=25)
        self.update()

    def update(self) -> None:
        assert self._ctx.guild is not None
        self.options = [
            discord.SelectOption(
                label=f"+{value:g}×",
                description=f"@{self._ctx.guild.get_role(int(role_id))}",
                value=str(role_id),
                emoji=Emojis.trash,
            )
            for role_id, value in sorted(self._multipliers_ref.items(), key=lambda pair: pair[1], reverse=True)
        ]
        self.disabled = not self.options
        if self.disabled:
            self.options = [discord.SelectOption(label=".")]
        self.max_values = len(self.options)

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert self.view is not None
        try:
            for value in self.values:
                self._multipliers_ref.pop(int(value))  # type: ignore
        except KeyError:
            pass

        self.view._rebuild()
        await interaction.response.edit_message(view=self.view)


class InteractiveMultiplierView(LayoutView):
    """Components V2 dashboard for configuring per-role XP multipliers.

    Shares the brand-accented card style of :class:`InteractiveLevelRolesView`, but
    edits :attr:`GuildLevelConfig.multiplier_roles` (role → bonus added to the base
    ``1.0`` XP multiplier) rather than the level-reward roles.
    """

    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._multipliers: dict[int, float] = dict(config.multiplier_roles)
        self._status: str | None = None

        self.add_select: discord.ui.RoleSelect = discord.ui.RoleSelect(
            placeholder="Add a new multiplier role...", min_values=1, max_values=1
        )
        self.add_select.callback = self._on_add_role  # type: ignore[assignment]

        self.remove_select = RemoveMultiplierRolesSelect(self._multipliers, ctx)

        self.save_btn: discord.ui.Button = discord.ui.Button(label="Save", style=discord.ButtonStyle.success)
        self.save_btn.callback = self._on_save  # type: ignore[assignment]

        self.cancel_btn: discord.ui.Button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        self.cancel_btn.callback = self._on_cancel  # type: ignore[assignment]

        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        self.remove_select.update()

        container = discord.ui.Container(accent_colour=Colour.brand())
        container.add_item(
            discord.ui.Section(
                "## Level Multiplier Roles\n-# Grant bonus XP to members holding the configured roles",
                accessory=discord.ui.Thumbnail(get_asset_url(self.ctx.guild)),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(_multipliers_summary(self._multipliers)))
        container.add_item(discord.ui.ActionRow(self.add_select))
        container.add_item(discord.ui.ActionRow(self.remove_select))
        container.add_item(discord.ui.ActionRow(self.save_btn, self.cancel_btn))

        container.add_item(discord.ui.Separator())
        footer = self._status or "Bonus is added to the base 1× multiplier. Press **Save** to apply."
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))
        self.add_item(container)

    def _disable_controls(self) -> None:
        self.add_select.disabled = True
        self.remove_select.disabled = True
        self.save_btn.disabled = True
        self.cancel_btn.disabled = True

    async def _on_add_role(self, interaction: discord.Interaction) -> None:
        role = self.add_select.values[0]
        if role.is_default():
            await interaction.response.send_message(
                f"{Emojis.error} You cannot assign a multiplier to the default role.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AddMultiplierRoleModal(self, role=role))

    async def _on_save(self, interaction: discord.Interaction) -> None:
        await self.config.update(multiplier_roles=self._multipliers)
        self._disable_controls()
        self._status = f"{Emojis.success} Saved multiplier roles."
        self._rebuild()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self._disable_controls()
        self._status = f"{Emojis.error} Cancelled — changes were discarded."
        self._rebuild()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        await self.config.update(multiplier_roles=self._multipliers)


_CARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("theme", "Background theme"),
    ("accent", "Accent colour"),
    ("bar", "Progress bar"),
    ("font", "Name font"),
)


class _CardFieldSelect(discord.ui.Select["RankCardEditor"]):
    """One preset picker; choosing an option only changes the preview."""

    def __init__(self, field: str, placeholder: str, current: str) -> None:
        options = style_options()[field]
        super().__init__(
            placeholder=placeholder,
            options=[
                discord.SelectOption(label=f"{placeholder}: {o['label']}", value=o["value"], default=o["value"] == current)
                for o in options
            ],
        )
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.draft[self.field] = self.values[0]
        await self.view.refresh(interaction)


class RankCardEditor(LayoutView):
    """Live rank card editor: pick presets, watch the preview, then Save.

    Nothing is stored until **Save** -- the preview is a draft, which the header says.
    """

    def __init__(
        self,
        *,
        member: discord.Member,
        render: Callable[[CardStyle], Awaitable[discord.File]],
        save: Callable[[dict[str, str]], Awaitable[None]],
        guild_default: dict[str, str],
        current: dict[str, str],
    ) -> None:
        super().__init__(timeout=600.0, members=member, disable_on_timeout=True)
        self.member = member
        self._render = render
        self._save = save
        self.guild_default = guild_default
        self.saved = dict(current)
        self.draft = dict(current)
        self.message: discord.Message | None = None

    @property
    def style(self) -> CardStyle:
        return resolve_style(self.guild_default, self.draft)

    async def build(self, *, note: str | None = None) -> discord.File:
        """(Re)build the layout around a freshly rendered preview and return its file."""
        file = await self._render(self.style)
        dirty = self.draft != self.saved
        status = note or ("-# Preview only — press **Save** to keep it." if dirty else "-# This is your saved card.")

        container = discord.ui.Container(accent_colour=Colour.brand())
        container.add_item(discord.ui.TextDisplay(f"### Rank card\n{status}"))
        container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{file.filename}")))
        container.add_item(discord.ui.Separator())
        style = self.style.to_dict()
        for field, label in _CARD_FIELDS:
            container.add_item(discord.ui.ActionRow(_CardFieldSelect(field, label, style[field])))
        container.add_item(
            discord.ui.TextDisplay("-# Custom background images: coming soon. Your style applies on every server that allows it.")
        )

        save = discord.ui.Button(label="Save", style=discord.ButtonStyle.success, disabled=not dirty)
        save.callback = self._on_save
        reset = discord.ui.Button(label="Use server default", style=discord.ButtonStyle.secondary, disabled=not self.draft)
        reset.callback = self._on_reset
        container.add_item(discord.ui.ActionRow(save, reset))

        self.clear_items()
        self.add_item(container)
        return file

    async def refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        await interaction.response.defer()
        file = await self.build(note=note)
        await interaction.edit_original_response(view=self, attachments=[file])

    async def _on_save(self, interaction: discord.Interaction) -> None:
        await self._save(self.draft)
        self.saved = dict(self.draft)
        await self.refresh(interaction, note=f"-# {Emojis.success} Saved. `/level` now shows this card.")

    async def _on_reset(self, interaction: discord.Interaction) -> None:
        self.draft = {}
        await self.refresh(interaction)
