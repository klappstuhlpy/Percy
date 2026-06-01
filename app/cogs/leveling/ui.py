from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.cogs.leveling.models import _MAX_LEVEL
from app.core import View
from app.utils import helpers
from config import Emojis

if TYPE_CHECKING:
    from typing import Any

    from app.cogs.leveling.models import GuildLevelConfig
    from app.core.models import Context

__all__ = (
    'AddLevelRoleModal',
    'InteractiveLevelRolesView',
    'InteractiveMultiplierView',
    'RemoveLevelRolesSelect',
    'RoleStackToggle',
)


class AddLevelRoleModal(discord.ui.Modal):
    level = discord.ui.TextInput(
        label='Level',
        placeholder='Enter a level, e.g. 10',
        required=True,
        min_length=1,
        max_length=3,
    )

    def __init__(self, view: InteractiveLevelRolesView, *, role: discord.Role) -> None:
        self.view = view
        self.role = role
        self.level.label = 'At what level will this role be assigned at?'
        super().__init__(title='Configure Level Role', timeout=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        level = int(self.level.value)
        if not 1 <= level <= _MAX_LEVEL:
            await interaction.response.send_message(f'Level must be between 1 and {_MAX_LEVEL}.', ephemeral=True)
            return

        self.view._roles[self.role.id] = level
        self.view.remove_select.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RemoveLevelRolesSelect(discord.ui.RoleSelect['InteractiveLevelRolesView']):
    def __init__(self, roles_ref: dict[int, int], ctx: Context) -> None:
        self._roles_ref = roles_ref
        self._ctx = ctx
        super().__init__(placeholder='Remove level roles...', row=1, max_values=25)
        self.update()

    def update(self) -> None:
        assert self._ctx.guild is not None
        self.options = [
            discord.SelectOption(
                label=f'Level {level}',
                description=f'@{self._ctx.guild.get_role(int(role_id))}',
                value=str(role_id),
                emoji=Emojis.trash,
            )
            for role_id, level in sorted(self._roles_ref.items(), key=lambda pair: pair[1])
        ]
        self.disabled = not self.options
        if self.disabled:
            self.options = [discord.SelectOption(label='.')]
        self.max_values = len(self.options)

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert self.view is not None
        try:
            for value in self.values:
                self._roles_ref.pop(int(value))  # type: ignore
        except KeyError:
            pass

        self.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RoleStackToggle(discord.ui.Button['InteractiveLevelRolesView']):
    def __init__(self, current: bool) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=f'{'Disable' if current else 'Enable'} Role Stack',
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view._role_stack = new = not self.view._role_stack
        self.label = f'{'Disable' if new else 'Enable'} Role Stack'
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class InteractiveLevelRolesView(View):
    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()
        self._role_stack = config.role_stack

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)
        self.add_item(self.remove_select)
        self.add_item(RoleStackToggle(self._role_stack))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(color=helpers.Colour.white(), timestamp=self.ctx.now)
        embed.set_author(name=f'{self.ctx.guild} Level Role Rewards', icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        embed.set_footer(text='Make sure to save your changes by pressing the Save button!')

        indicator = 'Users can accumulate multiple level roles.' if self._role_stack else 'Users can only have the highest level role.'
        embed.add_field(name='Role Stack', value=f'{Emojis.success if self._role_stack else Emojis.error} {indicator}')

        if not self._roles:
            embed.description = 'You have not configured any level role rewards yet.'
            return embed

        embed.insert_field_at(
            index=0,
            name=f'Level Roles ({len(self._roles)}/25 slots)',
            value='\n'.join(
                f'- Level {level}: <@&{role_id}>'
                for role_id, level in sorted(self._roles.items(), key=lambda pair: pair[1])
            ),
            inline=False
        )
        return embed

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder='Add a new level role reward...',
        min_values=1,
        max_values=1,
        row=0,
    )
    async def add_level_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                'That role is a default role or managed role, which means I am unable to assign it.\n'
                'Try using a different role or creating a new one.',
                ephemeral=True,
            )
            return

        assert self.ctx.guild is not None
        if not role.is_assignable():
            await interaction.response.send_message(
                f'That role is lower than or equal to my top role ({self.ctx.guild.me.top_role.mention}) in the role hierarchy, '
                f'which means I am unable to assign it.\nTry moving the role to be lower than {self.ctx.guild.me.top_role.mention}, '
                'and then try again.',
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(AddLevelRoleModal(self, role=role))

    @discord.ui.button(label='Save', style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, _) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        for child in self.children:
            child.disabled = True  # type: ignore[misc]

        embed = self.make_embed()
        embed.colour = helpers.Colour.yellow()
        await interaction.response.edit_message(content='Updating roles...', embed=embed, view=self)

        await self.config.update_all_roles()

        embed.colour = helpers.Colour.lime_green()
        await interaction.edit_original_response(content='Saved and updated level roles.', embed=embed, view=self)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, _) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[misc]

        embed = self.make_embed()
        embed.colour = helpers.Colour.light_red()
        await interaction.response.edit_message(content='Cancelled. Changes were discarded.', embed=embed, view=self)

    async def on_timeout(self) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        await self.config.update_all_roles()


class InteractiveMultiplierView(View):
    def __init__(self, ctx: Context, *, config: GuildLevelConfig) -> None:
        super().__init__(timeout=300, members=ctx.author)
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()
        self._role_stack = config.role_stack

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)
        self.add_item(self.remove_select)
        self.add_item(RoleStackToggle(self._role_stack))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(color=helpers.Colour.white(), timestamp=self.ctx.now)
        embed.set_author(name=f'{self.ctx.guild} Level Role Rewards', icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        embed.set_footer(text='Make sure to save your changes by pressing the Save button!')

        indicator = 'Users can accumulate multiple level roles.' if self._role_stack else 'Users can only have the highest level role.'
        embed.add_field(name='Role Stack', value=f'{Emojis.success if self._role_stack else Emojis.error} {indicator}')

        if not self._roles:
            embed.description = 'You have not configured any level role rewards yet.'
            return embed

        embed.insert_field_at(
            index=0,
            name=f'Level Roles ({len(self._roles)}/25 slots)',
            value='\n'.join(
                f'- Level {level}: <@&{role_id}>'
                for role_id, level in sorted(self._roles.items(), key=lambda pair: pair[1])
            ),
            inline=False
        )
        return embed

    async def on_timeout(self) -> None:
        await self.config.update()
