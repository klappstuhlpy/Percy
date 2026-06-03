"""Service layer: business logic extracted from cogs.

Cogs act as controllers and delegate non-trivial logic (counting, multi-step
orchestration, math) to the pure, unit-testable helpers in this package.
"""

from app.services.char_info import MAX_CHARACTERS, CharInfo, get_char_info
from app.services.code_stats import CodeStats, count_code_stats
from app.services.gateway_stats import GatewayTraffic, summarize_gateway_traffic

__all__ = (
    'MAX_CHARACTERS',
    'CharInfo',
    'CodeStats',
    'GatewayTraffic',
    'count_code_stats',
    'get_char_info',
    'summarize_gateway_traffic',
)
