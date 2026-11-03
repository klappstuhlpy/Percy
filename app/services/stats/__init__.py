"""Stats: analytics series, bot health, character info, code, gateway and presence statistics."""

from app.services.stats.analytics import (
    GRANULARITIES,
    METRICS,
    RANGES,
    default_granularity,
    fill_buckets,
    floor_bucket,
    resolve_granularity,
    resolve_range,
)
from app.services.stats.bot_health import (
    BotHealthReport,
    ConnectionState,
    HealthLevel,
    LavalinkMetrics,
    assess_bot_health,
    parse_lavalink_metrics,
    parse_prometheus_samples,
)
from app.services.stats.char_info import (
    MAX_CHARACTERS,
    CharInfo,
    get_char_info,
)
from app.services.stats.code_stats import (
    CodeStats,
    count_code_stats,
)
from app.services.stats.gateway_stats import (
    GatewayTraffic,
    summarize_gateway_traffic,
)
from app.services.stats.presence_stats import (
    PRESENCE_STATUSES,
    PresenceBreakdown,
    summarize_presence,
)

__all__ = (
    "GRANULARITIES",
    "MAX_CHARACTERS",
    "METRICS",
    "PRESENCE_STATUSES",
    "RANGES",
    "BotHealthReport",
    "CharInfo",
    "CodeStats",
    "ConnectionState",
    "GatewayTraffic",
    "HealthLevel",
    "LavalinkMetrics",
    "PresenceBreakdown",
    "assess_bot_health",
    "count_code_stats",
    "default_granularity",
    "fill_buckets",
    "floor_bucket",
    "get_char_info",
    "parse_lavalink_metrics",
    "parse_prometheus_samples",
    "resolve_granularity",
    "resolve_range",
    "summarize_gateway_traffic",
    "summarize_presence",
)
