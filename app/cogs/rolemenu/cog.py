from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.rolemenu import ui
from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, describe, group
from app.core.pagination import LinePaginator
from app.utils import helpers

if TYPE_CHECKING:
    import asyncpg

#: Discord allows at most 25 components (5 rows of 5) on a message.
MAX_ROLES = 25


class RoleMenus(Cog):
    """Self-assignable roles via persistent buttons."""

    emoji = '\N{LABEL}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        bot.add_dynamic_items(ui.RoleMenuButton)

    async def _render_menu(self, menu: asyncpg.Record) -> None:
        """Re-render a menu's message to reflect its current entries."""
        if menu['message_id'] is None:
            return
        channel = self.bot.get_channel(menu['channel_id'])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        entries = await self.bot.db.rolemenu.get_entries(menu['id'])
        embed = ui.build_menu_embed(
            menu['title'], menu['description'], entries, unique=menu['unique_roles']
        )
        view = ui.build_menu_view(menu['id'], entries, channel.guild)
        try:
            message = await channel.fetch_message(menu['message_id'])
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

    @group(
        'rolemenu',
        fallback='list',
        aliases=['rolemenus', 'reactionroles'],
        description='Create and manage self-assignable role menus.',
        guild_only=True,
        hybrid=True,
    )
    async def rolemenu(self, ctx: Context) -> None:
        """List the server's role menus."""
        assert ctx.guild is not None
        menus = await self.bot.db.rolemenu.get_guild_menus(ctx.guild.id)
        if not menus:
            await ctx.send_info('There are no role menus yet. Create one with `rolemenu create`.')
            return

        entries = [
            f"**#{menu['id']}** • {menu['title']} — <#{menu['channel_id']}>"
            f"{' *(unique)*' if menu['unique_roles'] else ''}"
            for menu in menus
        ]
        embed = discord.Embed(title='Role Menus', description='', colour=helpers.Colour.white())
        await LinePaginator.start(ctx, entries=entries, embed=embed, location='description')

    @rolemenu.command(
        'create',
        description='Create a new role menu in a channel.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(channel='Where to post the menu.', title='The menu title.', description='Optional description text.')
    async def rolemenu_create(
        self, ctx: Context, channel: discord.TextChannel, title: str, *, description: str | None = None
    ) -> None:
        """Create a role menu (initially empty — add roles with `rolemenu add`)."""
        assert ctx.guild is not None
        record = await self.bot.db.rolemenu.create_menu(ctx.guild.id, channel.id, title, description)

        embed = ui.build_menu_embed(title, description, [], unique=False)
        try:
            message = await channel.send(embed=embed, view=ui.build_menu_view(record['id'], [], channel.guild))
        except discord.HTTPException:
            await self.bot.db.rolemenu.delete_menu(record['id'])
            await ctx.send_error(f"I couldn't post a message in {channel.mention}.")
            return

        await self.bot.db.rolemenu.set_message(record['id'], message.id)
        await ctx.send_success(
            f"Created role menu **#{record['id']}** in {channel.mention}. "
            f"Add roles with `rolemenu add {record['id']} <role>`."
        )

    @rolemenu.command(
        'add',
        description='Add a role to a menu.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(
        menu_id='The menu number (from `rolemenu list`).',
        role='The role to offer.',
        emoji='An optional emoji for the button.',
        label='An optional button label/description.',
    )
    async def rolemenu_add(
        self,
        ctx: Context,
        menu_id: int,
        role: discord.Role,
        emoji: str | None = None,
        *,
        label: str | None = None,
    ) -> None:
        """Add a role to an existing menu."""
        assert ctx.guild is not None
        menu = await self.bot.db.rolemenu.get_guild_menu(ctx.guild.id, menu_id)
        if menu is None:
            await ctx.send_error(f'Role menu **#{menu_id}** does not exist.')
            return
        if role.is_default():
            await ctx.send_error('You cannot add the @​everyone role.')
            return
        if role >= ctx.guild.me.top_role or role.managed:
            await ctx.send_error(
                f"I can't assign {role.mention} — it's above my highest role or managed by an integration."
            )
            return

        if await self.bot.db.rolemenu.count_entries(menu_id) >= MAX_ROLES:
            await ctx.send_error(f'A role menu can hold at most {MAX_ROLES} roles.')
            return

        position = await self.bot.db.rolemenu.count_entries(menu_id)
        record = await self.bot.db.rolemenu.add_entry(menu_id, role.id, emoji, label, position)
        if record is None:
            await ctx.send_error(f'{role.mention} is already in menu **#{menu_id}**.')
            return

        await self._render_menu(menu)
        await ctx.send_success(f'Added {role.mention} to menu **#{menu_id}**.')

    @rolemenu.command(
        'remove',
        aliases=['delete-role', 'rm'],
        description='Remove a role from a menu.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(menu_id='The menu number.', role='The role to remove.')
    async def rolemenu_remove(self, ctx: Context, menu_id: int, role: discord.Role) -> None:
        """Remove a role from a menu."""
        assert ctx.guild is not None
        menu = await self.bot.db.rolemenu.get_guild_menu(ctx.guild.id, menu_id)
        if menu is None:
            await ctx.send_error(f'Role menu **#{menu_id}** does not exist.')
            return

        record = await self.bot.db.rolemenu.remove_entry(menu_id, role.id)
        if record is None:
            await ctx.send_error(f'{role.mention} is not in menu **#{menu_id}**.')
            return

        await self._render_menu(menu)
        await ctx.send_success(f'Removed {role.mention} from menu **#{menu_id}**.')

    @rolemenu.command(
        'unique',
        description='Toggle whether a menu allows only one role at a time.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(menu_id='The menu number.', enabled='Whether only one role may be selected at a time.')
    async def rolemenu_unique(self, ctx: Context, menu_id: int, enabled: bool) -> None:
        """Toggle radio-style (single-choice) behaviour for a menu."""
        assert ctx.guild is not None
        menu = await self.bot.db.rolemenu.get_guild_menu(ctx.guild.id, menu_id)
        if menu is None:
            await ctx.send_error(f'Role menu **#{menu_id}** does not exist.')
            return

        await self.bot.db.rolemenu.set_unique(menu_id, enabled)
        refreshed = await self.bot.db.rolemenu.get_guild_menu(ctx.guild.id, menu_id)
        if refreshed is not None:
            await self._render_menu(refreshed)
        mode = 'single-choice' if enabled else 'multi-choice'
        await ctx.send_success(f'Menu **#{menu_id}** is now **{mode}**.')

    @rolemenu.command(
        'delete',
        description='Delete a role menu and its message.',
        user_permissions=PermissionTemplate.manager,
    )
    @describe(menu_id='The menu number to delete.')
    async def rolemenu_delete(self, ctx: Context, menu_id: int) -> None:
        """Delete a role menu entirely."""
        assert ctx.guild is not None
        menu = await self.bot.db.rolemenu.get_guild_menu(ctx.guild.id, menu_id)
        if menu is None:
            await ctx.send_error(f'Role menu **#{menu_id}** does not exist.')
            return

        await self.bot.db.rolemenu.delete_menu(menu_id)

        channel = self.bot.get_channel(menu['channel_id'])
        if isinstance(channel, (discord.TextChannel, discord.Thread)) and menu['message_id'] is not None:
            try:
                message = await channel.fetch_message(menu['message_id'])
                await message.delete()
            except discord.HTTPException:
                pass

        await ctx.send_success(f'Deleted role menu **#{menu_id}**.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(RoleMenus(bot))
