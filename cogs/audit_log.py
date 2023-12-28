from contextlib import suppress
from typing import Optional

import discord
from discord.ext import commands

from bot import Percy
from .utils.constants import PossibleTarget
from .utils.converters import get_asset_url


class AuditLog(commands.Cog):
    """Configure the Server Audit Log sending to a channel.
    You can specify the Events you want to get notified about.

    Note: There are some events that are currently not supported by this bot.
    Please be patient until they are added."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='log_all', id=1080194735932711042)

    @staticmethod
    def resolve_target(target: PossibleTarget) -> str:
        attr = getattr(target, 'mention', None) or getattr(target, 'name', None) or getattr(target, 'id', None)
        if attr is None:
            return '<Not Found: />'
        elif isinstance(attr, int):
            return f'<Not Found: {attr}>'
        else:
            return attr

    def get_permissions_changes(self, entry: discord.AuditLogEntry, change_type: str):
        changes = []
        with suppress(AttributeError, TypeError):
            for b, a in zip(getattr(entry.before, change_type), getattr(entry.after, change_type)):
                if b[1] != a[1]:
                    changes.append(f'{self.resolve_target(entry.extra)}: **{b[0]}** `{b[1]}` -> `{a[1]}`')
        return '\n'.join(changes)

    def get_change_value(self, entry: discord.AuditLogEntry, change_type: str) -> Optional[str]:
        if change_type in ['overwrites', 'permissions', 'allow', 'deny']:
            return self.get_permissions_changes(entry, change_type)
        elif change_type == 'roles':
            message = ['### Updated Roles']
            role_removals = set(entry.before.roles) - set(entry.after.roles)
            role_additions = set(entry.after.roles) - set(entry.before.roles)
            for role in role_removals | role_additions:  # type: discord.Role
                sign = '-' if role in role_removals else '+'
                message.append(f'{sign} {role.name} ({role.id})')

            message = '\n'.join(message)
            return f'```diff\n{message}```'
        elif hasattr(entry.before, change_type) and hasattr(entry.after, change_type):
            return f'**{change_type.replace('_', ' ').title()}:** {getattr(entry.before, change_type)} -> {getattr(entry.after, change_type)}'
        return None

    @staticmethod
    def get_flags(config, only_active: bool = True) -> dict:
        flags = config.audit_log_flags
        if not flags:
            return {}

        if only_active:
            return {k: v for k, v in flags.items() if v is True}
        else:
            return flags

    @staticmethod
    def get_action_color(action: discord.AuditLogAction) -> int:
        category = getattr(action, 'category', None)
        if category is None:
            return 0x2F3136
        elif category.value == 1:
            return 0x3BA55C
        elif category.value == 2:
            return 0xEC4245
        elif category.value == 3:
            return 0xFAA61A

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        config = await self.bot.moderation.get_guild_config(entry.guild.id)

        if not config:
            return

        if not config.flags.audit_log:
            return

        flags = self.get_flags(config)
        if not flags:
            return

        action_name = ACTION_NAMES.get(entry.action)
        if action_name not in flags:
            return

        emoji = ACTION_EMOJIS.get(entry.action)
        color = self.get_action_color(entry.action)

        target_type = entry.action.target_type.title()
        action_event_type = entry.action.name.replace('_', ' ').title()  # noqa

        message = []
        for value in vars(entry.changes.before):
            changed = self.get_change_value(entry, value)
            if changed:
                message.append(changed)

        if not message:
            message.append('*Nothing Mentionable*')

        target = self.resolve_target(entry.target)
        by = getattr(entry, 'user', None) or 'N/A'

        embed = discord.Embed(
            title=f'{emoji} {action_event_type}',
            description='## Changes\n\n' + '\n'.join(message),
            colour=color
        )

        embed.add_field(name='Performed by', value=by, inline=True)
        embed.add_field(name='Target', value=target, inline=True)
        embed.add_field(name='Reason', value=entry.reason, inline=False)
        embed.add_field(name='Category', value=f'{action_name} (Type: {target_type})', inline=False)

        embed.set_thumbnail(url=get_asset_url(entry.guild))
        embed.set_footer(text=f'Log: [{entry.id}]', icon_url=get_asset_url(entry.user))
        embed.timestamp = entry.created_at

        if config.requires_migration:
            ch = f'<#{config.audit_log_channel_id}>'
            broadcast = (
                f'{ch}\n\n\N{WARNING SIGN}\ufe0f '
                f'This server requires migration for this feature to continue working.\n'
                f'Run "`/moderation disable Audit Logging`" followed by "`/moderation auditlog config {ch}`" '
                f'to ensure this feature continues working!'
            )
            return await entry.guild.system_channel.send(broadcast)

        webhook = config.audit_log_webhook
        await webhook.send(embed=embed)


async def setup(bot: Percy):
    await bot.add_cog(AuditLog(bot))


ACTION_EMOJIS = {
    discord.AuditLogAction.guild_update: '<:log_guild_update:1080533975153508352>',
    discord.AuditLogAction.channel_update: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.channel_create: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.channel_delete: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.overwrite_create: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.overwrite_update: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.overwrite_delete: '<:log_all:1080194735932711042>',
    discord.AuditLogAction.member_update: '<:log_member_update:1080194421229899829>',
    discord.AuditLogAction.member_role_update: '<:log_member_update:1080194421229899829>',
    discord.AuditLogAction.member_move: '<:log_member_update:1080194421229899829>',
    discord.AuditLogAction.member_disconnect: '<:log_member_update:1080194421229899829>',
    discord.AuditLogAction.bot_add: '<:log_member_plus:1112472154181750865>',
    discord.AuditLogAction.message_delete: '<:log_msg_minus:1112333427249795123>',
    discord.AuditLogAction.message_bulk_delete: '<:log_msg_minus:1112333427249795123>',
    discord.AuditLogAction.message_pin: '<:log_msg_update:1112333368332402699>',
    discord.AuditLogAction.message_unpin: '<:log_msg_update:1112333368332402699>',
    discord.AuditLogAction.kick: '<:log_member_minus:1112472097323745300>',
    discord.AuditLogAction.ban: '<:log_member_minus:1112472097323745300>',
    discord.AuditLogAction.unban: '<:log_member_plus:1112472154181750865>',
    discord.AuditLogAction.stage_instance_create: '<:log_stage_instance_plus:1112333263348965497>',
    discord.AuditLogAction.stage_instance_update: '<:log_stage_instance_update:1112332826369589319>',
    discord.AuditLogAction.stage_instance_delete: '<:log_stage_instance_minus:1112332874939637810>',
    discord.AuditLogAction.integration_create: '<:log_integration_plus:1080195469503905793>',
    discord.AuditLogAction.integration_update: '<:log_integration_update:1080194889536516207>',
    discord.AuditLogAction.integration_delete: '<:log_integration_minus:1080530500806000640>',
    discord.AuditLogAction.role_create: '<:log_role_plus:1080529743591518228>',
    discord.AuditLogAction.role_update: '<:log_role_update:1080529964169965608>',
    discord.AuditLogAction.role_delete: '<:log_role_minus:1080529853851369534>',
    discord.AuditLogAction.invite_create: '<:log_invite_plus:1080194590088368248>',
    discord.AuditLogAction.invite_delete: '<:log_invite_minus:1080530325760905346>',
    discord.AuditLogAction.webhook_create: '<:log_integration_plus:1080195469503905793>',
    discord.AuditLogAction.webhook_update: '<:log_integration_update:1080194889536516207>',
    discord.AuditLogAction.webhook_delete: '<:log_integration_minus:1080530500806000640>',
    discord.AuditLogAction.emoji_create: '<:log_emoji_plus:1080530759087042610>',
    discord.AuditLogAction.emoji_update: '<:log_emoji_update:1080194953453518909>',
    discord.AuditLogAction.emoji_delete: '<:log_emoji_minus:1080195415602888786>',
    discord.AuditLogAction.sticker_create: '<:log_sticker_plus:1080530914024620084>',
    discord.AuditLogAction.sticker_update: '<:log_sticker_update:1080531003250065478>',
    discord.AuditLogAction.sticker_delete: '<:log_sticker_minus:1080530961776775229>',
    discord.AuditLogAction.thread_create: '<:log_thread_plus:1080531262424502353>',
    discord.AuditLogAction.thread_update: '<:log_thread_update:1080531355978449007>',
    discord.AuditLogAction.thread_delete: '<:log_thread_minus:1080531308947718164>',
    discord.AuditLogAction.automod_rule_create: '<:log_automod_plus:1080194794795585626>',
    discord.AuditLogAction.automod_rule_update: '<:log_automod_update:1080194841989890048>',
    discord.AuditLogAction.automod_rule_delete: '<:log_automod_minus:1080194682442743858>'
}

ACTION_NAMES = {
    discord.AuditLogAction.guild_update: 'Server Updates',
    discord.AuditLogAction.channel_update: 'Channel Logs',
    discord.AuditLogAction.channel_create: 'Channel Logs',
    discord.AuditLogAction.channel_delete: 'Channel Logs',
    discord.AuditLogAction.overwrite_create: 'Overwrite Logs',
    discord.AuditLogAction.overwrite_update: 'Overwrite Logs',
    discord.AuditLogAction.overwrite_delete: 'Overwrite Logs',
    discord.AuditLogAction.member_update: 'Member Logs',
    discord.AuditLogAction.member_role_update: 'Member Logs',
    discord.AuditLogAction.member_move: 'Member Logs',
    discord.AuditLogAction.member_disconnect: 'Member Logs',
    discord.AuditLogAction.member_prune: 'Member Logs',
    discord.AuditLogAction.kick: 'Member Management',
    discord.AuditLogAction.ban: 'Member Management',
    discord.AuditLogAction.unban: 'Member Management',
    discord.AuditLogAction.bot_add: 'Bot Logs',
    discord.AuditLogAction.message_delete: 'Message Logs',
    discord.AuditLogAction.message_bulk_delete: 'Message Logs',
    discord.AuditLogAction.message_pin: 'Message Logs',
    discord.AuditLogAction.message_unpin: 'Message Logs',
    discord.AuditLogAction.integration_create: 'Integration Logs',
    discord.AuditLogAction.integration_update: 'Integration Logs',
    discord.AuditLogAction.integration_delete: 'Integration Logs',
    discord.AuditLogAction.stage_instance_create: 'Stage Logs',
    discord.AuditLogAction.stage_instance_update: 'Stage Logs',
    discord.AuditLogAction.stage_instance_delete: 'Stage Logs',
    discord.AuditLogAction.role_create: 'Role Logs',
    discord.AuditLogAction.role_update: 'Role Logs',
    discord.AuditLogAction.role_delete: 'Role Logs',
    discord.AuditLogAction.invite_create: 'Invite Logs',
    discord.AuditLogAction.invite_delete: 'Invite Logs',
    discord.AuditLogAction.webhook_create: 'Webhook Logs',
    discord.AuditLogAction.webhook_update: 'Webhook Logs',
    discord.AuditLogAction.webhook_delete: 'Webhook Logs',
    discord.AuditLogAction.emoji_create: 'Emoji Logs',
    discord.AuditLogAction.emoji_update: 'Emoji Logs',
    discord.AuditLogAction.emoji_delete: 'Emoji Logs',
    discord.AuditLogAction.sticker_create: 'Sticker Logs',
    discord.AuditLogAction.sticker_update: 'Sticker Logs',
    discord.AuditLogAction.sticker_delete: 'Sticker Logs',
    discord.AuditLogAction.thread_create: 'Thread Logs',
    discord.AuditLogAction.thread_update: 'Thread Logs',
    discord.AuditLogAction.thread_delete: 'Thread Logs',
    discord.AuditLogAction.automod_rule_create: 'Automod Logs',
    discord.AuditLogAction.automod_rule_update: 'Automod Logs',
    discord.AuditLogAction.automod_rule_delete: 'Automod Logs'
}
