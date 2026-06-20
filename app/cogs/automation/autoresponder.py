from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import discord

from app.cogs.automation.engine import MATCH_TYPES, MatchType, is_valid_regex, matches
from app.core import Accent, Cog, Context, NoticeView, describe, group
from app.core.models import PermissionTemplate
from app.utils import cache, truncate
from config import Emojis

if TYPE_CHECKING:
    import asyncpg

#: Mentions an autoresponder is allowed to ping (never @everyone or roles).
_SAFE_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)

#: Cap per guild to keep the on-message scan cheap and prevent abuse.
MAX_RESPONDERS = 100


def render_response(template: str, message: discord.Message) -> str:
    """Fill the placeholder tokens an autoresponder response may contain."""
    guild = message.guild
    return (
        template.replace('{user}', message.author.mention)
        .replace('{user.name}', message.author.display_name)
        .replace('{server}', guild.name if guild else '')
        .replace('{channel}', getattr(message.channel, 'mention', ''))
        .replace('{count}', str(guild.member_count) if guild and guild.member_count else '')
    )


class AutoResponderMixin:
    """Automatic replies that fire when a message matches a configured trigger phrase."""

    @cache.cache()
    async def get_responders(self, guild_id: int) -> list[asyncpg.Record]:
        """|coro| @cached — the enabled autoresponders for a guild (on-message hot path)."""
        return await self.bot.db.autoresponders.get_enabled(guild_id)

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content:
            return
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        responders = await self.get_responders(message.guild.id)
        if not responders:
            return

        for record in responders:
            if matches(
                message.content,
                record['trigger'],
                record['match_type'],
                ignore_case=record['ignore_case'],
            ):
                with contextlib.suppress(discord.HTTPException):
                    await message.channel.send(
                        render_response(record['response'], message),
                        allowed_mentions=_SAFE_MENTIONS,
                        reference=message.to_reference(fail_if_not_exists=False),
                    )
                await self.bot.db.autoresponders.increment_uses(record['id'])
                return  # first match wins

    # -- commands ---------------------------------------------------------

    @group(
        'autoresponder',
        alias='ar',
        fallback='list',
        description='Manage automatic trigger-phrase replies.',
        guild_only=True,
        hybrid=True,
    )
    async def autoresponder(self, ctx: Context) -> None:
        """List the server's autoresponders."""
        assert ctx.guild is not None
        records = await self.bot.db.autoresponders.get_all(ctx.guild.id)
        if not records:
            await ctx.send_info('No autoresponders yet. Add one with `autoresponder add`.')
            return

        container = discord.ui.Container(accent_colour=Accent.info.colour)
        container.add_item(discord.ui.TextDisplay(f'## Autoresponders · {ctx.guild.name}'))
        container.add_item(discord.ui.Separator())
        for record in records:
            state = Emojis.success if record['enabled'] else Emojis.error
            container.add_item(
                discord.ui.TextDisplay(
                    f'{state} **{truncate(record["trigger"], 80)}** '
                    f'· `{record["match_type"]}` · {record["uses"]} uses\n'
                    f'-# → {truncate(record["response"], 120)}'
                )
            )
        await ctx.send(view=NoticeView(container))

    @autoresponder.command(
        'add',
        description='Add an autoresponder.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(
        trigger='The phrase to watch for (quote it if it has spaces).',
        response='The reply to send. Placeholders: {user} {user.name} {server} {channel} {count}.',
        match_type='How the trigger is matched against messages.',
    )
    async def autoresponder_add(
        self,
        ctx: Context,
        trigger: str,
        response: str,
        match_type: MatchType = 'contains',
    ) -> None:
        """Add an autoresponder that replies when a message matches ``trigger``."""
        assert ctx.guild is not None
        if match_type not in MATCH_TYPES:
            await ctx.send_error(f'`match_type` must be one of: {", ".join(MATCH_TYPES)}.')
            return
        if match_type == 'regex' and not is_valid_regex(trigger):
            await ctx.send_error('That trigger is not a valid regular expression.')
            return

        existing = await self.bot.db.autoresponders.get_all(ctx.guild.id)
        if len(existing) >= MAX_RESPONDERS:
            await ctx.send_error(f'This server already has the maximum of **{MAX_RESPONDERS}** autoresponders.')
            return

        record = await self.bot.db.autoresponders.create(
            ctx.guild.id, trigger, response,
            match_type=match_type, ignore_case=True, created_by=ctx.author.id,
        )
        if record is None:
            await ctx.send_error(f'An autoresponder for **{truncate(trigger, 80)}** already exists.')
            return

        self.get_responders.invalidate(ctx.guild.id)
        await ctx.send_success(f'Added a `{match_type}` autoresponder for **{truncate(trigger, 80)}**.')

    @autoresponder.command(
        'remove',
        aliases=['delete', 'rm'],
        description='Remove an autoresponder.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(trigger='The trigger of the autoresponder to remove.')
    async def autoresponder_remove(self, ctx: Context, *, trigger: str) -> None:
        """Remove an autoresponder by its trigger."""
        assert ctx.guild is not None
        record = await self.bot.db.autoresponders.delete(ctx.guild.id, trigger)
        if record is None:
            await ctx.send_error(f'No autoresponder for **{truncate(trigger, 80)}** exists.')
            return
        self.get_responders.invalidate(ctx.guild.id)
        await ctx.send_success(f'Removed the autoresponder for **{truncate(record["trigger"], 80)}**.')

    @autoresponder.command(
        'toggle',
        description='Enable or disable an autoresponder.',
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(trigger='The trigger of the autoresponder to toggle.', enabled='Whether it should be active.')
    async def autoresponder_toggle(self, ctx: Context, enabled: bool, *, trigger: str) -> None:
        """Enable or disable an autoresponder without deleting it."""
        assert ctx.guild is not None
        record = await self.bot.db.autoresponders.set_enabled(ctx.guild.id, trigger, enabled)
        if record is None:
            await ctx.send_error(f'No autoresponder for **{truncate(trigger, 80)}** exists.')
            return
        self.get_responders.invalidate(ctx.guild.id)
        fmt = 'enabled' if enabled else 'disabled'
        await ctx.send_success(f'Autoresponder for **{truncate(record["trigger"], 80)}** {fmt}.')
