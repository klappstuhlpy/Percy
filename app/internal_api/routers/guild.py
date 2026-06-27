"""Guild configuration, roles, channels, and sentinel endpoints."""
from __future__ import annotations

import contextlib
import re

import discord
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import resolve_channel, resolve_entity, resolve_role

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Guilds"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PatchGuildConfigBody(BaseModel):
    audit_log_channel_id: int | None = None
    poll_channel_id: int | None = None
    poll_ping_role_id: int | None = None
    poll_reason_channel_id: int | None = None
    mention_count: int | None = None
    mute_role_id: int | None = None
    alert_channel_id: int | None = None
    music_panel_channel_id: int | None = None
    use_music_panel: bool | None = None
    mod_log_channel_id: int | None = None
    message_log_channel_id: int | None = None
    voice_log_channel_id: int | None = None
    flags: dict[str, bool] | None = None
    prefixes: list[str] | None = None

    model_config = {"extra": "ignore"}


class PatchAIFlagsBody(BaseModel):
    flags: dict[str, bool]

    model_config = {"extra": "ignore"}


class PutAIOverrideBody(BaseModel):
    # Which AI features this channel overrides, and their on/off value for those features.
    controlled: dict[str, bool] = {}
    enabled: dict[str, bool] = {}

    model_config = {"extra": "ignore"}


class BatchOperation(BaseModel):
    type: str
    data: dict = {}


class BatchGuildConfigBody(BaseModel):
    operations: list[BatchOperation]


class ModerationIgnoreBody(BaseModel):
    action: str
    entity_id: int | str


class PatchAuditLogFlagsBody(BaseModel):
    model_config = {"extra": "allow"}


class PatchSentinelBody(BaseModel):
    channel_id: int | None = None
    role_id: int | None = None
    starter_role_id: int | None = None
    bypass_action: str | None = None
    rate: str | None = None

    model_config = {"extra": "ignore"}


class SendSentinelMessageBody(BaseModel):
    channel_id: int | str
    title: str | None = None
    content: str | None = None


class ToggleSentinelBody(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_CONFIG_FIELDS = {
    "audit_log_channel_id", "poll_channel_id", "poll_ping_role_id",
    "poll_reason_channel_id", "mention_count", "mute_role_id",
    "alert_channel_id", "music_panel_channel_id", "use_music_panel",
    "mod_log_channel_id", "message_log_channel_id", "voice_log_channel_id",
}

_FLAG_MAP = {"audit_log": 1, "raid": 2, "alerts": 4, "sentinel": 8, "mentions": 16}

#: Bit values for the per-guild AI feature flags — must match GuildConfig.AIFlags.
_AI_FLAG_MAP = {
    "assistant": 1, "router": 2, "moderation": 4, "sentinel": 8, "music": 16,
    "polls": 32, "giveaways": 64, "tags": 128, "reminders": 256,
}


async def _rotate_log_webhook(bot, guild, old_url: str | None, channel_id: int | None, name: str) -> str | None:
    """(Re)create the webhook backing a logging/alert channel set from the dashboard.

    ``send_alert`` and audit logging need a *webhook*, not just a channel id — but the
    dashboard can only send a channel id. Percy has ``manage_webhooks``, so we create the
    webhook here (mirroring the in-bot setup command), deleting any previous one first.
    Returns the new webhook url, or ``None`` when the channel is being cleared.
    """
    if old_url:
        with contextlib.suppress(discord.HTTPException):
            await discord.Webhook.from_url(old_url, session=bot.session).delete()

    if channel_id is None:
        return None

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"channel {channel_id} is not a text channel")

    avatar = await bot.user.avatar.read() if bot.user and bot.user.avatar else None
    try:
        webhook = await channel.create_webhook(name=name, avatar=avatar)
    except discord.Forbidden:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"missing permission to create a webhook in #{channel.name}",
        ) from None
    except discord.HTTPException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="failed to create the webhook (the channel may be at its 10-webhook limit)",
        ) from None
    return webhook.url


def _build_config_updates(data: dict, guild_config) -> dict[str, object]:
    """Extract valid config updates from a flat dict (shared by PATCH and batch)."""
    updates: dict[str, object] = {}
    for key, value in data.items():
        if key in _ALLOWED_CONFIG_FIELDS:
            updates[key] = value
        elif key == "flags" and isinstance(value, dict):
            new_flags = guild_config.flags.value
            for flag_name, bit in _FLAG_MAP.items():
                if flag_name in value:
                    if value[flag_name]:
                        new_flags |= bit
                    else:
                        new_flags &= ~bit
            updates["flags"] = new_flags
        elif key == "prefixes" and isinstance(value, list):
            updates["prefixes"] = value
    return updates


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _serialize_ai_config(guild, ai_config) -> dict:
    """Shape the resolved AI config (server flags + per-channel overrides) for the dashboard."""
    return {
        "flags": {name: bool(getattr(ai_config.flags, name)) for name in _AI_FLAG_MAP},
        "overrides": [
            {
                "channel": resolve_channel(guild, channel_id),
                "channel_id": str(channel_id),
                "controlled": {name: bool(flags_mask & bit) for name, bit in _AI_FLAG_MAP.items()},
                "enabled": {name: bool(enabled_mask & bit) for name, bit in _AI_FLAG_MAP.items()},
            }
            for channel_id, (flags_mask, enabled_mask) in ai_config.overrides.items()
        ],
    }


@router.get("")
async def get_guild_config(guild: GuildDep, bot: BotDep) -> dict:
    guild_config = await bot.db.get_guild_config(guild.id)
    ai_config = await bot.db.get_guild_ai_config(guild.id)
    return {
        "ai": _serialize_ai_config(guild, ai_config),
        "id": guild_config.id,
        "name": guild.name,
        "icon_url": guild.icon.url if guild.icon else None,
        "member_count": guild.member_count,
        "flags": {
            "audit_log": guild_config.flags.audit_log,
            "raid": guild_config.flags.raid,
            "alerts": guild_config.flags.alerts,
            "sentinel": guild_config.flags.sentinel,
            "mentions": guild_config.flags.mentions,
        },
        "audit_log_channel": resolve_channel(guild, guild_config.audit_log_channel_id),
        "poll_channel": resolve_channel(guild, guild_config.poll_channel_id),
        "poll_ping_role": resolve_role(guild, guild_config.poll_ping_role_id),
        "poll_reason_channel": resolve_channel(guild, guild_config.poll_reason_channel_id),
        "mention_count": guild_config.mention_count,
        "ignored_entities": [resolve_entity(guild, eid) for eid in guild_config.safe_automod_entity_ids],
        "mute_role": resolve_role(guild, guild_config.mute_role_id),
        "alert_channel": resolve_channel(guild, guild_config.alert_channel_id),
        "mod_log_channel": resolve_channel(guild, getattr(guild_config, "mod_log_channel_id", None)),
        "message_log_channel": resolve_channel(guild, getattr(guild_config, "message_log_channel_id", None)),
        "voice_log_channel": resolve_channel(guild, getattr(guild_config, "voice_log_channel_id", None)),
        "audit_log_flags": guild_config.audit_log_flags or {},
        "music_panel_channel": resolve_channel(guild, guild_config.music_panel_channel_id),
        "use_music_panel": guild_config.use_music_panel,
        "prefixes": list(guild_config.prefixes),
        "is_new_config": guild_config.flags.value == 0 and guild_config.audit_log_channel_id is None,
    }


@router.patch("")
async def patch_guild_config(guild: GuildDep, bot: BotDep, body: PatchGuildConfigBody) -> dict:
    # exclude_unset (not exclude_none): a client-sent ``null`` is a deliberate "clear", which
    # we must honour (e.g. clearing an alert channel) — exclude_none would silently drop it.
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    guild_config = await bot.db.get_guild_config(guild.id)
    updates = _build_config_updates(data, guild_config)

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    # Alert / audit-log channels only work with a backing webhook (send_alert / audit logging
    # use the webhook, not the channel id). The dashboard sends just a channel id, so create
    # or delete the webhook here. Skip if the channel is unchanged and already has a webhook.
    for channel_field, url_field, hook_name in (
        ("alert_channel_id", "alert_webhook_url", "Moderation Alerts"),
        ("audit_log_channel_id", "audit_log_webhook_url", "Audit Log"),
    ):
        if channel_field not in updates:
            continue
        new_channel_id = updates[channel_field]
        if new_channel_id == getattr(guild_config, channel_field, None) and getattr(guild_config, url_field, None):
            continue
        updates[url_field] = await _rotate_log_webhook(
            bot, guild, getattr(guild_config, url_field, None), new_channel_id, hook_name
        )

    await guild_config.update(**updates)
    return {"ok": True}


@router.get("/ai")
async def get_ai_config(guild: GuildDep, bot: BotDep) -> dict:
    ai_config = await bot.db.get_guild_ai_config(guild.id)
    return _serialize_ai_config(guild, ai_config)


@router.patch("/ai")
async def patch_ai_flags(guild: GuildDep, bot: BotDep, body: PatchAIFlagsBody) -> dict:
    """Toggle the server-wide AI feature flags (only known flag names are honoured)."""
    guild_config = await bot.db.get_guild_config(guild.id)
    new_value = guild_config.ai_flags.value
    changed = False
    for name, enabled in body.flags.items():
        bit = _AI_FLAG_MAP.get(name)
        if bit is None:
            continue
        changed = True
        if enabled:
            new_value |= bit
        else:
            new_value &= ~bit

    if not changed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid AI flags to update")

    await bot.db.guilds.set_ai_flags(guild.id, new_value)
    return {"ok": True}


@router.put("/ai/overrides/{channel_id}")
async def put_ai_override(guild: GuildDep, bot: BotDep, channel_id: int, body: PutAIOverrideBody) -> dict:
    """Set a per-channel AI override. An override that controls nothing is removed."""
    flags_mask = 0
    enabled_mask = 0
    for name, bit in _AI_FLAG_MAP.items():
        if body.controlled.get(name):
            flags_mask |= bit
            if body.enabled.get(name):
                enabled_mask |= bit

    if flags_mask == 0:
        await bot.db.guilds.delete_ai_override(guild.id, channel_id)
        return {"ok": True, "removed": True}

    await bot.db.guilds.upsert_ai_override(guild.id, channel_id, flags_mask, enabled_mask)
    return {"ok": True}


@router.delete("/ai/overrides/{channel_id}")
async def delete_ai_override(guild: GuildDep, bot: BotDep, channel_id: int) -> dict:
    await bot.db.guilds.delete_ai_override(guild.id, channel_id)
    return {"ok": True}


@router.post("/batch")
async def batch_guild_config(guild: GuildDep, bot: BotDep, body: BatchGuildConfigBody) -> dict:
    if not body.operations:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="operations must be a non-empty array")

    results: list[dict] = []

    for op in body.operations:
        op_type = op.type
        data = op.data

        if op_type == "config":
            guild_config = await bot.db.get_guild_config(guild.id)
            updates = _build_config_updates(data, guild_config)
            if updates:
                await guild_config.update(**updates)
            results.append({"type": "config", "ok": True})

        elif op_type == "sentinel":
            allowed = {"channel_id", "role_id", "starter_role_id", "bypass_action", "rate"}
            updates = {k: v for k, v in data.items() if k in allowed}
            if updates:
                await bot.db.guilds.upsert_sentinel(guild.id, updates)
            results.append({"type": "sentinel", "ok": True})

        elif op_type == "sentinel_toggle":
            enabled = data.get("enabled")
            sentinel = await bot.db.get_guild_sentinel(guild.id)
            if enabled and sentinel and not sentinel.requires_setup and sentinel.started_at is None:
                await sentinel.enable()
            elif not enabled and sentinel and sentinel.started_at is not None:
                await sentinel.disable()
            results.append({"type": "sentinel_toggle", "ok": True})

        elif op_type == "audit_log_flags":
            config = await bot.db.get_guild_config(guild.id)
            current_flags = config.audit_log_flags or {}
            for key, value in data.items():
                if key in current_flags:
                    current_flags[key] = bool(value)
            await bot.db.moderation.set_audit_log_flags(guild.id, current_flags)
            results.append({"type": "audit_log_flags", "ok": True})

        else:
            results.append({"type": op_type, "ok": False, "error": f"unknown operation type: {op_type}"})

    return {"ok": True, "results": results}


@router.post("/moderation/ignore")
async def manage_moderation_ignore(guild: GuildDep, bot: BotDep, body: ModerationIgnoreBody) -> dict:
    if body.action not in ("add", "remove"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be add or remove")

    entity_id = int(body.entity_id)

    if body.action == "add":
        await bot.db.moderation.add_safe_entities(guild.id, [entity_id])
    else:
        await bot.db.moderation.remove_safe_entities(guild.id, [entity_id])

    return {"ok": True}


@router.patch("/audit-log-flags")
async def patch_audit_log_flags(guild: GuildDep, bot: BotDep, body: PatchAuditLogFlagsBody) -> dict:
    data = body.model_dump()
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body must be an object mapping flag names to booleans",
        )

    config = await bot.db.get_guild_config(guild.id)
    current_flags = config.audit_log_flags or {}

    for key, value in data.items():
        if key in current_flags:
            current_flags[key] = bool(value)

    await bot.db.moderation.set_audit_log_flags(guild.id, current_flags)
    return {"ok": True, "flags": current_flags}


@router.get("/roles")
async def get_guild_roles(guild: GuildDep) -> list[dict]:
    return [
        {
            "id": str(role.id),
            "name": role.name,
            "color": role.color.value,
            "position": role.position,
            "permissions": role.permissions.value,
            "mentionable": role.mentionable,
            "managed": role.managed,
            "hoist": role.hoist,
            "icon_url": role.icon.url if role.icon else None,
        }
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
    ]


@router.get("/channels")
async def get_guild_channels(guild: GuildDep) -> list[dict]:
    return [
        {
            "id": str(ch.id),
            "name": ch.name,
            "type": str(ch.type),
            "position": ch.position,
            "category_id": str(ch.category_id) if ch.category_id else None,
        }
        for ch in sorted(guild.channels, key=lambda c: (c.position, c.name))
    ]


@router.get("/sentinel")
async def get_sentinel(guild: GuildDep, bot: BotDep) -> dict | None:
    sentinel = await bot.db.get_guild_sentinel(guild.id)
    if sentinel is None:
        return None

    rate = sentinel.rate
    if isinstance(rate, (list, tuple)):
        rate = f"{rate[0]}/{rate[1]}"

    return {
        "channel": resolve_channel(guild, sentinel.channel_id),
        "role": resolve_role(guild, sentinel.role_id),
        "message": sentinel.message_id,
        "starter_role": resolve_role(guild, sentinel.starter_role_id),
        "bypass_action": sentinel.bypass_action,
        "rate": rate if isinstance(rate, str) else None,
        "started_at": sentinel.started_at.isoformat() if sentinel.started_at else None,
        "member_count": len(sentinel.members),
        "needs_setup": sentinel.requires_setup,
    }


@router.patch("/sentinel")
async def patch_sentinel(guild: GuildDep, bot: BotDep, body: PatchSentinelBody) -> dict:
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    updates: dict[str, object] = {}
    for key, value in data.items():
        if key == "bypass_action" and value not in ("ban", "kick"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bypass_action must be ban or kick")
        if key == "rate" and not re.match(r"^\d+/\d+$", value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rate must be in the format X/Y")
        updates[key] = value

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    await bot.db.guilds.upsert_sentinel(guild.id, updates)
    return {"ok": True}


@router.post("/sentinel/message")
async def send_sentinel_message(guild: GuildDep, bot: BotDep, body: SendSentinelMessageBody) -> dict:
    from app.cogs.moderation.sentinel import (
        SENTINEL_DEFAULT_MESSAGE_BODY,
        SENTINEL_DEFAULT_MESSAGE_TITLE,
        SentinelVerifyView,
    )

    channel_id = int(body.channel_id)
    title = body.title or SENTINEL_DEFAULT_MESSAGE_TITLE
    content = body.content or SENTINEL_DEFAULT_MESSAGE_BODY

    channel = guild.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="channel not found")
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="channel must be a text channel")

    config = await bot.db.get_guild_config(guild.id)
    sentinel = await bot.db.get_guild_sentinel(guild.id)

    view = SentinelVerifyView(config, sentinel, title=title, body=content)
    try:
        message = await channel.send(view=view)
    except discord.HTTPException as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"failed to send message: {e}")

    await bot.db.guilds.upsert_sentinel(guild.id, {"message_id": message.id, "channel_id": channel_id})
    return {"ok": True, "message_id": message.id}


@router.post("/sentinel/toggle")
async def toggle_sentinel(guild: GuildDep, bot: BotDep, body: ToggleSentinelBody) -> dict:
    sentinel = await bot.db.get_guild_sentinel(guild.id)

    if body.enabled:
        if sentinel is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sentinel has not been configured")
        if sentinel.requires_setup:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sentinel requires setup (channel, role, and message must be set)",
            )
        if sentinel.started_at is not None:
            return {"ok": True, "status": "already_enabled"}
        await sentinel.enable()
    else:
        if sentinel is None:
            return {"ok": True, "status": "not_configured"}
        if sentinel.started_at is None:
            return {"ok": True, "status": "already_disabled"}
        await sentinel.disable()

    return {"ok": True, "status": "enabled" if body.enabled else "disabled"}
