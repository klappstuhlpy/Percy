"""Persistent Components V2 role menu — buttons embedded inside a Container."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.rolemenu.engine import resolve_toggle
from app.core.views import LayoutView
from app.utils import helpers
from config import Emojis

if TYPE_CHECKING:
    import re

    import asyncpg

    from app.core.bot import Bot

__all__ = ('RoleMenuButton', 'build_menu_view')

BUTTONS_PER_ROW = 5


class RoleMenuButton(discord.ui.DynamicItem[discord.ui.Button], template=r'rolemenu:(?P<menu>[0-9]+):(?P<role>[0-9]+)'):
    """A persistent button that toggles a single role from a menu."""

    def __init__(
        self,
        menu_id: int,
        role_id: int,
        *,
        label: str | None = None,
        emoji: str | None = None,
    ) -> None:
        self.menu_id = menu_id
        self.role_id = role_id
        super().__init__(
            discord.ui.Button(
                label=label or 'Role',
                emoji=emoji or None,
                style=discord.ButtonStyle.secondary,
                custom_id=f'rolemenu:{menu_id}:{role_id}',
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction[Bot], _: discord.ui.Button, match: re.Match[str], /
    ) -> RoleMenuButton:
        return cls(int(match['menu']), int(match['role']))

    async def callback(self, interaction: discord.Interaction[Bot]) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        role = guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                f'{Emojis.error} That role no longer exists.', ephemeral=True)
            return

        if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role or role.managed:
            await interaction.response.send_message(
                f"{Emojis.error} I can't assign {role.mention} — it's above my highest role or managed by an integration.",
                ephemeral=True,
            )
            return

        menu = await interaction.client.db.rolemenu.get_menu(self.menu_id)
        unique = bool(menu['unique_roles']) if menu else False
        menu_role_ids: list[int] = []
        if unique:
            entries = await interaction.client.db.rolemenu.get_entries(self.menu_id)
            menu_role_ids = [entry['role_id'] for entry in entries]

        update = resolve_toggle(
            clicked_role=self.role_id,
            member_role_ids=[r.id for r in member.roles],
            menu_role_ids=menu_role_ids,
            unique=unique,
        )

        try:
            if update.add:
                await member.add_roles(role, reason='Role menu')
            for remove_id in update.remove:
                if (remove_role := guild.get_role(remove_id)) is not None:
                    await member.remove_roles(remove_role, reason='Role menu')
        except discord.HTTPException:
            await interaction.response.send_message(
                f'{Emojis.error} I could not update your roles. Please try again later.', ephemeral=True)
            return

        verb = 'Added' if update.add else 'Removed'
        await interaction.response.send_message(f'{Emojis.success} {verb} {role.mention}.', ephemeral=True)


def _build_container(
    menu_id: int,
    title: str,
    description: str | None,
    entries: list[asyncpg.Record],
    guild: discord.Guild,
    *,
    unique: bool,
) -> discord.ui.Container:
    """Builds the CV2 container with heading, role list, and action-row buttons."""
    container = discord.ui.Container(accent_colour=helpers.Colour.brand())

    header = f'## {title}'
    if description:
        header += f'\n{description}'
    container.add_item(discord.ui.TextDisplay(header))

    container.add_item(discord.ui.Separator())

    if entries:
        lines: list[str] = []
        for entry in entries:
            emoji = f"{entry['emoji']} " if entry['emoji'] else ''
            role = guild.get_role(entry['role_id'])
            mention = role.mention if role else f"<@&{entry['role_id']}>"
            label = entry['label'] or ''
            lines.append(f"{emoji}{mention}{f' — {label}' if label else ''}")
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
    else:
        container.add_item(discord.ui.TextDisplay('*No roles yet. Add some with `rolemenu add`.*'))

    container.add_item(discord.ui.Separator())

    if entries:
        for i in range(0, len(entries), BUTTONS_PER_ROW):
            chunk = entries[i:i + BUTTONS_PER_ROW]
            row = discord.ui.ActionRow()
            for entry in chunk:
                role = guild.get_role(entry['role_id'])
                label = role.name if role is not None else (entry['label'] or 'Unknown role')
                row.add_item(RoleMenuButton(menu_id, entry['role_id'], label=label[:80], emoji=entry['emoji']))
            container.add_item(row)

    footer = 'Pick one role at a time.' if unique else 'Pick as many roles as you like.'
    container.add_item(discord.ui.TextDisplay(f'-# {footer}'))
    return container


def build_menu_view(
    menu_id: int,
    title: str,
    description: str | None,
    entries: list[asyncpg.Record],
    guild: discord.Guild,
    *,
    unique: bool,
) -> LayoutView:
    """Builds the persistent CV2 layout view for a role menu."""
    view = LayoutView(timeout=None)
    view.add_item(_build_container(menu_id, title, description, entries, guild, unique=unique))
    return view
