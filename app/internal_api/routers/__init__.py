"""API domain routers, one per feature area."""
from __future__ import annotations

from .analytics import router as analytics_router
from .backup import router as backup_router
from .content import router as content_router
from .economy import router as economy_router
from .gallery import router as gallery_router
from .guild import router as guild_router
from .leveling import router as leveling_router
from .members import router as members_router
from .moderation import router as moderation_router
from .music import router as music_router
from .profile import router as profile_router
from .stats import router as stats_router
from .subscriptions import router as subscriptions_router
from .users import router as users_router
from .webhooks import router as webhooks_router

ALL_ROUTERS = [
    guild_router,
    members_router,
    moderation_router,
    leveling_router,
    economy_router,
    content_router,
    music_router,
    profile_router,
    stats_router,
    users_router,
    webhooks_router,
    analytics_router,
    backup_router,
    subscriptions_router,
    gallery_router,
]
