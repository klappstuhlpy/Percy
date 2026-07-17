"""Internal API stats endpoints: guild stats, bot stats, changelog, metrics, feature flags."""
from __future__ import annotations

import datetime
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from config import get_full_version

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(tags=["Stats"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FeatureFlagBody(BaseModel):
    action: str
    target: str
    type: str = 'command'


# ---------------------------------------------------------------------------
# Guild-scoped endpoints
# ---------------------------------------------------------------------------


@router.get("/guilds/{guild_id}/stats")
async def get_guild_stats(bot: BotDep, guild: GuildDep) -> dict:
    """Member counts, channel/role/emoji counts, boosts, top commands."""
    command_summary = await bot.db.stats.get_command_summary(guild.id)
    command_usage = await bot.db.stats.get_command_usage(guild_id=guild.id, group_by='command', limit=10)

    online = sum(1 for m in guild.members if m.status.name != 'offline')
    bots = sum(1 for m in guild.members if m.bot)
    humans = guild.member_count - bots if guild.member_count else 0

    top_commands = [
        {'command': r['command'], 'uses': r['uses']}
        for r in command_usage
    ]

    return {
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
    }


@router.get("/guilds/{guild_id}/overview")
async def get_guild_overview(bot: BotDep, guild: GuildDep) -> dict:
    """Composite endpoint: guild config + guild stats + bot stats in one round-trip."""
    guild_config = await bot.db.get_guild_config(guild.id)
    command_summary = await bot.db.stats.get_command_summary(guild.id)
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
    recent_cases = len(await bot.db.moderation.get_recent_cases(guild.id, since=since))

    online = sum(1 for m in guild.members if m.status.name != 'offline')
    bots = sum(1 for m in guild.members if m.bot)

    return {
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
            'guild_count': len(bot.guilds),
            'user_count': sum(g.member_count or 0 for g in bot.guilds),
            'command_count': sum(1 for _ in bot.walk_commands()),
            'latency_ms': round(bot.latency * 1000, 1),
        },
        'features': {
            'leveling': guild_config.flags.leveling if hasattr(guild_config.flags, 'leveling') else True,
            'economy': guild_config.flags.economy if hasattr(guild_config.flags, 'economy') else True,
            'music': guild_config.use_music_panel,
            'sentinel': guild_config.flags.sentinel,
            'audit_log': guild_config.flags.audit_log,
        },
    }


@router.get("/guilds/{guild_id}/games")
async def get_guild_games(bot: BotDep, guild: GuildDep) -> dict:
    """Guild-wide game statistics: per-game totals and the top players by wins."""
    # Deferred import: pulling a cog module in at import time would drag discord
    # extension setup into the API module graph; the enum itself is pure.
    from app.cogs.games.models import Game

    overview_rows = await bot.db.game_stats.get_guild_overview(guild.id)
    top_rows = await bot.db.game_stats.get_leaderboard(guild.id, metric='won', limit=10)

    games = []
    for row in overview_rows:
        try:
            game = Game(row['game'])
        except ValueError:  # a game removed from the catalogue; keep the raw key
            label, icon = row['game'].title(), None
        else:
            label = game.label
            # Custom Discord emojis (`<:name:id>`) don't render on the web.
            icon = game.icon if not game.icon.startswith('<') else None
        games.append({
            'game': row['game'],
            'label': label,
            'icon': icon,
            'played': row['played'],
            'won': row['won'],
            'players': row['players'],
            'profit': row['profit'],
        })

    top_players = []
    for row in top_rows:
        member = guild.get_member(row['user_id'])
        top_players.append({
            'user_id': str(row['user_id']),
            'username': member.display_name if member else str(row['user_id']),
            'avatar_url': member.display_avatar.url if member else None,
            'played': row['played'],
            'won': row['won'],
            'winrate': round(row['winrate'] or 0.0, 3),
            'profit': row['profit'],
        })

    return {'games': games, 'top_players': top_players}


# ---------------------------------------------------------------------------
# Global endpoints (no guild context)
# ---------------------------------------------------------------------------


@router.get("/bot/stats")
async def get_bot_stats(bot: BotDep) -> dict:
    """Global bot statistics: guilds, users, latency, uptime, AI engine health."""
    total_commands = await bot.db.stats.count_all_commands()

    # AI engine snapshot (probe is cached internally, so this stays cheap). Guarded in
    # case stats are requested before the AI service is wired during startup.
    ai_service = getattr(bot, 'ai', None)
    ai_health = asdict(await ai_service.health()) if ai_service is not None else None

    return {
        'version': get_full_version(),
        'guild_count': len(bot.guilds),
        'user_count': sum(g.member_count or 0 for g in bot.guilds),
        'channel_count': sum(len(g.channels) for g in bot.guilds),
        'total_commands_used': total_commands,
        'cog_count': len(bot.cogs),
        'command_count': sum(1 for _ in bot.walk_commands()),
        'latency_ms': round(bot.latency * 1000, 1),
        'uptime_seconds': (bot.uptime.total_seconds() if hasattr(bot, 'uptime') else 0),
        'ai': ai_health,
    }


@router.get("/bot/metrics")
async def get_bot_metrics(bot: BotDep) -> dict:
    """Command metrics and query tracker summaries."""
    return {
        'commands': bot.metrics.summary(),
        'queries': bot.db.query_tracker.summary(),
    }


@router.get("/bot/changelog")
async def get_changelog(limit: int = Query(default=20, le=50)) -> dict:
    """Parsed CHANGELOG.md releases with categorized sections."""
    from ..changelog import RELEASES

    entries = [
        {
            'version': r.version,
            'date': r.date,
            'sections': [
                {'name': s.name, 'slug': s.slug, 'entries': s.entries}
                for s in r.sections
            ],
        }
        for r in RELEASES[:limit]
    ]
    return {'entries': entries, 'current_version': get_full_version()}


@router.get("/commands/public")
async def get_public_commands(bot: BotDep) -> dict:
    """All bot commands grouped by cog, without guild-specific disable state."""
    commands = []
    for cmd in bot.walk_commands():
        if cmd.hidden:
            continue
        if cmd.cog and getattr(cmd.cog, '__hidden__', False):
            continue
        if cmd.cog and cmd.cog.qualified_name == 'Jishaku':
            continue

        cog_name = cmd.cog.qualified_name if cmd.cog else 'Uncategorized'
        commands.append({
            'name': cmd.qualified_name,
            'cog': cog_name,
            'description': cmd.short_doc or '',
            'signature': cmd.signature or None,
        })
    return {'commands': commands}


@router.get("/feature-flags")
async def get_feature_flags(bot: BotDep) -> dict:
    """Current feature flag status."""
    return bot.feature_flags.status()


@router.post("/feature-flags")
async def post_feature_flags(bot: BotDep, body: FeatureFlagBody) -> dict:
    """Enable or disable commands/cogs at runtime."""
    if not body.action or not body.target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing "action" or "target" field')

    if body.type == 'cog':
        if body.action == 'disable':
            bot.feature_flags.disable_cog(body.target)
        elif body.action == 'enable':
            bot.feature_flags.enable_cog(body.target)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Unknown action: {body.action}')
    else:
        if body.action == 'disable':
            bot.feature_flags.disable_command(body.target)
        elif body.action == 'enable':
            bot.feature_flags.enable_command(body.target)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Unknown action: {body.action}')

    return bot.feature_flags.status()
