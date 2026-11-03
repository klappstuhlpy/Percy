"""Response models for ``guild.py`` — the source of truth for percy-client's generated guild types.

Class names are the Rust type names (dashboard/crates/percy-client/GENERATION.md). Integer
bounds exist only so the generator picks the same Rust width the hand-written structs used.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

U32 = Annotated[int, Field(ge=0, le=2**32 - 1)]
U64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
I32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]


class ChannelRef(BaseModel):
    id: str
    name: str
    type: str


class RoleRef(BaseModel):
    id: str
    name: str
    color: U32


class IgnoredEntity(BaseModel):
    id: str
    type: str
    name: str


class GuildFlags(BaseModel):
    audit_log: bool
    raid: bool
    alerts: bool
    sentinel: bool
    mentions: bool


class AiFlags(BaseModel):
    assistant: bool
    router: bool
    moderation: bool
    sentinel: bool
    music: bool
    polls: bool
    giveaways: bool
    tags: bool
    reminders: bool


class AiChannelOverride(BaseModel):
    channel: ChannelRef | None
    channel_id: str
    controlled: AiFlags
    enabled: AiFlags


class AiConfig(BaseModel):
    """Per-guild AI feature flags plus per-channel overrides."""

    flags: AiFlags
    overrides: list[AiChannelOverride]


class GuildInfo(BaseModel):
    ai: AiConfig
    id: U64
    name: str
    icon_url: str | None
    member_count: U32 | None
    flags: GuildFlags
    audit_log_channel: ChannelRef | None
    poll_channel: ChannelRef | None
    poll_ping_role: RoleRef | None
    poll_reason_channel: ChannelRef | None
    mention_count: U32 | None
    ignored_entities: list[IgnoredEntity]
    mute_role: RoleRef | None
    alert_channel: ChannelRef | None
    mod_log_channel: ChannelRef | None
    message_log_channel: ChannelRef | None
    voice_log_channel: ChannelRef | None
    audit_log_flags: dict[str, bool]
    music_panel_channel: ChannelRef | None
    use_music_panel: bool
    mod_dm_on_action: bool
    mod_require_reason: bool
    mod_reinvite_on_unban: bool
    mod_default_ban_delete_days: U32
    disabled_command_message: str | None
    prefixes: list[str]
    is_new_config: bool


class AiSuggestion(BaseModel):
    label: str
    command: str


class AiAskResponse(BaseModel):
    """``available`` is false when the assistant is off/unreachable; ``reason`` then says why."""

    available: bool
    reason: str | None = None
    answer: str | None = None
    suggestions: list[AiSuggestion]


class Role(BaseModel):
    id: str
    name: str
    color: U32
    position: I32
    permissions: U64
    mentionable: bool
    managed: bool
    hoist: bool
    icon_url: str | None
    assignable: bool


class Channel(BaseModel):
    id: str
    name: str
    type: str
    position: I32
    category_id: str | None
    can_post: bool


class SentinelInfo(BaseModel):
    channel: ChannelRef | None
    role: RoleRef | None
    message: U64 | None
    starter_role: RoleRef | None
    bypass_action: str
    rate: str | None
    started_at: str | None
    member_count: U32
    needs_setup: bool


class HealthFeature(BaseModel):
    key: str
    label: str
    status: str  # ok | attention | off — open set, so str
    problems: list[str]


class GuildHealth(BaseModel):
    features: list[HealthFeature]
    attention: U32
