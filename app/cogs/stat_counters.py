from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Literal

import discord
from discord.ext import tasks

from app.core import Accent, Bot, Cog, Context, NoticeView, describe, group
from app.core.models import PermissionTemplate
from app.utils import truncate

if TYPE_CHECKING:
    import asyncpg

StatKind = Literal['members', 'humans', 'bots', 'online', 'boosts', 'roles', 'channels']

#: Friendly default label substituted for ``{name}`` in a counter template.
KIND_LABELS: dict[str, str] = {
    'members': 'Members',
    'humans': 'Humans',
    'bots': 'Bots',
    'online': 'Online',
    'boosts': 'Boosts',
    'roles': 'Roles',
    'channels': 'Channels',
}
STAT_KINDS: tuple[StatKind, ...] = ('members', 'humans', 'bots', 'online', 'boosts', 'roles', 'channels')


def compute_stat(guild: discord.Guild, kind: str) -> int:
    """Compute the current value of a statistic for a guild."""
    match kind:
        case 'members':
            return guild.member_count or len(guild.members)
        case 'humans':
            return sum(1 for m in guild.members if not m.bot)
        case 'bots':
            return sum(1 for m in guild.members if m.bot)
        case 'online':
            return sum(1 for m in guild.members if m.status is not discord.Status.offline)
        case 'boosts':
            return guild.premium_subscription_count or 0
        case 'roles':
            return len(guild.roles)
        case 'channels':
            return len(guild.channels)
        case _:
            return 0


def render_name(template: str, kind: str, count: int) -> str:
    """Render a counter channel name, clamped to Discord's 100-char limit."""
    label = KIND_LABELS.get(kind, kind.title())
    return truncate(template.replace('{name}', label).replace('{count}', f'{count:,}'), 100)


class StatCounters(Cog):
    """Voice channels whose names display a live server statistic."""

    emoji = '\N{BAR CHART}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.refresh_counters.start()

    async def cog_unload(self) -> None:
        self.refresh_counters.cancel()

    @tasks.loop(minutes=10)
    async def refresh_counters(self) -> None:
        """Periodically rename each bound channel to its current value.

        Runs every 10 minutes because Discord rate-limits channel renames hard
        (≈2 per 10 minutes per channel); a name is only edited when it actually changes.
        """
        records = await self.bot.db.stat_counters.get_every()
        for record in records:
            await self._apply(record)

    @refresh_counters.before_loop
    async def _before_refresh(self) -> None:
        await self.bot.wait_until_ready()

    async def _apply(self, record: asyncpg.Record) -> None:
        guild = self.bot.get_guild(record['guild_id'])
        if guild is None:
            return
        channel = guild.get_channel(record['channel_id'])
        if not isinstance(channel, discord.VoiceChannel):
            return

        desired = render_name(record['template'], record['kind'], compute_stat(guild, record['kind']))
        if channel.name == desired:
            return
        with contextlib.suppress(discord.HTTPException):
            await channel.edit(name=desired, reason='Stat counter refresh')

    @Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        # Drop the binding if the channel is removed manually.
        await self.bot.db.stat_counters.delete_by_channel(channel.id)

    # -- commands ---------------------------------------------------------

    @group(
        'statcounter',
        alias='counter',
        fallback='list',
        description='Manage live server-statistic voice channels.',
        guild_only=True,
        hybrid=True,
    )
    async def statcounter(self, ctx: Context) -> None:
        """List the server's stat counters."""
        assert ctx.guild is not None
        records = await self.bot.db.stat_counters.get_all(ctx.guild.id)
        if not records:
            await ctx.send_info('No stat counters yet. Create one with `statcounter create`.')
            return

        container = discord.ui.Container(accent_colour=Accent.info.colour)
        container.add_item(discord.ui.TextDisplay(f'## Stat Counters · {ctx.guild.name}'))
        container.add_item(discord.ui.Separator())
        for record in records:
            value = compute_stat(ctx.guild, record['kind'])
            container.add_item(
                discord.ui.TextDisplay(
                    f'<#{record["channel_id"]}> · `{record["kind"]}` → **{value:,}**\n'
                    f'-# template: `{record["template"]}`'
                )
            )
        await ctx.send(view=NoticeView(container))

    @statcounter.command(
        'create',
        description='Create a voice channel that displays a live statistic.',
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
        bot_permissions=['manage_channels'],
    )
    @describe(
        kind='Which statistic to display.',
        template='Name template — use {name} and {count}. Default: "{name}: {count}".',
    )
    async def statcounter_create(
        self, ctx: Context, kind: StatKind, *, template: str = '{name}: {count}'
    ) -> None:
        """Create a locked voice channel that shows a live server statistic."""
        assert ctx.guild is not None
        if kind not in STAT_KINDS:
            await ctx.send_error(f'`kind` must be one of: {", ".join(STAT_KINDS)}.')
            return
        if '{count}' not in template:
            await ctx.send_error('Your template must include the `{count}` placeholder.')
            return

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(connect=False),
        }
        name = render_name(template, kind, compute_stat(ctx.guild, kind))
        try:
            channel = await ctx.guild.create_voice_channel(
                name=name, overwrites=overwrites, reason=f'Stat counter ({kind})'
            )
        except discord.HTTPException:
            await ctx.send_error('Could not create the channel — check my permissions and try again.')
            return

        await self.bot.db.stat_counters.create(ctx.guild.id, channel.id, kind, template)
        await ctx.send_success(f'Created stat counter {channel.mention} for **{kind}**.')

    @statcounter.command(
        'remove',
        aliases=['delete', 'rm'],
        description='Remove a stat counter (and its channel).',
        guild_only=True,
        user_permissions=PermissionTemplate.admin,
    )
    @describe(channel='The stat-counter voice channel to remove.')
    async def statcounter_remove(self, ctx: Context, channel: discord.VoiceChannel) -> None:
        """Remove a stat counter and delete its voice channel."""
        assert ctx.guild is not None
        record = await self.bot.db.stat_counters.delete_by_channel(channel.id)
        if record is None:
            await ctx.send_error(f'{channel.mention} is not a stat counter.')
            return
        with contextlib.suppress(discord.HTTPException):
            await channel.delete(reason='Stat counter removed')
        await ctx.send_success('Removed the stat counter.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(StatCounters(bot))
