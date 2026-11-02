"""Pure rules and payload shaping for member reports. Discord-free; SQL is ``db.notifications``."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = (
    "EXCERPT_LIMIT",
    "HOURLY_LIMIT",
    "NOT_SET_UP",
    "PAIR_COOLDOWN",
    "REPORT_ACTIONS",
    "check_report_rate",
    "report_channel_error",
    "report_meta",
)

NOT_SET_UP = "Reports aren't set up on this server — ask a moderator to set a report channel."

EXCERPT_LIMIT = 300
PAIR_COOLDOWN = datetime.timedelta(minutes=10)  # one report per reporter per target
HOURLY_LIMIT = 5  # reports per reporter per hour
HOURLY_WINDOW = datetime.timedelta(hours=1)

#: Button verb -> (member action, permission the clicking moderator needs). ``dismiss`` runs no action.
REPORT_ACTIONS: dict[str, tuple[str | None, str]] = {
    "dismiss": (None, "manage_messages"),
    "warn": ("warn", "kick_members"),
    "timeout": ("timeout", "moderate_members"),
    "ban": ("ban", "ban_members"),
}


def check_report_rate(recent: Iterable[tuple[int, datetime.datetime]], target_id: int, now: datetime.datetime) -> str | None:
    """``recent`` is the reporter's earlier ``(target_id, created_at)`` pairs; returns a refusal or ``None``."""
    recent = list(recent)
    if any(t == target_id and now - at < PAIR_COOLDOWN for t, at in recent):
        return "You already reported that member a moment ago. Give the moderators a few minutes."
    if sum(1 for _, at in recent if now - at < HOURLY_WINDOW) >= HOURLY_LIMIT:
        return f"You can send at most {HOURLY_LIMIT} reports per hour."
    return None


def report_meta(
    *, reporter_id: int, target_id: int, reason: str, channel_id: int | None = None,
    message_url: str | None = None, content: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The notification ``meta`` for a report; ids are strings (JSON-safe snowflakes), excerpt capped."""
    meta: dict[str, Any] = {"reporter_id": str(reporter_id), "target_id": str(target_id), "reason": reason}
    if channel_id:
        meta["channel_id"] = str(channel_id)
    if message_url:
        meta["message_url"] = message_url
    if content:
        meta["excerpt"] = content[: EXCERPT_LIMIT - 1] + "…" if len(content) > EXCERPT_LIMIT else content
    return meta


def report_channel_error(*, configured: bool, exists: bool, can_post: bool) -> str | None:
    """The refusal shown to a reporter when there is nowhere to deliver the report, else ``None``."""
    return None if configured and exists and can_post else NOT_SET_UP
