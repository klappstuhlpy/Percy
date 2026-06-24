"""Internal HTTP API for the klappstuhl_me BFF dashboard.

Rebuilt on FastAPI with automatic OpenAPI documentation (served via Scalar).
Split into domain routers under ``routers/``. All mutations route through
Percy's repository layer so cache invalidation happens atomically.
"""
from __future__ import annotations

from .server import InternalAPI

__all__ = ('InternalAPI',)
