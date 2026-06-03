"""Service layer: business logic extracted from cogs.

Cogs act as controllers and delegate non-trivial logic (counting, multi-step
orchestration, math) to the pure, unit-testable helpers in this package.
"""

from app.services.bot_health import BotHealthReport, ConnectionState, HealthLevel, assess_bot_health
from app.services.char_info import MAX_CHARACTERS, CharInfo, get_char_info
from app.services.code_stats import CodeStats, count_code_stats
from app.services.gateway_stats import GatewayTraffic, summarize_gateway_traffic
from app.services.presence_stats import PRESENCE_STATUSES, PresenceBreakdown, summarize_presence
from app.services.purge import PurgeMessage, PurgePlan, build_purge_predicate

__all__ = (
    'MAX_CHARACTERS',
    'PRESENCE_STATUSES',
    'BotHealthReport',
    'CharInfo',
    'CodeStats',
    'ConnectionState',
    'GatewayTraffic',
    'HealthLevel',
    'PresenceBreakdown',
    'PurgeMessage',
    'PurgePlan',
    'assess_bot_health',
    'build_purge_predicate',
    'count_code_stats',
    'get_char_info',
    'summarize_gateway_traffic',
    'summarize_presence',
)
