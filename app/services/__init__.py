"""Service layer: business logic extracted from cogs.

Cogs act as controllers and delegate non-trivial logic (counting, multi-step
orchestration, math) to the pure, unit-testable helpers in this package.
"""

from app.services.bot_health import (
    BotHealthReport,
    ConnectionState,
    HealthLevel,
    LavalinkMetrics,
    assess_bot_health,
    parse_lavalink_metrics,
)
from app.services.char_info import MAX_CHARACTERS, CharInfo, get_char_info
from app.services.code_stats import CodeStats, count_code_stats
from app.services.economy import (
    DailyResult,
    boost_multiplier,
    compute_daily,
    describe_effect,
    roll_lootbox,
    sell_price,
    validate_item_effect,
)
from app.services.gateway_stats import GatewayTraffic, summarize_gateway_traffic
from app.services.lyrics import LyricLine, LyricsResult, SyncedLyrics, clean_track_title, parse_lrc
from app.services.presence_stats import PRESENCE_STATUSES, PresenceBreakdown, summarize_presence
from app.services.purge import PurgeMessage, PurgePlan, build_purge_predicate
from app.services.recurrence import (
    RecurrenceResult,
    advance_recurrence,
    describe_interval,
    interval_too_short,
    next_occurrence,
    normalize_interval,
)
from app.services.spam_penalty import compute_spam_penalty

__all__ = (
    'MAX_CHARACTERS',
    'PRESENCE_STATUSES',
    'BotHealthReport',
    'CharInfo',
    'CodeStats',
    'ConnectionState',
    'DailyResult',
    'GatewayTraffic',
    'HealthLevel',
    'LavalinkMetrics',
    'LyricLine',
    'LyricsResult',
    'PresenceBreakdown',
    'PurgeMessage',
    'PurgePlan',
    'RecurrenceResult',
    'SyncedLyrics',
    'advance_recurrence',
    'assess_bot_health',
    'boost_multiplier',
    'build_purge_predicate',
    'clean_track_title',
    'compute_daily',
    'compute_spam_penalty',
    'count_code_stats',
    'describe_effect',
    'describe_interval',
    'get_char_info',
    'interval_too_short',
    'next_occurrence',
    'normalize_interval',
    'parse_lavalink_metrics',
    'parse_lrc',
    'roll_lootbox',
    'sell_price',
    'summarize_gateway_traffic',
    'summarize_presence',
    'validate_item_effect',
)
