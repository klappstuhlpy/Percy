"""Guild configuration, roles, channels, and sentinel endpoints."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

import discord
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import config
from app.services import ModelTier, build_dashboard_assistant_system
from app.services.guild_health import ChannelState, FeatureFacts, RoleState, summarize
from app.utils import truncate
from app.utils.constants import MAX_MENTION_COUNT, MIN_MENTION_COUNT
from app.utils.webhooks import send_webhook

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import parse_id, resolve_channel, resolve_entity, resolve_role

if TYPE_CHECKING:
    from app.core import Bot
    from app.database.base import Sentinel

log = logging.getLogger(__name__)

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


class AIAskBody(BaseModel):
    # A free-form natural-language question from the dashboard command palette.
    question: str = Field(max_length=2000)

    model_config = {"extra": "ignore"}


class BatchOperation(BaseModel):
    type: str
    data: dict = {}


class BatchGuildConfigBody(BaseModel):
    operations: list[BatchOperation] = Field(max_length=50)


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
    title: str | None = Field(default=None, max_length=256)
    content: str | None = Field(default=None, max_length=2000)


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
    webhook here (mirroring the in-bot setup command). The old webhook is deleted only once
    the new one exists, so a failed create leaves logging working; if the create fails at the
    channel's webhook limit, the old one is deleted to free a slot and the create retried once.
    Returns the new webhook url, or ``None`` when the channel is being cleared.
    """

    async def delete_old() -> None:
        if old_url:
            with contextlib.suppress(discord.HTTPException, ValueError):
                await discord.Webhook.from_url(old_url, session=bot.session).delete()

    if channel_id is None:
        await delete_old()
        return None

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"channel {channel_id} is not a text channel")

    avatar = await bot.user.avatar.read() if bot.user and bot.user.avatar else None
    try:
        try:
            webhook = await channel.create_webhook(name=name, avatar=avatar)
        except discord.HTTPException as exc:
            if not old_url or isinstance(exc, discord.Forbidden):
                raise
            await delete_old()
            old_url = None
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
    await delete_old()
    return webhook.url


def _check_mention_count(value: object) -> int | None:
    """``None``/``0`` turns mention-spam protection off; anything else must be in the command's range."""
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or (value != 0 and not MIN_MENTION_COUNT <= value <= MAX_MENTION_COUNT)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"mention_count must be 0 (off) or between {MIN_MENTION_COUNT} and {MAX_MENTION_COUNT}",
        )
    return value


_CHANNEL_FIELDS = frozenset(f for f in _ALLOWED_CONFIG_FIELDS if f.endswith("_channel_id"))


def _check_target(guild: discord.Guild, guild_config, key: str, value: int) -> None:
    """A channel/role id from the body must belong to *this* guild (and a mute role be one Percy can give).

    An unchanged value is let through: the dashboard resends every field, and a role moved above
    Percy since must not make saving an unrelated setting fail.
    """
    if value == getattr(guild_config, key, None):
        return
    if key in _CHANNEL_FIELDS:
        if not isinstance(guild.get_channel(value), discord.abc.Messageable):
            raise _bad_request(f"{key} must be a channel of this server")
        return
    role = guild.get_role(value)
    if role is None:
        raise _bad_request(f"{key} must be a role of this server")
    if key == "mute_role_id" and (role.is_default() or role.managed or role >= guild.me.top_role):
        raise _bad_request("I cannot assign that role: pick one below my highest role that no integration manages")


def _build_config_updates(data: dict, guild_config, guild: discord.Guild | None = None) -> dict[str, object]:
    """Extract valid config updates from a flat dict (shared by PATCH and batch).

    With ``guild`` the channel/role ids are checked to belong to it (backup restore passes none: ids are stripped).
    """
    updates: dict[str, object] = {}
    for key, value in data.items():
        if key == "mention_count":
            updates[key] = _check_mention_count(value)
        elif key == "use_music_panel":
            if not isinstance(value, bool):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="use_music_panel must be a boolean")
            updates[key] = value
        elif key in _ALLOWED_CONFIG_FIELDS:
            # /batch hands over an untyped dict, so a string here would otherwise reach asyncpg as a 500.
            updates[key] = None if value is None else parse_id(value, key)
            if guild is not None and updates[key] is not None:
                _check_target(guild, guild_config, key, updates[key])  # type: ignore[arg-type]
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
            # Same limits as the prefix command; an empty prefix would make every message a command.
            if len(value) > 25 or not all(isinstance(p, str) and p.strip() and len(p) <= 100 for p in value):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="prefixes must be at most 25 non-empty strings of up to 100 characters",
                )
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
@router.patch("/config", include_in_schema=False)  # legacy alias; the BFF's patch_guild_config still targets it
async def patch_guild_config(guild: GuildDep, bot: BotDep, body: PatchGuildConfigBody) -> dict:
    # exclude_unset (not exclude_none): a client-sent ``null`` is a deliberate "clear", which
    # we must honour (e.g. clearing an alert channel) — exclude_none would silently drop it.
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    guild_config = await bot.db.get_guild_config(guild.id)
    updates = _build_config_updates(data, guild_config, guild)

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no valid fields to update")

    await _apply_config_updates(bot, guild, guild_config, updates)
    return {"ok": True}


async def _apply_config_updates(bot, guild, guild_config, updates: dict[str, object]) -> None:
    """Store config updates, creating/deleting the webhooks the alert and audit-log channels need.

    Alert / audit-log channels only work with a backing webhook (send_alert / audit logging
    use the webhook, not the channel id). The dashboard sends just a channel id, so create
    or delete the webhook here. Skip if the channel is unchanged and already has a webhook.
    """
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
            bot, guild, getattr(guild_config, url_field, None), new_channel_id, hook_name  # type: ignore[arg-type]
        )
    await guild_config.update(**updates)


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
    if guild.get_channel_or_thread(channel_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel not found in this server")
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


#: Backtick-wrapped snippets in an answer are the model's command suggestions (mirrors the
#: in-Discord assistant). The dashboard turns each resolved command into an informational chip.
_BACKTICK_RE = re.compile(r"`([^`\n]{1,60})`")
#: How many top-level commands to feed the model and how many suggestions to surface.
_MAX_CATALOGUE = 80
_MAX_SUGGESTIONS = 5
#: Free-form replies generate many tokens; bound them so CPU-bound inference falls back
#: instead of hanging the request (mirrors the assistant cog's budget).
_ASK_MAX_TOKENS = 400


def _command_catalogue(bot) -> list[tuple[str, str]]:
    """Visible top-level commands as ``(qualified_name, short_description)`` pairs.

    Injected into the system prompt so the model recommends real commands instead of inventing
    them. Mirrors the in-Discord assistant's catalogue (top-level only; groups cover subcommands).
    """
    seen: set[str] = set()
    catalogue: list[tuple[str, str]] = []
    for cmd in bot.commands:
        if cmd.hidden or not cmd.enabled or cmd.qualified_name in seen:
            continue
        seen.add(cmd.qualified_name)
        desc = (cmd.description or cmd.short_doc or "").strip().split("\n", 1)[0]
        catalogue.append((cmd.qualified_name, truncate(desc, 70)))
    catalogue.sort(key=lambda pair: pair[0])
    return catalogue[:_MAX_CATALOGUE]


def _extract_suggestions(bot, answer: str, prefix: str) -> list[dict]:
    """Resolve backtick-wrapped command names in ``answer`` to real, invokable commands.

    Returns ``[{"label": "<prefix><name>", "command": "<name>"}]`` for each distinct visible
    command the answer names, capped at :data:`_MAX_SUGGESTIONS`. Tokens that do not resolve to
    a real command are ignored, so the model can never surface an invented command.
    """
    seen: set[str] = set()
    suggestions: list[dict] = []
    for match in _BACKTICK_RE.finditer(answer):
        name = match.group(1).strip()
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
        # Keep the bare command path: drop any trailing arguments the model wrote.
        name = name.strip(".,!?:;()[]<>").strip()
        if not name:
            continue
        cmd = bot.get_command(name) or bot.get_command(name.lower())
        if cmd is None or cmd.hidden or not cmd.enabled or cmd.qualified_name in seen:
            continue
        seen.add(cmd.qualified_name)
        suggestions.append({"label": f"{prefix}{cmd.qualified_name}", "command": cmd.qualified_name})
        if len(suggestions) >= _MAX_SUGGESTIONS:
            break
    return suggestions


@router.post("/ai/ask")
async def ai_ask(guild: GuildDep, bot: BotDep, body: AIAskBody) -> dict:
    """Answer a dashboard command-palette question with Percy's AI assistant.

    Gated like every other AI behaviour: the guild must have the ``assistant`` AI feature
    enabled *and* the engine must be reachable. When either is false this returns
    ``{"available": false, ...}`` (never an error) so the palette degrades gracefully to its
    navigation/action results. The answer is grounded in the real command catalogue, and any
    command it names is resolved and returned as a suggestion (invented commands are dropped).
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="question must not be empty")

    ai_config = await bot.db.get_guild_ai_config(guild.id)
    if not bot.ai.available or not ai_config.is_enabled("assistant"):
        reason = (
            "The AI assistant is not enabled for this server."
            if bot.ai.available
            else "The AI assistant is currently unavailable."
        )
        return {"available": False, "reason": reason, "answer": None, "suggestions": []}

    guild_config = await bot.db.get_guild_config(guild.id)
    prefix = next(iter(guild_config.prefixes), None) or _default_prefix()

    system = build_dashboard_assistant_system(
        server_name=guild.name,
        prefix=prefix,
        website=config.website,
        command_catalogue=_command_catalogue(bot),
    )
    # User text is untrusted data, never an instruction (see prompts.py security model).
    convo = [{"role": "system", "content": system}, {"role": "user", "content": truncate(question, 1000)}]

    answer = await bot.ai.complete(
        convo,
        tier=ModelTier.SMART,
        temperature=0.4,
        timeout=config.ollama.chat_timeout,
        max_tokens=_ASK_MAX_TOKENS,
    )
    if answer is None:
        return {"available": False, "reason": "The AI assistant timed out. Please try again.", "answer": None, "suggestions": []}

    return {
        "available": True,
        "answer": answer.strip(),
        "suggestions": _extract_suggestions(bot, answer, prefix),
    }


def _default_prefix() -> str:
    """The first configured global prefix, or ``?`` — used when a guild has no custom prefix."""
    default = config.default_prefix
    if isinstance(default, str):
        return default
    return next(iter(default), "?")


@router.post("/batch")
async def batch_guild_config(guild: GuildDep, bot: BotDep, body: BatchGuildConfigBody) -> dict:
    if not body.operations:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="operations must be a non-empty array")

    # A bad config value answers 400 before anything is applied, not after earlier operations were.
    if any(op.type == "config" for op in body.operations):
        guild_config = await bot.db.get_guild_config(guild.id)
        for op in body.operations:
            if op.type == "config":
                _build_config_updates(op.data, guild_config, guild)

    results: list[dict] = []

    for op in body.operations:
        op_type = op.type
        data = op.data

        if op_type == "config":
            guild_config = await bot.db.get_guild_config(guild.id)
            updates = _build_config_updates(data, guild_config, guild)
            if updates:
                # Same path as PATCH, so a log/alert channel saved here also gets its webhook.
                await _apply_config_updates(bot, guild, guild_config, updates)
            results.append({"type": "config", "ok": True})

        elif op_type == "sentinel":
            updates = {k: v for k, v in data.items() if k in _SENTINEL_FIELDS}
            try:
                applied = await _apply_sentinel_settings(guild, bot, updates) if updates else {}
            except HTTPException as e:
                # Reported like sentinel_toggle, so the ops already applied are not answered with a 400.
                results.append({"type": "sentinel", "ok": False, "error": e.detail})
                continue
            results.append({"type": "sentinel", "ok": True, "warnings": applied.get("warnings", [])})

        elif op_type == "sentinel_toggle":
            enabled = data.get("enabled")
            sentinel = await bot.db.get_guild_sentinel(guild.id)
            if enabled and sentinel and not sentinel.requires_setup and sentinel.started_at is None:
                # Best effort, as before: a flags save must not fail because Sentinel cannot start.
                problem = await _sentinel_start_problem(guild, bot, sentinel)
                if problem is not None:
                    results.append({"type": "sentinel_toggle", "ok": False, "error": problem})
                    continue
                await sentinel.enable()
            elif not enabled and sentinel and sentinel.started_at is not None:
                await sentinel.disable()
            results.append({"type": "sentinel_toggle", "ok": True})

        elif op_type == "audit_log_flags":
            current_flags = dict((await bot.db.get_guild_config(guild.id)).audit_log_flags or {})
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

    entity_id = parse_id(body.entity_id, "entity_id")

    if body.action == "add":
        # Only this guild's channels, roles and members; removal stays open so stale ids can be cleared.
        if guild.get_channel_or_thread(entity_id) is None and guild.get_role(entity_id) is None:
            try:
                guild.get_member(entity_id) or await guild.fetch_member(entity_id)
            except discord.HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=f"{entity_id} is not a channel, role or member here"
                ) from None
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

    current_flags = dict((await bot.db.get_guild_config(guild.id)).audit_log_flags or {})

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
            # Percy can give it out: below its top role and not owned by an integration.
            "assignable": role < guild.me.top_role and not role.managed and not role.is_default(),
        }
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)
    ]


def _can_post(perms: discord.Permissions) -> bool:
    return perms.view_channel and perms.send_messages and perms.embed_links


@router.get("/channels")
async def get_guild_channels(guild: GuildDep) -> list[dict]:
    return [
        {
            "id": str(ch.id),
            "name": ch.name,
            "type": str(ch.type),
            "position": ch.position,
            "category_id": str(ch.category_id) if ch.category_id else None,
            # Percy can post an embed there right now (what every logging/announce feature needs).
            "can_post": _can_post(ch.permissions_for(guild.me)),
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


_SENTINEL_FIELDS = frozenset({"channel_id", "role_id", "starter_role_id", "bypass_action", "rate"})
_RATE_RE = re.compile(r"^(\d+)/(\d+)$")
# Permission syncs run after the response: a large server takes minutes under Discord's rate limits.
_background_syncs: set[asyncio.Task[None]] = set()


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _snowflake_or_none(value: object, field: str) -> int | None:
    """``None`` clears the field; anything else must be a Discord id (int or numeric string)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _bad_request(f"{field} must be a Discord id or null")
    try:
        parsed = int(value)
    except ValueError:
        raise _bad_request(f"{field} must be a Discord id or null") from None
    if parsed <= 0:
        raise _bad_request(f"{field} must be a Discord id or null")
    return parsed


def _parse_sentinel_rate(value: object) -> tuple[int, int] | None:
    """``"joins/seconds"``, both above zero, as the menu's modal accepts; ``None`` removes it."""
    if value is None:
        return None
    match = _RATE_RE.match(value) if isinstance(value, str) else None
    if match is None or int(match[1]) <= 0 or int(match[2]) <= 0:
        raise _bad_request("rate must be JOINS/SECONDS with both numbers above 0, e.g. 5/10")
    return int(match[1]), int(match[2])


def _check_sentinel_channel(guild: discord.Guild, channel_id: int) -> discord.TextChannel:
    channel = guild.get_channel(channel_id)
    if channel is None:
        raise _bad_request("channel not found")
    if not isinstance(channel, discord.TextChannel):
        raise _bad_request("channel must be a text channel")
    perms = channel.permissions_for(guild.me)
    if not perms.send_messages or not perms.embed_links:
        raise _bad_request(f"I need Send Messages and Embed Links in #{channel.name}")
    return channel


def _check_sentinel_role(guild: discord.Guild, role_id: int, *, other: int | None) -> None:
    from app.cogs.moderation.sentinel import sentinel_role_problem

    role = guild.get_role(role_id)
    if role is None:
        raise _bad_request("role not found")
    problem = sentinel_role_problem(role, other=other)
    if problem is not None:
        raise _bad_request(problem)


def _check_lockdown_role_unworn(guild: discord.Guild, role_id: int, held: set[int]) -> None:
    """The dashboard syncs the lockdown role without asking, so it must not be a role anyone
    outside Sentinel's hold already wears, or they would lose every channel."""
    if not guild.chunked:
        # ``role.members`` only knows cached members: an incomplete cache would wave the role through.
        raise _bad_request(
            "I have not loaded this server's member list yet, so I cannot check who already has that role. "
            "Try again in a minute, or set it up from the Discord menu, which asks before syncing."
        )
    role = guild.get_role(role_id)
    holders = sum(1 for member in role.members if member.id not in held) if role is not None else 0
    if holders:
        raise _bad_request(
            f"{holders} member(s) already have {role.name}, and the lockdown role is hidden from every channel. "
            "Pick a role only Sentinel uses, or set it up from the Discord menu, which asks before syncing."
        )


def _stale_role_warning(guild: discord.Guild, role_id: int, *, other: int | None, what: str) -> list[str]:
    """An unchanged role is not re-validated (saving the rate must not fail on it), only reported."""
    from app.cogs.moderation.sentinel import sentinel_role_problem

    role = guild.get_role(role_id)
    if role is None:
        return [f"The Sentinel {what} no longer exists. Pick another one."]
    if what == "lockdown role" and not guild.me.guild_permissions.manage_roles:
        return ["I need Manage Roles to give and take the Sentinel lockdown role."]
    problem = sentinel_role_problem(role, other=other)
    return [] if problem is None else [f"The Sentinel {what} needs attention: {problem}"]


async def _get_or_create_sentinel(guild: discord.Guild, bot: Bot) -> Sentinel:
    sentinel = await bot.db.get_guild_sentinel(guild.id)
    if sentinel is None:
        await bot.db.guilds.upsert_sentinel(guild.id, {})
        sentinel = await bot.db.get_guild_sentinel(guild.id)
    assert sentinel is not None
    return sentinel


def _schedule_permission_sync(sentinel: Sentinel, guild: discord.Guild) -> bool:
    """Hide the server from the lockdown role and open the verification channel, as the menu does."""
    from app.cogs.moderation.sentinel import sync_sentinel_permissions

    if sentinel.role is None:
        return False
    async def run() -> None:
        try:
            await sync_sentinel_permissions(sentinel, guild.me)
        except Exception:
            log.exception("Sentinel permission sync failed in guild %s", guild.id)

    task = asyncio.create_task(run())
    _background_syncs.add(task)
    task.add_done_callback(_background_syncs.discard)
    return True


async def _apply_sentinel_settings(guild: discord.Guild, bot: Bot, data: dict) -> dict:
    """Validate and apply a dashboard sentinel edit with the same rules as the setup menu.

    Keys absent from ``data`` are left alone; an explicit ``None`` clears the field. Changes go
    through :meth:`Sentinel.edit` on the cached instance, so a role change strips the old role
    from held members instead of leaving it on them.
    """
    sentinel = await _get_or_create_sentinel(guild, bot)
    edits: dict[str, Any] = {}

    if "bypass_action" in data:
        if data["bypass_action"] not in ("ban", "kick"):
            raise _bad_request("bypass_action must be ban or kick")
        edits["bypass_action"] = data["bypass_action"]
    if "rate" in data:
        edits["rate"] = _parse_sentinel_rate(data["rate"])
    for key in ("channel_id", "role_id", "starter_role_id"):
        if key in data:
            edits[key] = _snowflake_or_none(data[key], key)

    # The dashboard sends every field on each save, so only a *different* value is a change.
    changed = {key for key in ("channel_id", "role_id") if key in edits and edits[key] != getattr(sentinel, key)}
    if changed and sentinel.started_at is not None:
        what = " and ".join("lockdown role" if key == "role_id" else "verification channel" for key in sorted(changed))
        raise _bad_request(f"stop Sentinel before changing its {what}")

    if "channel_id" in changed and edits["channel_id"] is not None:
        _check_sentinel_channel(guild, edits["channel_id"])

    role_id = edits.get("role_id", sentinel.role_id)
    starter_role_id = edits.get("starter_role_id", sentinel.starter_role_id)
    # Only a changed role is validated: a role dragged above Percy since must not make saving the
    # rate or bypass action fail. A problem with an unchanged role is a warning instead.
    role_warnings: list[str] = []
    if "role_id" in changed and role_id is not None:
        if not guild.me.guild_permissions.manage_roles:
            raise _bad_request("I need Manage Roles to use a lockdown role")
        _check_sentinel_role(guild, role_id, other=starter_role_id)
        _check_lockdown_role_unworn(guild, role_id, sentinel.members)
    elif "role_id" in edits and role_id is not None:
        role_warnings += _stale_role_warning(guild, role_id, other=starter_role_id, what="lockdown role")
    starter_changed = "starter_role_id" in edits and starter_role_id != sentinel.starter_role_id
    if starter_changed and starter_role_id is not None:
        _check_sentinel_role(guild, starter_role_id, other=role_id)
    elif "starter_role_id" in edits and starter_role_id is not None:
        role_warnings += _stale_role_warning(guild, starter_role_id, other=role_id, what="starter role")

    old_card = sentinel.message if "channel_id" in changed else None
    if old_card is not None:
        # The card lives in the old channel; the dashboard posts a new one in the new channel.
        edits["message_id"] = None

    await sentinel.edit(**edits)
    if old_card is not None:
        with contextlib.suppress(discord.HTTPException):
            await old_card.delete()

    synced = bool(changed) and _schedule_permission_sync(sentinel, guild)
    warnings = role_warnings + _verification_warnings(guild, edits.get("channel_id", sentinel.channel_id))
    return {"permission_sync": synced, "warnings": warnings}


def _verification_warnings(guild: discord.Guild, channel_id: int | None) -> list[str]:
    """What the dashboard sync deliberately leaves to a human: hiding the verification channel
    from @everyone. Done unasked, picking #general by mistake would hide it from the server."""
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None or not channel.permissions_for(guild.default_role).read_messages:
        return []
    return [
        f"#{channel.name} is still visible to @everyone. Hide it from @everyone, or run Sync Permissions "
        "in the Discord sentinel menu, so only members waiting to verify see it."
    ]


async def _sentinel_start_problem(guild: discord.Guild, bot: Bot, sentinel: Sentinel) -> str | None:
    """Why Sentinel cannot be switched on right now, or ``None``. Mirrors what the listeners need."""
    from app.cogs.moderation.sentinel import sentinel_role_problem

    config = await bot.db.get_guild_config(guild.id)
    if config is None or not config.flags.sentinel:
        return "turn on Sentinel protection for this server first; without it nobody is held"
    role = sentinel.role
    if role is None:
        return "the lockdown role no longer exists"
    return sentinel_role_problem(role, other=sentinel.starter_role_id)


@router.patch("/sentinel")
async def patch_sentinel(guild: GuildDep, bot: BotDep, body: PatchSentinelBody) -> dict:
    # ``exclude_unset``: an explicit null clears the rate or starter role, a missing key leaves it.
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise _bad_request("no valid fields to update")
    result = await _apply_sentinel_settings(guild, bot, data)
    return {"ok": True, **result}


@router.post("/sentinel/message")
async def send_sentinel_message(guild: GuildDep, bot: BotDep, body: SendSentinelMessageBody) -> dict:
    from app.cogs.moderation.sentinel import (
        SENTINEL_DEFAULT_MESSAGE_BODY,
        SENTINEL_DEFAULT_MESSAGE_TITLE,
        SentinelVerifyView,
    )

    channel_id = _snowflake_or_none(body.channel_id, "channel_id")
    if channel_id is None:
        raise _bad_request("channel_id is required")
    title = body.title or SENTINEL_DEFAULT_MESSAGE_TITLE
    content = body.content or SENTINEL_DEFAULT_MESSAGE_BODY

    sentinel = await _get_or_create_sentinel(guild, bot)
    moved = channel_id != sentinel.channel_id
    if moved and sentinel.started_at is not None:
        raise _bad_request("stop Sentinel before changing its verification channel")
    channel = _check_sentinel_channel(guild, channel_id)

    config = await bot.db.get_guild_config(guild.id)
    view = SentinelVerifyView(config, sentinel, title=title, body=content)
    try:
        message = await channel.send(view=view)
    except discord.HTTPException as e:
        log.warning("Sentinel card send failed in guild %s: %s", guild.id, e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="failed to send message") from None

    # Replace the card rather than orphan it. Point at the new one first, so deleting the old
    # one is not mistaken for someone removing the live card (which stands Sentinel down).
    old_card = sentinel.message
    await sentinel.edit(channel_id=channel_id, message_id=message.id)
    if old_card is not None:
        with contextlib.suppress(discord.HTTPException):
            await old_card.delete()

    synced = moved and _schedule_permission_sync(sentinel, guild)
    return {
        "ok": True,
        "message_id": message.id,
        "permission_sync": synced,
        "warnings": _verification_warnings(guild, channel_id),
    }


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
        problem = await _sentinel_start_problem(guild, bot, sentinel)
        if problem is not None:
            raise _bad_request(problem)
        await sentinel.enable()
    else:
        if sentinel is None:
            return {"ok": True, "status": "not_configured"}
        if sentinel.started_at is None:
            return {"ok": True, "status": "already_disabled"}
        await sentinel.disable()

    return {"ok": True, "status": "enabled" if body.enabled else "disabled"}


# ---------------------------------------------------------------------------
# Health: can Percy do what this server configured?
# ---------------------------------------------------------------------------

_POST = ('view_channel', 'send_messages', 'embed_links')


def _channel_state(guild: discord.Guild, purpose: str, channel_id: int, needs: tuple[str, ...] = _POST) -> ChannelState:
    channel = guild.get_channel(channel_id)
    if channel is None:
        return ChannelState(purpose, channel_id, None)
    perms = channel.permissions_for(guild.me)
    return ChannelState(purpose, channel_id, channel.name, tuple(p for p in needs if not getattr(perms, p)))


def _role_state(guild: discord.Guild, purpose: str, role_id: int) -> RoleState:
    role = guild.get_role(role_id)
    if role is None:
        return RoleState(purpose, role_id, None)
    return RoleState(purpose, role_id, role.name, role < guild.me.top_role and not role.managed)


def _guild_missing(guild: discord.Guild, *needs: str) -> tuple[str, ...]:
    perms = guild.me.guild_permissions
    return tuple(p for p in needs if not getattr(perms, p))


@router.get("/health")
async def get_guild_health(guild: GuildDep, bot: BotDep) -> dict:
    """Per feature: ``ok`` / ``attention`` / ``off``, with each problem in plain language.

    Read live from Discord on every call (never cached): the answer to "can Percy post there
    *now*" goes stale the moment someone edits a channel's permissions.
    """
    cfg = await bot.db.get_guild_config(guild.id)
    facts: list[FeatureFacts] = []

    audit = FeatureFacts('audit_log', 'Audit log', cfg.audit_log_channel_id is not None)
    if cfg.audit_log_channel_id:
        audit.channels.append(_channel_state(guild, 'audit log', cfg.audit_log_channel_id, ('view_channel', 'manage_webhooks')))
        audit.guild_missing = _guild_missing(guild, 'view_audit_log')
        if not cfg.audit_log_webhook_url:
            audit.notes.append('The audit log webhook is missing. Pick the channel again so Percy recreates it.')
    facts.append(audit)

    for key, label, attr in (
        ('mod_log', 'Moderation log', 'mod_log_channel_id'),
        ('message_log', 'Message log', 'message_log_channel_id'),
        ('voice_log', 'Voice log', 'voice_log_channel_id'),
        ('alerts', 'Alerts', 'alert_channel_id'),
        ('polls', 'Polls', 'poll_channel_id'),
    ):
        channel_id = getattr(cfg, attr, None)
        feature = FeatureFacts(key, label, channel_id is not None)
        if channel_id:
            feature.channels.append(_channel_state(guild, label.lower(), channel_id))
        facts.append(feature)

    mute = FeatureFacts('mute_role', 'Mute role', cfg.mute_role_id is not None)
    if cfg.mute_role_id:
        mute.roles.append(_role_state(guild, 'mute', cfg.mute_role_id))
        mute.guild_missing = _guild_missing(guild, 'manage_roles')
    facts.append(mute)

    level_record = await bot.db.leveling.get_guild_config_record(guild.id)
    leveling = FeatureFacts('leveling', 'Leveling', bool(level_record and level_record['enabled']))
    if level_record:
        channel = int(level_record.get('level_up_channel') or 1)
        if channel > 2:  # 0 = off, 1 = where they talked, 2 = DM
            leveling.channels.append(_channel_state(guild, 'level-up', channel, (*_POST, 'attach_files')))
        reward_roles = level_record.get('level_roles') or {}
        leveling.roles.extend(_role_state(guild, 'level reward', int(role_id)) for role_id in reward_roles)
        if reward_roles:
            leveling.guild_missing = _guild_missing(guild, 'manage_roles')
    facts.append(leveling)

    music = FeatureFacts('music_panel', 'Music panel', bool(cfg.use_music_panel and cfg.music_panel_channel_id))
    if cfg.music_panel_channel_id:
        music.channels.append(_channel_state(guild, 'music panel', cfg.music_panel_channel_id))
    facts.append(music)

    sentinel = await bot.db.get_guild_sentinel(guild.id)
    gate = FeatureFacts('sentinel', 'Sentinel verification', sentinel is not None and sentinel.started_at is not None)
    if sentinel is not None:
        if sentinel.requires_setup:
            gate.notes.append('Sentinel is on but not fully set up: it needs a channel, a role and its verify message.')
        if sentinel.channel_id:
            gate.channels.append(_channel_state(guild, 'verification', sentinel.channel_id, (*_POST, 'attach_files')))
        if sentinel.role_id:
            gate.roles.append(_role_state(guild, 'verified', sentinel.role_id))
        gate.guild_missing = _guild_missing(guild, 'manage_roles')
    facts.append(gate)

    return summarize(facts)


# ---------------------------------------------------------------------------
# Guided setup: one step instead of five settings
# ---------------------------------------------------------------------------

#: log key -> (config field, feature flag to switch on with it, or None)
_SETUP_LOGS = {
    "audit": ("audit_log_channel_id", "audit_log"),
    "mod": ("mod_log_channel_id", None),
    "message": ("message_log_channel_id", None),
    "voice": ("voice_log_channel_id", None),
    "alerts": ("alert_channel_id", "alerts"),
}
_LOG_CHANNEL_PERMS = discord.PermissionOverwrite(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, manage_webhooks=True
)


class SetupLoggingBody(BaseModel):
    #: An existing text channel, or ``None`` to have Percy create a private ``#percy-logs``.
    channel_id: int | None = None
    logs: list[str] = Field(max_length=len(_SETUP_LOGS))


@router.post("/setup/logging")
async def setup_logging(guild: GuildDep, bot: BotDep, body: SetupLoggingBody) -> dict:
    """Send the chosen logs to one channel, creating it (visible to admins only) when asked.

    Equivalent to picking the same channel in each logging setting and switching the audit
    log / alert features on -- in one step, with the webhooks created as part of it.
    """
    unknown = sorted(set(body.logs) - set(_SETUP_LOGS))
    if unknown or not body.logs:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"choose logs from: {', '.join(_SETUP_LOGS)}")

    created = False
    if body.channel_id is None:
        if not guild.me.guild_permissions.manage_channels:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Percy needs Manage Channels to create the log channel. Pick an existing channel instead.",
            )
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), guild.me: _LOG_CHANNEL_PERMS}
        try:
            channel = await guild.create_text_channel(
                "percy-logs", overwrites=overwrites, reason="Logging set up from the Percy dashboard"
            )
        except discord.HTTPException:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Discord refused to create the channel.") from None
        created = True
    else:
        channel = guild.get_channel(body.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a text channel.")
        missing = [p for p in ("view_channel", "send_messages", "embed_links", "manage_webhooks")
                   if not getattr(channel.permissions_for(guild.me), p)]
        if missing:
            names = ", ".join(p.replace("_", " ").title() for p in missing)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Percy is missing {names} in #{channel.name}.")

    guild_config = await bot.db.get_guild_config(guild.id)
    updates: dict[str, object] = {}
    flags = guild_config.flags.value
    for key in body.logs:
        field, flag = _SETUP_LOGS[key]
        updates[field] = channel.id
        if flag:
            flags |= _FLAG_MAP[flag]
    updates["flags"] = flags
    await _apply_config_updates(bot, guild, guild_config, updates)
    return {
        "ok": True,
        "channel": resolve_channel(guild, channel.id),
        "created": created,
        "message": f"{len(body.logs)} log{'s' if len(body.logs) != 1 else ''} now go to #{channel.name}.",
    }


class SetupSentinelBody(BaseModel):
    #: An existing text channel / role, or ``None`` to have Percy create ``#verify`` / ``Unverified``.
    channel_id: int | None = None
    role_id: int | None = None
    start: bool = False


@router.post("/setup/sentinel")
async def setup_sentinel(guild: GuildDep, bot: BotDep, body: SetupSentinelBody) -> dict:
    """Verification channel, lockdown role and verify message in one step, optionally started.

    The same paths as the Sentinel tab: ``_apply_sentinel_settings`` (validation + permission
    sync), ``send_sentinel_message`` (the card) and ``_sentinel_start_problem`` (starting).
    """
    perms = guild.me.guild_permissions
    if not perms.manage_roles:
        raise _bad_request("Percy needs Manage Roles to give and take the Sentinel lockdown role.")
    if body.channel_id is None and not perms.manage_channels:
        raise _bad_request("Percy needs Manage Channels to create #verify. Pick an existing channel instead.")

    sentinel = await _get_or_create_sentinel(guild, bot)
    if sentinel.started_at is not None:
        raise _bad_request("Sentinel is already running. Stop it before setting it up again.")
    # Check the picked channel/role before creating anything, so a bad pick leaves nothing behind.
    if body.channel_id is not None:
        _check_sentinel_channel(guild, body.channel_id)
    if body.role_id is not None:
        _check_sentinel_role(guild, body.role_id, other=sentinel.starter_role_id)
    # Also run for a role still to be created: its "member list not loaded" refusal would
    # otherwise only come from ``_apply_sentinel_settings``, after the role and channel exist.
    _check_lockdown_role_unworn(guild, body.role_id or 0, sentinel.members)

    reason = "Sentinel set up from the Percy dashboard"
    role_id = body.role_id
    if role_id is None:
        try:
            role_id = (await guild.create_role(name="Unverified", reason=reason)).id
        except discord.HTTPException:
            raise _bad_request("Discord refused to create the Unverified role.") from None
    channel_id = body.channel_id
    if channel_id is None:
        # Percy made this channel, so hiding it from @everyone cannot hide a busy channel by mistake.
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
        }
        try:
            channel_id = (await guild.create_text_channel("verify", overwrites=overwrites, reason=reason)).id
        except discord.HTTPException:
            raise _bad_request("Discord refused to create the #verify channel.") from None

    applied = await _apply_sentinel_settings(guild, bot, {"channel_id": channel_id, "role_id": role_id})
    posted = await send_sentinel_message(guild, bot, SendSentinelMessageBody(channel_id=channel_id))
    bot.db.get_guild_sentinel.invalidate(guild.id)

    channel, role = guild.get_channel(channel_id), guild.get_role(role_id)
    done = (
        f"Sentinel is set up: members wait in #{channel.name if channel else channel_id} "
        f"with the {role.name if role else role_id} role, and the verify message is posted."
    )
    warnings = list(dict.fromkeys(applied["warnings"] + posted["warnings"]))
    # Warnings ride in the message too: the dashboard shows only ``message``.
    note = "".join(f" {w}" for w in warnings)
    if not body.start:
        return {"ok": True, "message": done + note, "started": False, "warnings": warnings}

    guild_config = await bot.db.get_guild_config(guild.id)
    if not guild_config.flags.sentinel:
        await _apply_config_updates(bot, guild, guild_config, {"flags": guild_config.flags.value | _FLAG_MAP["sentinel"]})
    sentinel = await _get_or_create_sentinel(guild, bot)
    problem = await _sentinel_start_problem(guild, bot, sentinel)
    if problem is not None:
        raise _bad_request(f"{done} It could not start yet: {problem}.")
    await sentinel.enable()
    return {"ok": True, "message": f"{done} New members are now held until they verify.{note}", "started": True, "warnings": warnings}


_TEST_FEATURES = {
    "audit_log": ("the audit log", None),
    "alerts": ("alerts", None),
    "mod_log": ("the mod log", "mod_log_channel_id"),
    "message_log": ("the message log", "message_log_channel_id"),
    "voice_log": ("the voice log", "voice_log_channel_id"),
    "polls": ("polls", "poll_channel_id"),
}


class TestMessageBody(BaseModel):
    feature: str


@router.post("/test-message")
async def send_test_message(guild: GuildDep, bot: BotDep, body: TestMessageBody) -> dict:
    """Post one harmless embed to a feature's saved destination, so the user can see where it lands."""
    if body.feature not in _TEST_FEATURES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"choose a feature from: {', '.join(_TEST_FEATURES)}")
    label, field = _TEST_FEATURES[body.feature]
    guild_config = await bot.db.get_guild_config(guild.id)
    embed = discord.Embed(
        title="Test message from the dashboard",
        description=f"This is a test for {label}. Nothing happened; it only shows where those messages will appear.",
        colour=discord.Colour.blurple(),
    )

    def refuse(detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    if field is None:
        webhook = guild_config.audit_log_webhook if body.feature == "audit_log" else guild_config.alert_webhook
        if webhook is None:
            raise refuse(f"No channel is set for {label}.")
        try:
            await send_webhook(webhook, embed=embed)
        except (discord.Forbidden, discord.NotFound):
            raise refuse(f"The webhook for {label} no longer works. Set the channel again.") from None
        except discord.HTTPException:
            raise refuse("Discord refused the test message.") from None
        return {"ok": True, "message": f"Test message sent for {label}."}

    channel_id = getattr(guild_config, field, None)
    if not channel_id:
        raise refuse(f"No channel is set for {label}.")
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise refuse(f"The channel for {label} no longer exists. Pick another one.")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        raise refuse(f"Percy cannot post in #{channel.name}.") from None
    except discord.HTTPException:
        raise refuse("Discord refused the test message.") from None
    return {"ok": True, "message": f"Test message sent to #{channel.name}."}
