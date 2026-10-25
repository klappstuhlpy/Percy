"""Who may do what with a guild's music player (shared by the dashboard and ``/music/control``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ('MusicViewer', 'resolve_viewer')


@dataclass(frozen=True, slots=True)
class MusicViewer:
    role: Literal['visitor', 'listener', 'manager']
    can_start: bool
    can_control: bool
    can_destroy: bool
    is_manager: bool


def resolve_viewer(
    *, has_dj: bool, user_channel_id: int | None, bot_channel_id: int | None, dj_mode: int
) -> MusicViewer:
    """``dj_mode``: 0 everyone, 1 dj_only, 2 hybrid."""
    in_voice = bot_channel_id is not None and user_channel_id == bot_channel_id
    can_start = bot_channel_id is None and user_channel_id is not None and (has_dj or dj_mode != 1)
    can_control = has_dj or (in_voice and dj_mode != 1)
    can_destroy = has_dj or (in_voice and dj_mode == 0)
    role = 'manager' if has_dj else 'listener' if (can_control or can_start) else 'visitor'
    return MusicViewer(role, can_start, can_control, can_destroy, has_dj)
