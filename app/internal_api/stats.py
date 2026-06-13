"""InternalAPI stats endpoints."""
from __future__ import annotations

from aiohttp import web

from .models import InternalAPIHandlers


class StatsHandlers(InternalAPIHandlers):
    """Stats-related internal API handlers."""

    async def _get_guild_stats(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        command_summary = await self.bot.db.stats.get_command_summary(guild_id)
        command_usage = await self.bot.db.stats.get_command_usage(guild_id=guild_id, group_by='command', limit=10)

        online = sum(1 for m in guild.members if m.status.name != 'offline')
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots if guild.member_count else 0

        top_commands = [
            {'command': r['command'], 'uses': r['uses']}
            for r in command_usage
        ]

        return web.json_response({
            'member_count': guild.member_count,
            'online_count': online,
            'bot_count': bots,
            'human_count': humans,
            'channel_count': len(guild.channels),
            'role_count': len(guild.roles),
            'emoji_count': len(guild.emojis),
            'boost_count': guild.premium_subscription_count or 0,
            'boost_tier': guild.premium_tier,
            'total_commands': command_summary[0] if command_summary else 0,
            'top_commands': top_commands,
            'created_at': guild.created_at.isoformat(),
            'owner_id': str(guild.owner_id),
            'owner_name': guild.owner.display_name if guild.owner else None,
        })

    async def _get_bot_stats(self, request: web.Request) -> web.Response:
        total_commands = await self.bot.db.stats.count_all_commands()

        return web.json_response({
            'guild_count': len(self.bot.guilds),
            'user_count': sum(g.member_count or 0 for g in self.bot.guilds),
            'channel_count': sum(len(g.channels) for g in self.bot.guilds),
            'total_commands_used': total_commands,
            'cog_count': len(self.bot.cogs),
            'command_count': sum(1 for _ in self.bot.walk_commands()),
            'latency_ms': round(self.bot.latency * 1000, 1),
            'uptime_seconds': (self.bot.uptime.total_seconds() if hasattr(self.bot, 'uptime') else 0),
        })

    async def _get_bot_metrics(self, request: web.Request) -> web.Response:
        return web.json_response({
            'commands': self.bot.metrics.summary(),
            'queries': self.bot.db.query_tracker.summary(),
        })

    async def _get_feature_flags(self, request: web.Request) -> web.Response:
        return web.json_response(self.bot.feature_flags.status())

    async def _post_feature_flags(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = body.get('action')
        target = body.get('target')
        target_type = body.get('type', 'command')

        if not action or not target:
            raise web.HTTPBadRequest(text='Missing "action" or "target" field')

        if target_type == 'cog':
            if action == 'disable':
                self.bot.feature_flags.disable_cog(target)
            elif action == 'enable':
                self.bot.feature_flags.enable_cog(target)
            else:
                raise web.HTTPBadRequest(text=f'Unknown action: {action}')
        else:
            if action == 'disable':
                self.bot.feature_flags.disable_command(target)
            elif action == 'enable':
                self.bot.feature_flags.enable_command(target)
            else:
                raise web.HTTPBadRequest(text=f'Unknown action: {action}')

        return web.json_response(self.bot.feature_flags.status())

