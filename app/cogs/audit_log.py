from contextlib import suppress
from typing import Any

import discord

from app.core import Cog
from app.utils import get_asset_url

PossibleTarget = discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel | discord.StageChannel | discord.GroupChannel | \
    discord.ForumChannel | discord.Member | discord.Role | discord.Emoji | discord.PartialEmoji | discord.Invite | \
    discord.StageInstance | discord.Webhook | discord.Message | discord.User | discord.Guild | discord.Thread | \
    discord.ThreadMember | discord.Interaction


class AuditLog(Cog):
    """Configure the Server Audit Log sending to a channel.
    You can specify the Events you want to get notified about.

    Note: There are some events that are currently not supported by this bot.
    Please be patient until they are added."""

    emoji = '<:log_all:1322360000919769169>'

    @classmethod
    def to_title(cls, string: str) -> str:
        return string.replace('_', ' ').title()

    @classmethod
    def resolve_target(cls, target: PossibleTarget, *, raw: bool = False) -> str:
        if isinstance(target, str):
            return target

        is_user = isinstance(target, (discord.User, discord.Member))

        attr = getattr(target, 'mention', None) or getattr(target, 'name', None) or getattr(target, 'id', None)
        if attr is None:
            return '<Unknown />'
        elif isinstance(attr, int):
            return f'<Unknown {attr}>'
        else:
            if raw and is_user:
                return f'[{target}](https://discord.com/users/{target.id})'
            return attr

    @classmethod
    def get_permissions_changes(cls, entry: discord.AuditLogEntry, change_type: str) -> str:
        changes = []
        with suppress(AttributeError, TypeError):
            for b, a in zip(getattr(entry.before, change_type), getattr(entry.after, change_type)):
                if b[1] != a[1]:
                    changes.append(f'{cls.resolve_target(entry.extra)}: **{b[0]}** `{b[1]}` -> `{a[1]}`')
        return '\n'.join(changes)

    @classmethod
    def get_change_value(cls, entry: discord.AuditLogEntry, change_type: str) -> str | None:
        if change_type in ['overwrites', 'permissions', 'allow', 'deny']:
            return cls.get_permissions_changes(entry, change_type)
        elif change_type == 'roles':
            message = ['### Updated Roles']
            role_removals = set(entry.before.roles) - set(entry.after.roles)
            role_additions = set(entry.after.roles) - set(entry.before.roles)
            for role in role_removals | role_additions:
                sign = '-' if role in role_removals else '+'
                message.append(f'{sign} {role.name} ({role.id})')

            message = '\n'.join(message)
            return f'```diff\n{message}```'
        elif hasattr(entry.before, change_type) and hasattr(entry.after, change_type):
            return f'**{cls.to_title(change_type)}:** {getattr(entry.before, change_type)} -> {getattr(entry.after, change_type)}'
        return None

    @classmethod
    def get_flags(cls, config: Any, only_active: bool = True) -> dict:
        flags = config.audit_log_flags
        if not flags:
            return {}

        if only_active:
            return {k: v for k, v in flags.items() if v is True}
        else:
            return flags

    @classmethod
    def get_action_color(cls, action: discord.AuditLogAction) -> int:
        category = getattr(action, 'category', None)
        if category is None:
            return 0x2F3136
        elif category.value == 1:
            return 0x3BA55C
        elif category.value == 2:
            return 0xEC4245
        elif category.value == 3:
            return 0xFAA61A
        else:
            return 0x000000

    @Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        config = await self.bot.db.get_guild_config(entry.guild.id)

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

        target_type = self.to_title(entry.action.target_type)
        action_event_type = self.to_title(entry.action.name)  # type: ignore

        message = []
        for value in vars(entry.changes.before):
            changed = self.get_change_value(entry, value)
            if changed:
                message.append(changed)

        if not message:
            message.append('*No data provided.*')

        target = self.resolve_target(entry.target, raw=True)
        by = self.resolve_target(getattr(entry, 'user', 'N/A'))

        embed = discord.Embed(
            title=f'{emoji} {action_event_type}',
            description='## Changes\n\n' + '\n'.join(message),
            colour=color,
            timestamp=entry.created_at
        )
        embed.add_field(name='Performed by', value=by, inline=True)
        embed.add_field(name='Target', value=target, inline=True)
        embed.add_field(name='Reason', value=entry.reason, inline=False)
        embed.add_field(name='Category', value=f'{action_name} (Type: {target_type})', inline=False)
        embed.set_thumbnail(url=get_asset_url(entry.guild))
        embed.set_footer(text=f'Log: [{entry.id}]', icon_url=get_asset_url(entry.user))
        await config.audit_log_webhook.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(AuditLog(bot))


ACTION_EMOJIS = {
    discord.AuditLogAction.guild_update: '<:log_guild_update:1322354786514767995>',
    discord.AuditLogAction.channel_update: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.channel_create: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.channel_delete: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.overwrite_create: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.overwrite_update: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.overwrite_delete: '<:log_all:1322360000919769169>',
    discord.AuditLogAction.member_update: '<:log_member_update:1322354869843001345>',
    discord.AuditLogAction.member_role_update: '<:log_member_update:1322354869843001345>',
    discord.AuditLogAction.member_move: '<:log_member_update:1322354869843001345>',
    discord.AuditLogAction.member_disconnect: '<:log_member_update:1322354869843001345>',
    discord.AuditLogAction.bot_add: '<:log_member_plus:1322354862117359687>',
    discord.AuditLogAction.message_delete: '<:log_msg_minus:1322354877682286632>',
    discord.AuditLogAction.message_bulk_delete: '<:log_msg_minus:1322354877682286632>',
    discord.AuditLogAction.message_pin: '<:log_msg_update:1322354943348178944>',
    discord.AuditLogAction.message_unpin: '<:log_msg_update:1322354943348178944>',
    discord.AuditLogAction.kick: '<:log_member_minus:1322354852512137297>',
    discord.AuditLogAction.ban: '<:log_member_minus:1322354852512137297>',
    discord.AuditLogAction.unban: '<:log_member_plus:1322354862117359687>',
    discord.AuditLogAction.stage_instance_create: '<:log_stage_plus:1322360612701081661>',
    discord.AuditLogAction.stage_instance_update: '<:log_stage_update:1322354994309107784>',
    discord.AuditLogAction.stage_instance_delete: '<:log_stage_minus:1322354983781273600>',
    discord.AuditLogAction.integration_create: '<:log_integration_plus:1322354818072973332>',
    discord.AuditLogAction.integration_update: '<:log_integration_update:1322354805301182647>',
    discord.AuditLogAction.integration_delete: '<:log_integration_minus:1322354793724907550>',
    discord.AuditLogAction.role_create: '<:log_role_plus:1322354962834919465>',
    discord.AuditLogAction.role_update: '<:log_role_update:1322354973148975164>',
    discord.AuditLogAction.role_delete: '<:log_role_minus:1322354896116387860>',
    discord.AuditLogAction.invite_create: '<:log_invite_plus:1322354835034738781>',
    discord.AuditLogAction.invite_delete: '<:log_invite_minus:1322354825979236433>',
    discord.AuditLogAction.webhook_create: '<:log_integration_plus:1322354818072973332>',
    discord.AuditLogAction.webhook_update: '<:log_integration_update:1322354805301182647>',
    discord.AuditLogAction.webhook_delete: '<:log_integration_minus:1322354793724907550>',
    discord.AuditLogAction.emoji_create: '<:log_emoji_plus:1322354769959977021>',
    discord.AuditLogAction.emoji_update: '<:log_emoji_update:1322354777790615614>',
    discord.AuditLogAction.emoji_delete: '<:log_emoji_minus:1322354762171023453>',
    discord.AuditLogAction.sticker_create: '<:log_sticker_plus:1322355019307159695>',
    discord.AuditLogAction.sticker_update: '<:log_sticker_update:1322355025208672266>',
    discord.AuditLogAction.sticker_delete: '<:log_sticker_minus:1322355005700968530>',
    discord.AuditLogAction.thread_create: '<:log_msg_plus:1322354885684891728>',
    discord.AuditLogAction.thread_update: '<:log_msg_update:1322354943348178944>',
    discord.AuditLogAction.thread_delete: '<:log_msg_minus:1322354877682286632>',
    discord.AuditLogAction.automod_rule_create: '<:log_automod_plus:1322354747579174952>',
    discord.AuditLogAction.automod_rule_update: '<:log_automod_update:1322354754365427803>',
    discord.AuditLogAction.automod_rule_delete: '<:log_automod_minus:1322354740058919116>'
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
