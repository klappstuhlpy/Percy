from typing import TypedDict, Required, Sequence, NotRequired, ClassVar

import discord
from discord import AutoModRuleEventType, AutoModRuleTriggerType, AutoModRuleActionType, Interaction
from discord.automod import *
from discord.ext import commands
from discord.utils import MISSING

from app.core import Cog, Context, View, command
from app.database import GuildConfig
from app.utils import helpers, get_asset_url, TimeDelta, letter_emoji, format_fields
from config import Emojis


class AutoModRulePreset(TypedDict):
    id: Required[int]

    name: Required[str]
    event_type: Required[AutoModRuleEventType]
    trigger: Required[AutoModTrigger]
    actions: list[AutoModRuleAction]
    enabled: bool
    exempt_roles: NotRequired[Sequence[discord.abc.Snowflake]]
    exempt_channels: NotRequired[Sequence[discord.abc.Snowflake]]


class AutoModPresets:
    """A class to represent the automod presets.

    This class contains automod rule presets that can be used to create automod rules.
    This class also contains helper methdos for converting the presets to embeds and select options.

    The logic behind this is the following:
    - By default, the presets are not created in the users guild and show up as disabled.
    - If a user wants to apply a preset to their guild, they can simply modfiy the preset and/or click
      the "Enable" button to enable the preset.
    - To apply all changes made, the user can click the "Finish" button to save the changes.
    - Currently, only the bots presets can be modified and created, not custom ones or from other users.
      This is done to be able to make the automod creation process as easy as possible for the end user.
      Also, because I currently don't have the motivation to implement a full automod rule creation system that
      covers all the functionality that the discord automod system provides.

    Attributes:
        links: A preset for blocking links.
        capital_spam: A preset for blocking capital spam.
        invites_spam: A preset for blocking invites spam.
        bad_words: A preset for blocking bad words.
    """

    __PREFIX__ = 'Percy Rule'

    links: ClassVar[AutoModRulePreset] = {
        'id': 0,
        'name': f'{__PREFIX__} Links',
        'event_type': AutoModRuleEventType.message_send,
        'trigger': AutoModTrigger(
            type=AutoModRuleTriggerType.keyword,
            regex_patterns=[r'https?://', r'www.'],
            allow_list=['*.gif', '*.jpg', '*.jpeg', '*.png', '*.webp', 'http://tenor.com/*', 'https://tenor.com/*']
        ),
        'actions': [
            AutoModRuleAction(
                type=AutoModRuleActionType.block_message,
                custom_message='This message was prevented by a AutoMod rule created by Percy.'
            ),
        ],
        'enabled': False,
        'exempt_roles': MISSING,
        'exempt_channels': MISSING,
    }
    capital_spam: ClassVar[AutoModRulePreset] = {
        'id': 0,
        'name': f'{__PREFIX__} Capital Spam',
        'event_type': AutoModRuleEventType.message_send,
        'trigger': AutoModTrigger(
            type=AutoModRuleTriggerType.keyword,
            regex_patterns=[r'(?-i)^[A-Z\s]+$']
        ),
        'actions': [
            AutoModRuleAction(
                type=AutoModRuleActionType.block_message,
                custom_message='This message was prevented by a AutoMod rule created by Percy.'
            ),
        ],
        'enabled': False,
        'exempt_roles': MISSING,
        'exempt_channels': MISSING,
    }
    invites_spam: ClassVar[AutoModRulePreset] = {
        'id': 0,
        'name': f'{__PREFIX__} Invites Spam',
        'event_type': AutoModRuleEventType.message_send,
        'trigger': AutoModTrigger(
            type=AutoModRuleTriggerType.keyword,
            regex_patterns=[r'discord(?:.com|app.com|.gg)[/invite/]?(?:[a-zA-Z0-9-]{2,32})']
        ),
        'actions': [
            AutoModRuleAction(
                type=AutoModRuleActionType.block_message,
                custom_message='This message was prevented by a AutoMod rule created by Percy.'
            ),
        ],
        'enabled': False,
        'exempt_roles': MISSING,
        'exempt_channels': MISSING,
    }
    bad_words: ClassVar[AutoModRulePreset] = {
        'id': 0,
        'name': f'{__PREFIX__} Bad Words',
        'event_type': AutoModRuleEventType.message_send,
        'trigger': AutoModTrigger(
            type=AutoModRuleTriggerType.keyword,
            keyword_filter=[]
        ),
        'actions': [
            AutoModRuleAction(
                type=AutoModRuleActionType.block_message,
                custom_message='This message was prevented by a AutoMod rule created by Percy.'
            ),
        ],
        'enabled': False,
        'exempt_roles': MISSING,
        'exempt_channels': MISSING,
    }

    @classmethod
    def to_embed(cls, preset: AutoModRulePreset) -> discord.Embed:
        """Convert a preset to an embed.

        Parameters
        ----------
        preset : AutoModRulePreset
            The preset to convert to an embed.

        Returns
        -------
        discord.Embed
            The embed that represents the preset.
        """
        embed = discord.Embed(title=preset['name'], color=helpers.Colour.white())

        embed.add_field(name='Event Type', value=preset['event_type'].name, inline=False)

        trigger = format_fields(preset['trigger'].to_metadata_dict()) if preset['trigger'].to_metadata_dict() else '...'
        embed.add_field(name='Trigger', value=f'```py\n{trigger}```', inline=False)

        actions = [
            format_fields(action.to_dict()['metadata'] | {'type': action.type.name.replace('_', ' ').title()})
            for action in preset['actions']
        ]
        embed.add_field(name='Actions',
                        value=f'```py\n{'\n'.join(actions)}```', inline=False)
        embed.add_field(name='Enabled', value=preset['enabled'], inline=False)

        roles = ', '.join(f'<@&{role.id}>' for role in preset['exempt_roles']) if preset['exempt_roles'] else '...'
        embed.add_field(name='Exempt Roles', value=roles, inline=False)

        channels = ', '.join(f'<#{channel.id}>' for channel in preset['exempt_channels']) if preset['exempt_channels'] else '...'
        embed.add_field(name='Exempt Channels', value=channels, inline=False)

        embed.set_footer(text=f'ID: {preset['id']} (ID = 0 means that the rule is not created yet.)')
        return embed

    @classmethod
    def to_list(cls) -> list[AutoModRulePreset]:
        """Convert the presets to select options.

        Returns
        -------
        list[AutoModRulePreset]
            The presets as a list.
        """
        return [cls.links, cls.capital_spam, cls.invites_spam, cls.bad_words]

    @classmethod
    def from_rules(cls, rules: list[AutoModRule], existing: set[str]) -> list[AutoModRulePreset]:
        """Convert the rules to presets.

        Parameters
        ----------
        rules : list[AutoModRule]
            The rules to convert to presets.
        existing : set[str]
            The existing rules.

        Returns
        -------
        list[AutoModRulePreset]
            The presets.
        """
        presets = list(filter(lambda preset: preset['name'] not in existing, cls.to_list()))
        return [  # type: ignore
            {
                'id': rule.id,
                'name': rule.name,
                'event_type': rule.event_type,
                'trigger': rule.trigger,
                'actions': rule.actions,
                'enabled': rule.enabled,
                'exempt_roles': rule.exempt_roles,
                'exempt_channels': rule.exempt_channels
            }
            for rule in rules
        ] + presets


class BasicTextInputModal(discord.ui.Modal, title='Text Input'):
    text_input = discord.ui.TextInput(label='Input', placeholder='Enter text...', style=discord.TextStyle.long)

    def __init__(self, placeholder: str = 'Enter text...', default: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.text_input.placeholder = placeholder
        self.text_input.default = default
        self.interaction: Interaction | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class AlertChannelSelect(View):
    """A view for selecting the alert channel."""

    def __init__(self) -> None:
        super().__init__(timeout=60., delete_on_timeout=True)
        self.channel: discord.TextChannel | None = None

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, placeholder='Select a channel...', min_values=1, max_values=1,
        channel_types=[_t for _t in discord.ChannelType if 'voice' not in _t.name]
    )
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        """Select a channel."""
        self.interaction = interaction
        self.channel = select.values[0]
        self.stop()


class InteractiveAutoModRuleSetupView(View):
    """An interactive view for setting up automod rules."""

    def __init__(
            self,
            ctx: Context,
            *,
            presets: list[AutoModRulePreset],
            linked_rules: set[str]
    ) -> None:
        super().__init__(timeout=300., members=ctx.author, delete_on_timeout=True)
        self.ctx = ctx

        self.presets: list[AutoModRulePreset] = presets
        self.linked_rules: set[str] = linked_rules
        self.selected_preset: AutoModRulePreset | None = None

        self.to_update: set[str] = set()
        self.to_create: set[str] = set()

        self.select_preset.options = [
            discord.SelectOption(emoji=letter_emoji(i), label=preset['name'], value=preset['name'])
            for i, preset in enumerate(self.presets)
        ]
        self.select_action.options = [
            discord.SelectOption(emoji=emoji, label=label, value=str(value))
            for emoji, label, value in [
                (Emojis.trash, 'Block Message', 1),
                ('\N{WARNING SIGN}', 'Send Alert Message', 2),
                ('\N{BELL}', 'Timeout', 3),
                ('\N{NO ENTRY}', 'Block Member Interactions', 4)
            ]
        ]

        self.update_items()

    def update_items(self) -> None:
        if self.selected_preset is None:
            self.disable_all()
            self.select_preset.disabled = False
            return
        self.enable_all()

        self.add_keyword.disabled = self.selected_preset['trigger'].type != AutoModRuleTriggerType.keyword
        self.enable_disable.label = 'Disable' if self.selected_preset['enabled'] else 'Enable'
        self.enable_disable.style = discord.ButtonStyle.red if self.selected_preset['enabled'] else discord.ButtonStyle.green

        for option in self.select_preset.options:
            option.default = option.value == self.selected_preset['name']

        for option in self.select_action.options:
            option.default = option.value in [str(action.type.value) for action in self.selected_preset['actions']]

        self.select_exempt_channels.default_values = [
            discord.SelectDefaultValue.from_channel(channel)
            for channel in self.selected_preset['exempt_channels'] or []
        ]
        self.select_exempt_roles.default_values = [
            discord.SelectDefaultValue.from_role(role)
            for role in self.selected_preset['exempt_roles'] or []
        ]

    @discord.ui.select(placeholder='Select a preset...', row=0, min_values=1, max_values=1, options=[])
    async def select_preset(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Select a preset."""
        try:
            self.selected_preset = next(preset for preset in self.presets if preset['name'] == select.values[0])
        except StopIteration:
            await interaction.response.send_message('An error occurred while selecting the preset.', ephemeral=True)
            return

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(self.selected_preset), view=self)

    @discord.ui.select(placeholder='Select actions...', row=1, options=[], min_values=1, max_values=4)
    async def select_action(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Select an action."""
        preset = self.selected_preset
        if preset is None:
            await interaction.response.send_message('You must select a preset first.', ephemeral=True)
            return

        preset['actions'] = []
        for selected in select.values:
            action = AutoModRuleActionType(int(selected))  # type: ignore
            match action:
                case AutoModRuleActionType.block_message | AutoModRuleActionType.block_member_interactions:
                    preset['actions'].append(AutoModRuleAction(
                        type=action,
                        custom_message='This message was prevented by a AutoMod rule created by Percy.'
                    ))
                case AutoModRuleActionType.send_alert_message:
                    view = AlertChannelSelect()
                    await interaction.response.send_message('Select a channel to send the alert messages...', view=view)
                    await view.wait()

                    if not view.channel:
                        continue

                    interaction = view.interaction

                    preset['actions'].append(AutoModRuleAction(
                        type=action,
                        channel_id=view.channel.id
                    ))
                case AutoModRuleActionType.timeout:
                    modal = BasicTextInputModal(placeholder='Enter the duration of the timeout in seconds...')
                    await interaction.response.send_modal(modal)
                    await modal.wait()
                    interaction = modal.interaction

                    try:
                        resolved = await TimeDelta.transform(interaction, modal.text_input.value)
                    except commands.BadArgument:
                        continue

                    preset['actions'].append(AutoModRuleAction(
                        type=action,
                        duration=resolved.dt
                    ))

        if preset['name'] in self.linked_rules:
            self.to_update.add(preset['name'])
        else:
            self.to_create.add(preset['name'])

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(preset), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, placeholder='Select exempt channels...', row=2, min_values=0, max_values=25,
        channel_types=[_t for _t in discord.ChannelType if 'voice' not in _t.name]
    )
    async def select_exempt_channels(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Select exempt channels."""
        preset = self.selected_preset
        if preset is None:
            await interaction.response.send_message('You must select a preset first.', ephemeral=True)
            return

        preset['exempt_channels'] = select.values  # type: ignore

        if preset['name'] in self.linked_rules:
            self.to_update.add(preset['name'])
        else:
            self.to_create.add(preset['name'])

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(preset), view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect, placeholder='Select exempt roles...', row=3, min_values=0, max_values=25
    )
    async def select_exempt_roles(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Select exempt roles."""
        preset = self.selected_preset
        if preset is None:
            await interaction.response.send_message('You must select a preset first.', ephemeral=True)
            return

        preset['exempt_roles'] = select.values  # type: ignore

        if preset['name'] in self.linked_rules:
            self.to_update.add(preset['name'])
        else:
            self.to_create.add(preset['name'])

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(preset), view=self)

    @discord.ui.button(label='Enable/Disable', row=4, style=discord.ButtonStyle.gray)
    async def enable_disable(self, interaction: discord.Interaction, _) -> None:
        """Enable the rule."""
        preset = self.selected_preset
        if preset is None:
            await interaction.response.send_message('You must select a preset first.', ephemeral=True)
            return

        if self.enable_disable.label == 'Enable':
            preset['enabled'] = True
        else:
            preset['enabled'] = False

        if preset['name'] in self.linked_rules:
            self.to_update.add(preset['name'])
        else:
            self.to_create.add(preset['name'])

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(preset), view=self)

    @discord.ui.button(label='Add Keyword', row=4, style=discord.ButtonStyle.primary)
    async def add_keyword(self, interaction: discord.Interaction, _) -> None:
        """Add a trigger."""
        preset = self.selected_preset
        if preset is None:
            await interaction.response.send_message('You must select a preset first.', ephemeral=True)
            return

        modal = BasicTextInputModal(
            placeholder='Enter keywords to filter messages. E.g. bad; ...',
            default='; '.join(preset['trigger'].keyword_filter)
        )
        await interaction.response.send_message('Please enter the trigger:', view=modal)
        await modal.wait()
        interaction = modal.interaction

        if not modal.text_input.value:
            await interaction.response.send_message('You must provide a trigger.', ephemeral=True)
            return

        keywords = [kw.strip() for kw in modal.text_input.value.split(';')]

        if any(len(keyword) > 60 for keyword in keywords):
            await interaction.response.send_message('The keywords must be less than 60 characters.', ephemeral=True)
            return

        if len(keywords) > 100:
            await interaction.response.send_message('You can only add up to 100 keywords.', ephemeral=True)
            return

        preset['trigger'].keyword_filter = keywords

        if preset['name'] in self.linked_rules:
            self.to_update.add(preset['name'])
        else:
            self.to_create.add(preset['name'])

        self.update_items()
        await interaction.response.edit_message(embed=AutoModPresets.to_embed(preset), view=self)

    @discord.ui.button(label='Save', style=discord.ButtonStyle.success, row=4)
    async def save(self, interaction: discord.Interaction, _) -> None:
        """Finish the setup."""
        for name in self.to_create:
            preset = discord.utils.find(lambda x: x['name'] == name, self.presets)
            rule = await interaction.client.http.create_auto_moderation_rule(
                guild_id=interaction.guild.id,
                reason=f'AutoMod Rule created by {interaction.user} (ID: {interaction.user.id}).',
                # payload
                name=preset['name'],
                event_type=preset['event_type'].value,
                trigger_type=preset['trigger'].type.value,
                trigger_metadata=preset['trigger'].to_metadata_dict() or None,
                actions=[a.to_dict() for a in preset['actions']],
                enabled=preset['enabled'],
                exempt_roles=[str(r.id) for r in preset['exempt_roles']] if preset['exempt_roles'] else None,
                exempt_channels=[str(c.id) for c in preset['exempt_channels']] if preset['exempt_channels'] else None
            )
            preset['id'] = rule['id']

        if self.to_create:
            config: GuildConfig = await interaction.client.db.get_guild_config(interaction.guild.id)
            await config.update(
                linked_automod_rules=config.linked_automod_rules | self.to_create)

        for name in self.to_update:
            preset = discord.utils.find(lambda x: x['name'] == name, self.presets)
            await interaction.client.http.edit_auto_moderation_rule(
                guild_id=interaction.guild.id,
                rule_id=preset['id'],
                reason=f'AutoMod Rule updated by {interaction.user} (ID: {interaction.user.id}).',
                # payload
                name=preset['name'],
                event_type=preset['event_type'].value,
                trigger_metadata=preset['trigger'].to_metadata_dict(),
                actions=[a.to_dict() for a in preset['actions']],
                enabled=preset['enabled'],
                exempt_roles=[str(r.id) for r in preset['exempt_roles']] if preset['exempt_roles'] else None,
                exempt_channels=[str(c.id) for c in preset['exempt_channels']] if preset['exempt_channels'] else None
            )

        await interaction.response.send_message(f'{Emojis.success} AutoMod rules have been successfully updated.', ephemeral=True)


class AutoMod(Cog):
    """A cog for automoderation features."""

    emoji = '<:automod:1322338624121077851>'

    @command(
        'automod',
        description='Modify/Create and view Percys\' AutoMod preset rules.',
        guild_only=True,
        hybrid=True,
        user_permissions=['manage_guild'],
        bot_permissions=['manage_guild']
    )
    async def automod(self, ctx: Context) -> None:
        """Show the automod presets."""
        embed = discord.Embed(
            title='AutoMod Rule Configuration - Information',
            description=(
                'You can choose from the preconfigured AutoMod rules below and also configure them to your liking.\n'
                'You can also add custom rules by clicking the "Add Trigger" and "Add Action" buttons.\n'
                'Once you are done, click the "Finish" button to save your changes.\n'
                '\n'
                'To switch between the presets, use the select menu below.\n'
                'By default, the rules are disabled. You can enable them by clicking the "Enable" button.\n'
                '\n'
                f'{Emojis.info} **Note that this currently only supports to modify the preset automod rules that the bot provides!**'
            ),
            colour=helpers.Colour.white()
        )
        embed.set_thumbnail(url=get_asset_url(ctx.guild))

        rules = await ctx.guild.fetch_automod_rules()
        config: GuildConfig = await ctx.db.get_guild_config(ctx.guild.id)
        linked_rules = config.linked_automod_rules

        presets = AutoModPresets.from_rules(
            list(filter(lambda rule: rule.creator_id == ctx.me.id, rules)), linked_rules)

        view = InteractiveAutoModRuleSetupView(ctx, presets=presets, linked_rules=linked_rules)
        view.message = await ctx.send(embed=embed, view=view)


async def setup(bot) -> None:
    await bot.add_cog(AutoMod(bot))
