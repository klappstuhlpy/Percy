"""Notifications cog package; lazy so importing ``decide`` does not load ``cog``/``ui`` (see moderation)."""

from __future__ import annotations

lazy from app.cogs.notifications.cog import Notifications, setup

__all__ = (
    'Notifications',
    'setup',
)
