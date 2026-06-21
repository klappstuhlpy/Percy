"""The InternalAPI cog: aiohttp server lifecycle + route registration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

import config

from .auth import auth_middleware
from .content import ContentHandlers
from .economy import EconomyHandlers
from .guild import GuildHandlers
from .leveling import LevelingHandlers
from .members import MemberHandlers
from .moderation import ModerationHandlers
from .music import MusicHandlers
from .profile import ProfileHandlers
from .stats import StatsHandlers
from .votes import VoteHandlers

if TYPE_CHECKING:
    from app.core import Bot

log = logging.getLogger(__name__)

__all__ = ('InternalAPI',)


class InternalAPI(
    GuildHandlers,
    MemberHandlers,
    ModerationHandlers,
    LevelingHandlers,
    EconomyHandlers,
    ContentHandlers,
    MusicHandlers,
    ProfileHandlers,
    StatsHandlers,
    VoteHandlers,
):
    """Manages the internal HTTP API server lifecycle."""

    __hidden__ = True

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if not config.internal_api_token:
            log.warning('Internal API disabled (INTERNAL_API_TOKEN not set)')
            return

        self._app = web.Application(middlewares=[auth_middleware])
        assert self._app is not None

        self._app['bot'] = self.bot
        # Guild config
        self._app.router.add_get('/api/internal/guilds/{guild_id}', self._get_guild_config)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/config', self._patch_guild_config)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/roles', self._get_guild_roles)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/channels', self._get_guild_channels)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/audit-log-flags', self._patch_audit_log_flags)
        # Members
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members', self._get_guild_members)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members/{user_id}/detail', self._get_member_detail)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members/{user_id}/avatars', self._get_member_avatars)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/members/{user_id}/action', self._member_action)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/members/{user_id}/roles', self._member_roles)
        # Sentinel
        self._app.router.add_get('/api/internal/guilds/{guild_id}/sentinel', self._get_sentinel)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/sentinel', self._patch_sentinel)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/sentinel/message', self._send_sentinel_message)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/sentinel/toggle', self._toggle_sentinel)
        # User
        self._app.router.add_get('/api/internal/users/{discord_id}/guilds', self._get_user_guilds)
        self._app.router.add_get('/api/internal/users/{discord_id}/avatar', self._get_user_avatar)
        # Leveling
        self._app.router.add_get('/api/internal/guilds/{guild_id}/leveling/config', self._get_leveling_config)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/leveling/leaderboard', self._get_leveling_leaderboard)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/leveling/xp-history', self._get_leveling_xp_history)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/leveling/users/{user_id}', self._patch_leveling_user)
        # Polls
        self._app.router.add_get('/api/internal/guilds/{guild_id}/polls', self._get_polls)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/polls', self._create_poll)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/polls/{poll_id}', self._patch_poll)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/polls/{poll_id}/end', self._end_poll)
        # Giveaways
        self._app.router.add_get('/api/internal/guilds/{guild_id}/giveaways', self._get_giveaways)
        # Tags
        self._app.router.add_get('/api/internal/guilds/{guild_id}/tags', self._get_tags)
        # Commands
        self._app.router.add_get('/api/internal/guilds/{guild_id}/commands', self._get_commands)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/commands/toggle', self._toggle_command)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/plonks', self._manage_plonk)
        # Stats
        self._app.router.add_get('/api/internal/guilds/{guild_id}/stats', self._get_guild_stats)
        self._app.router.add_get('/api/internal/bot/stats', self._get_bot_stats)
        # Autoresponders
        self._app.router.add_get('/api/internal/guilds/{guild_id}/autoresponders', self._get_autoresponders)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/autoresponders', self._create_autoresponder)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/autoresponders/{trigger}', self._delete_autoresponder)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/autoresponders/{trigger}', self._patch_autoresponder)
        # Economy
        self._app.router.add_get('/api/internal/guilds/{guild_id}/economy', self._get_economy)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/economy/items', self._create_economy_item)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/economy/items/{name}', self._delete_economy_item)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/economy/balances', self._get_economy_balances)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/economy/balances/{user_id}', self._patch_economy_balance)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/economy/lottery', self._create_lottery)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/economy/lottery', self._delete_lottery)
        # Comics
        self._app.router.add_get('/api/internal/guilds/{guild_id}/comics', self._get_comics)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/comics', self._create_comic)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/comics/{brand}', self._patch_comic)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/comics/{brand}', self._delete_comic)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/comics/{brand}/push', self._push_comic)
        # Temp Channels
        self._app.router.add_get('/api/internal/guilds/{guild_id}/temp-channels', self._get_temp_channels)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/temp-channels', self._create_temp_channel)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/temp-channels/{channel_id}', self._patch_temp_channel)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/temp-channels/{channel_id}', self._delete_temp_channel)
        # Status Feed
        self._app.router.add_get('/api/internal/guilds/{guild_id}/status-feed', self._get_status_feed)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/status-feed', self._post_status_feed)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/status-feed', self._delete_status_feed)
        # Lockdowns
        self._app.router.add_get('/api/internal/guilds/{guild_id}/lockdowns', self._get_lockdowns)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/lockdowns/lock', self._lock_channels)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/lockdowns/unlock', self._unlock_channels)
        # Moderation ignore list (safe automod entities)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/moderation/ignore', self._manage_moderation_ignore)
        # Highlights
        self._app.router.add_get('/api/internal/guilds/{guild_id}/highlights', self._get_highlights)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/highlights/{user_id}', self._delete_highlight)
        # Emoji Stats
        self._app.router.add_get('/api/internal/guilds/{guild_id}/emoji-stats', self._get_emoji_stats)
        # Leveling (extended)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/leveling/config', self._patch_leveling_config)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/leveling/roles', self._post_leveling_roles)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/leveling/roles/preset', self._create_leveling_role_preset)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/leveling/multipliers', self._post_leveling_multipliers)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/leveling/blacklist', self._post_leveling_blacklist)
        # Moderation (cases browser + management, bulk actions, activity)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/cases', self._get_cases)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/cases', self._create_case)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/cases/recent', self._get_recent_cases)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/cases/{case_index}', self._patch_case)
        self._app.router.add_delete('/api/internal/guilds/{guild_id}/cases/{case_index}', self._delete_case)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/members/bulk-action', self._bulk_member_action)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/members/{user_id}/activity', self._get_member_activity)
        # Music
        self._app.router.add_get('/api/internal/guilds/{guild_id}/music', self._get_music)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/setup', self._post_music_setup)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/reset', self._post_music_reset)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/equalizer', self._post_music_equalizer)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/filters', self._post_music_filters)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/247', self._post_music_247)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/music/dj-mode', self._patch_music_dj_mode)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/music/control', self._post_music_control)
        self._app.router.add_get('/api/internal/guilds/{guild_id}/music/lyrics', self._get_music_lyrics)
        # Custom Bot Profile
        self._app.router.add_get('/api/internal/guilds/{guild_id}/custom-bot', self._get_custom_bot)
        self._app.router.add_patch('/api/internal/guilds/{guild_id}/custom-bot', self._patch_custom_bot)
        self._app.router.add_post('/api/internal/guilds/{guild_id}/custom-bot/reset', self._reset_custom_bot)

        # Feature flags (runtime enable/disable)
        self._app.router.add_get('/api/internal/feature-flags', self._get_feature_flags)
        self._app.router.add_post('/api/internal/feature-flags', self._post_feature_flags)
        # Metrics (command latency, query timing)
        self._app.router.add_get('/api/internal/bot/metrics', self._get_bot_metrics)

        # Public vote webhooks (bot lists). Exempt from the bearer middleware; each
        # handler validates the per-service secret instead. Expose via your reverse proxy.
        self._app.router.add_post('/api/webhooks/topgg', self._vote_topgg)
        self._app.router.add_post('/api/webhooks/discordbotlist', self._vote_discordbotlist)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, config.internal_api_host, config.internal_api_port)
        await self._site.start()
        log.info('Internal API listening on %s:%d', config.internal_api_host, config.internal_api_port)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()

