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

API_V1 = '/api/v1'


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

        r = self._app.router
        g = f'{API_V1}/guilds/{{guild_id}}'

        # Guild config
        r.add_get(f'{g}', self._get_guild_config)
        r.add_patch(f'{g}/config', self._patch_guild_config)
        r.add_post(f'{g}/batch', self._batch_guild_config)
        r.add_get(f'{g}/roles', self._get_guild_roles)
        r.add_get(f'{g}/channels', self._get_guild_channels)
        r.add_patch(f'{g}/audit-log-flags', self._patch_audit_log_flags)
        # Members
        r.add_get(f'{g}/members', self._get_guild_members)
        r.add_get(f'{g}/members/{{user_id}}/self', self._get_member_self)
        r.add_get(f'{g}/members/{{user_id}}/detail', self._get_member_detail)
        r.add_get(f'{g}/members/{{user_id}}/avatars', self._get_member_avatars)
        r.add_post(f'{g}/members/{{user_id}}/action', self._member_action)
        r.add_patch(f'{g}/members/{{user_id}}/roles', self._member_roles)
        # Sentinel
        r.add_get(f'{g}/sentinel', self._get_sentinel)
        r.add_patch(f'{g}/sentinel', self._patch_sentinel)
        r.add_post(f'{g}/sentinel/message', self._send_sentinel_message)
        r.add_post(f'{g}/sentinel/toggle', self._toggle_sentinel)
        # User
        r.add_get(f'{API_V1}/users/{{discord_id}}/guilds', self._get_user_guilds)
        r.add_get(f'{API_V1}/users/{{discord_id}}/avatar', self._get_user_avatar)
        r.add_get(f'{API_V1}/users/{{discord_id}}/settings', self._get_user_settings)
        r.add_patch(f'{API_V1}/users/{{discord_id}}/settings', self._patch_user_settings)
        # Leveling
        r.add_get(f'{g}/leveling/config', self._get_leveling_config)
        r.add_get(f'{g}/leveling/leaderboard', self._get_leveling_leaderboard)
        r.add_get(f'{g}/leveling/xp-history', self._get_leveling_xp_history)
        r.add_patch(f'{g}/leveling/users/{{user_id}}', self._patch_leveling_user)
        r.add_patch(f'{g}/leveling/config', self._patch_leveling_config)
        r.add_post(f'{g}/leveling/roles', self._post_leveling_roles)
        r.add_post(f'{g}/leveling/roles/preset', self._create_leveling_role_preset)
        r.add_post(f'{g}/leveling/multipliers', self._post_leveling_multipliers)
        r.add_post(f'{g}/leveling/blacklist', self._post_leveling_blacklist)
        # Polls
        r.add_get(f'{g}/polls', self._get_polls)
        r.add_post(f'{g}/polls', self._create_poll)
        r.add_patch(f'{g}/polls/{{poll_id}}', self._patch_poll)
        r.add_post(f'{g}/polls/{{poll_id}}/end', self._end_poll)
        # Giveaways
        r.add_get(f'{g}/giveaways', self._get_giveaways)
        # Tags
        r.add_get(f'{g}/tags', self._get_tags)
        # Commands
        r.add_get(f'{g}/commands', self._get_commands)
        r.add_post(f'{g}/commands/toggle', self._toggle_command)
        r.add_post(f'{g}/plonks', self._manage_plonk)
        # Stats
        r.add_get(f'{g}/stats', self._get_guild_stats)
        r.add_get(f'{g}/overview', self._get_guild_overview)
        r.add_get(f'{API_V1}/bot/stats', self._get_bot_stats)
        r.add_get(f'{API_V1}/bot/metrics', self._get_bot_metrics)
        r.add_get(f'{API_V1}/bot/changelog', self._get_changelog)
        r.add_get(f'{API_V1}/commands/public', self._get_public_commands)
        # Autoresponders
        r.add_get(f'{g}/autoresponders', self._get_autoresponders)
        r.add_post(f'{g}/autoresponders', self._create_autoresponder)
        r.add_delete(f'{g}/autoresponders/{{trigger}}', self._delete_autoresponder)
        r.add_patch(f'{g}/autoresponders/{{trigger}}', self._patch_autoresponder)
        # Economy
        r.add_get(f'{g}/economy', self._get_economy)
        r.add_post(f'{g}/economy/items', self._create_economy_item)
        r.add_delete(f'{g}/economy/items/{{name}}', self._delete_economy_item)
        r.add_get(f'{g}/economy/balances', self._get_economy_balances)
        r.add_patch(f'{g}/economy/balances/{{user_id}}', self._patch_economy_balance)
        r.add_post(f'{g}/economy/lottery', self._create_lottery)
        r.add_delete(f'{g}/economy/lottery', self._delete_lottery)
        # Comics
        r.add_get(f'{g}/comics', self._get_comics)
        r.add_post(f'{g}/comics', self._create_comic)
        r.add_patch(f'{g}/comics/{{brand}}', self._patch_comic)
        r.add_delete(f'{g}/comics/{{brand}}', self._delete_comic)
        r.add_post(f'{g}/comics/{{brand}}/push', self._push_comic)
        # Temp Channels
        r.add_get(f'{g}/temp-channels', self._get_temp_channels)
        r.add_post(f'{g}/temp-channels', self._create_temp_channel)
        r.add_patch(f'{g}/temp-channels/{{channel_id}}', self._patch_temp_channel)
        r.add_delete(f'{g}/temp-channels/{{channel_id}}', self._delete_temp_channel)
        # Status Feed
        r.add_get(f'{g}/status-feed', self._get_status_feed)
        r.add_post(f'{g}/status-feed', self._post_status_feed)
        r.add_delete(f'{g}/status-feed', self._delete_status_feed)
        # Lockdowns
        r.add_get(f'{g}/lockdowns', self._get_lockdowns)
        r.add_post(f'{g}/lockdowns/lock', self._lock_channels)
        r.add_post(f'{g}/lockdowns/unlock', self._unlock_channels)
        # Moderation ignore list (safe automod entities)
        r.add_post(f'{g}/moderation/ignore', self._manage_moderation_ignore)
        # Highlights
        r.add_get(f'{g}/highlights', self._get_highlights)
        r.add_delete(f'{g}/highlights/{{user_id}}', self._delete_highlight)
        # Emoji Stats
        r.add_get(f'{g}/emoji-stats', self._get_emoji_stats)
        # Moderation (cases browser + management, bulk actions, activity)
        r.add_get(f'{g}/cases', self._get_cases)
        r.add_post(f'{g}/cases', self._create_case)
        r.add_get(f'{g}/cases/recent', self._get_recent_cases)
        r.add_patch(f'{g}/cases/{{case_index}}', self._patch_case)
        r.add_delete(f'{g}/cases/{{case_index}}', self._delete_case)
        r.add_post(f'{g}/members/bulk-action', self._bulk_member_action)
        r.add_get(f'{g}/members/{{user_id}}/activity', self._get_member_activity)
        # Music
        r.add_get(f'{g}/music', self._get_music)
        r.add_post(f'{g}/music/setup', self._post_music_setup)
        r.add_post(f'{g}/music/reset', self._post_music_reset)
        r.add_post(f'{g}/music/equalizer', self._post_music_equalizer)
        r.add_post(f'{g}/music/filters', self._post_music_filters)
        r.add_post(f'{g}/music/247', self._post_music_247)
        r.add_patch(f'{g}/music/dj-mode', self._patch_music_dj_mode)
        r.add_post(f'{g}/music/control', self._post_music_control)
        r.add_get(f'{g}/music/lyrics', self._get_music_lyrics)
        # Custom Bot Profile
        r.add_get(f'{g}/custom-bot', self._get_custom_bot)
        r.add_patch(f'{g}/custom-bot', self._patch_custom_bot)
        r.add_post(f'{g}/custom-bot/reset', self._reset_custom_bot)
        # Feature flags (runtime enable/disable)
        r.add_get(f'{API_V1}/feature-flags', self._get_feature_flags)
        r.add_post(f'{API_V1}/feature-flags', self._post_feature_flags)

        # Public vote webhooks (bot lists). Exempt from the bearer middleware; each
        # handler validates the per-service secret instead. Expose via your reverse proxy.
        r.add_post('/api/webhooks/topgg', self._vote_topgg)
        r.add_post('/api/webhooks/discordbotlist', self._vote_discordbotlist)

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

