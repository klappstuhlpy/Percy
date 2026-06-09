"""Internal HTTP API for the klappstuhl_me BFF dashboard.

Split into handler mixins (see :mod:`.cog`). Exposes guild configuration over a
local aiohttp server authenticated with a pre-shared token so that all mutations
route through Percy's repository layer and cache invalidation happens atomically.
"""
from __future__ import annotations

from .base import InternalAPI

__all__ = ('InternalAPI',)
