"""InternalAPI stats endpoints."""
from __future__ import annotations

import datetime
from pathlib import Path

from aiohttp import web

from config import get_full_version

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
            'version': get_full_version(),
            'guild_count': len(self.bot.guilds),
            'user_count': sum(g.member_count or 0 for g in self.bot.guilds),
            'channel_count': sum(len(g.channels) for g in self.bot.guilds),
            'total_commands_used': total_commands,
            'cog_count': len(self.bot.cogs),
            'command_count': sum(1 for _ in self.bot.walk_commands()),
            'latency_ms': round(self.bot.latency * 1000, 1),
            'uptime_seconds': (self.bot.uptime.total_seconds() if hasattr(self.bot, 'uptime') else 0),
        })

    async def _get_guild_overview(self, request: web.Request) -> web.Response:
        """Composite endpoint: guild config + guild stats + bot stats in one round-trip."""
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        guild_config = await self.bot.db.get_guild_config(guild_id)
        command_summary = await self.bot.db.stats.get_command_summary(guild_id)
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
        recent_cases = len(await self.bot.db.moderation.get_recent_cases(guild_id, since=since))

        online = sum(1 for m in guild.members if m.status.name != 'offline')
        bots = sum(1 for m in guild.members if m.bot)

        return web.json_response({
            'guild': {
                'id': guild_config.id,
                'name': guild.name,
                'icon_url': guild.icon.url if guild.icon else None,
                'member_count': guild.member_count,
            },
            'stats': {
                'online_count': online,
                'bot_count': bots,
                'channel_count': len(guild.channels),
                'role_count': len(guild.roles),
                'emoji_count': len(guild.emojis),
                'boost_count': guild.premium_subscription_count or 0,
                'boost_tier': guild.premium_tier,
                'total_commands': command_summary[0] if command_summary else 0,
                'recent_cases': recent_cases,
            },
            'bot': {
                'version': get_full_version(),
                'guild_count': len(self.bot.guilds),
                'user_count': sum(g.member_count or 0 for g in self.bot.guilds),
                'command_count': sum(1 for _ in self.bot.walk_commands()),
                'latency_ms': round(self.bot.latency * 1000, 1),
            },
            'features': {
                'leveling': guild_config.flags.leveling if hasattr(guild_config.flags, 'leveling') else True,
                'economy': guild_config.flags.economy if hasattr(guild_config.flags, 'economy') else True,
                'music': guild_config.use_music_panel,
                'sentinel': guild_config.flags.sentinel,
                'audit_log': guild_config.flags.audit_log,
            },
        })

    async def _get_public_commands(self, request: web.Request) -> web.Response:
        """All bot commands grouped by cog, without guild-specific disable state."""
        commands = []
        for cmd in self.bot.walk_commands():
            if cmd.hidden:
                continue
            if cmd.cog and getattr(cmd.cog, "__hidden__", False):
                continue
            if cmd.cog and cmd.cog.qualified_name == "Jishaku":
                continue

            cog_name = cmd.cog.qualified_name if cmd.cog else 'Uncategorized'
            commands.append({
                'name': cmd.qualified_name,
                'cog': cog_name,
                'description': cmd.short_doc or '',
                'signature': cmd.signature or None,
            })
        return web.json_response({'commands': commands})

    async def _get_changelog(self, request: web.Request) -> web.Response:
        """Git log grouped by version tags. Falls back to recent commits if pygit2 is unavailable."""
        limit = min(int(request.query.get('limit', '20')), 50)

        try:
            import pygit2

            repo = pygit2.Repository(str(Path(__file__).parents[2]))
            entries: list[dict] = []

            # Collect version tags sorted by commit time (newest first)
            tag_commits: list[tuple[str, pygit2.Commit]] = []
            for ref_name in repo.references:
                if not ref_name.startswith('refs/tags/v'):
                    continue
                tag_name = ref_name.removeprefix('refs/tags/')
                obj = repo.references[ref_name].resolve().peel(pygit2.Commit)
                tag_commits.append((tag_name, obj))

            tag_commits.sort(key=lambda t: t[1].commit_time, reverse=True)

            if tag_commits:
                # Group commits between consecutive tags
                for i, (tag_name, tag_commit) in enumerate(tag_commits[:limit]):
                    if i + 1 < len(tag_commits):
                        parent_oid = tag_commits[i + 1][1].id
                    else:
                        parent_oid = None

                    changes: list[str] = []
                    walker = repo.walk(tag_commit.id, pygit2.GIT_SORT_TOPOLOGICAL)
                    for commit in walker:
                        if parent_oid and commit.id == parent_oid:
                            break
                        msg = commit.message.split('\n', 1)[0].strip()
                        if msg:
                            changes.append(msg)

                    dt = datetime.datetime.fromtimestamp(tag_commit.commit_time, tz=datetime.timezone.utc)
                    entries.append({
                        'version': tag_name.removeprefix('v'),
                        'date': dt.strftime('%Y-%m-%d'),
                        'changes': changes[:30],
                    })
            else:
                # No tags: group recent commits by date
                walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL)
                current_date = None
                current_changes: list[str] = []
                count = 0

                for commit in walker:
                    if count >= limit * 5:
                        break
                    dt = datetime.datetime.fromtimestamp(commit.commit_time, tz=datetime.timezone.utc)
                    date_str = dt.strftime('%Y-%m-%d')
                    msg = commit.message.split('\n', 1)[0].strip()
                    if not msg:
                        continue

                    if date_str != current_date:
                        if current_date and current_changes:
                            entries.append({
                                'version': current_date,
                                'date': current_date,
                                'changes': current_changes,
                            })
                            if len(entries) >= limit:
                                break
                        current_date = date_str
                        current_changes = []
                    current_changes.append(msg)
                    count += 1

                if current_date and current_changes and len(entries) < limit:
                    entries.append({
                        'version': current_date,
                        'date': current_date,
                        'changes': current_changes,
                    })

            return web.json_response({'entries': entries, 'current_version': get_full_version()})

        except Exception:
            return web.json_response({'entries': [], 'current_version': get_full_version()})

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

