"""Percy's AutoMod presets, for the dashboard.

Writes go through ``save_preset`` in the AutoMod cog module -- the same function the ``automod``
view's Save button calls -- so a rule built here is byte-for-byte the rule the command builds.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import BotDep, GuildDep, verify_token

if TYPE_CHECKING:
    from app.cogs.automation.automod import AutoModRulePreset

router = APIRouter(prefix="/guilds/{guild_id}", tags=["AutoMod"], dependencies=[Depends(verify_token)])

MISSING_PERMISSION = "Percy needs the Manage Server permission to change AutoMod rules."
MAX_KEYWORDS = 100
MAX_KEYWORD_LENGTH = 60

DESCRIPTIONS = {
    "links": "Blocks messages containing links. Image links and Tenor GIFs are still allowed.",
    "capital_spam": "Blocks messages written entirely in capital letters.",
    "invites_spam": "Blocks Discord server invite links.",
    "bad_words": "Blocks messages containing any of the keywords you list.",
}


class PutPresetBody(BaseModel):
    enabled: bool
    # Discord's own caps on a rule's exemptions.
    exempt_role_ids: list[int] = Field(default_factory=list, max_length=20)
    exempt_channel_ids: list[int] = Field(default_factory=list, max_length=50)
    keywords: list[str] | None = None


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _takes_keywords(preset: AutoModRulePreset) -> bool:
    """Keyword presets match a word list; the regex presets (links, caps, invites) do not."""
    trigger = preset["trigger"]
    return trigger.type == discord.AutoModRuleTriggerType.keyword and not trigger.regex_patterns


def _action_text(action: discord.AutoModRuleAction) -> str:
    match action.type:
        case discord.AutoModRuleActionType.block_message:
            return "Block the message"
        case discord.AutoModRuleActionType.send_alert_message:
            return f"Send an alert to <#{action.channel_id}>" if action.channel_id else "Send an alert"
        case discord.AutoModRuleActionType.timeout:
            seconds = int(action.duration.total_seconds()) if action.duration else 0
            return f"Time the member out for {seconds} seconds"
        case discord.AutoModRuleActionType.block_member_interactions:
            return "Block the member's interactions"
    return action.type.name.replace("_", " ").capitalize()


def _can_manage(guild: discord.Guild) -> bool:
    return guild.me.guild_permissions.manage_guild


async def _load(bot: Any, guild: discord.Guild, key: str) -> AutoModRulePreset:
    """The preset under ``key``, merged with Percy's existing Discord rule of the same name."""
    from app.cogs.automation.automod import AutoModPresets

    preset = AutoModPresets.fresh(key)
    if preset is None:
        raise _bad(f"Unknown AutoMod preset {key!r}.")
    return _merge(preset, (await _percy_rules(bot, guild)).get(preset["name"]))


def _merge(preset: AutoModRulePreset, rule: discord.AutoModRule | None) -> AutoModRulePreset:
    """Overlay the live Discord rule (if any) onto the preset's defaults."""
    if rule is not None:
        preset.update({
            "id": rule.id, "trigger": rule.trigger, "actions": rule.actions, "enabled": rule.enabled,
            "exempt_roles": [discord.Object(id=i) for i in rule.exempt_role_ids],
            "exempt_channels": [discord.Object(id=i) for i in rule.exempt_channel_ids],
        })
    return preset


async def _percy_rules(bot: Any, guild: discord.Guild) -> dict[str, discord.AutoModRule]:
    rules = await guild.fetch_automod_rules()
    return {rule.name: rule for rule in rules if rule.creator_id == bot.user.id}


def _serialize(key: str, preset: AutoModRulePreset) -> dict:
    return {
        "key": key,
        "name": preset["name"],
        "description": DESCRIPTIONS.get(key, ""),
        "exists": bool(preset["id"]),
        "enabled": preset["enabled"],
        "actions": [_action_text(a) for a in preset["actions"]],
        "exempt_role_ids": [str(r.id) for r in preset.get("exempt_roles") or []],
        "exempt_channel_ids": [str(c.id) for c in preset.get("exempt_channels") or []],
        "keywords": list(preset["trigger"].keyword_filter) if _takes_keywords(preset) else None,
    }


@router.get("/automod")
async def get_automod(guild: GuildDep, bot: BotDep) -> dict:
    """Every preset Percy offers, with the state of its Discord rule if one exists."""
    from app.cogs.automation.automod import AutoModPresets

    missing = not _can_manage(guild)
    rules: dict[str, discord.AutoModRule] = {}
    if not missing:
        try:
            rules = await _percy_rules(bot, guild)
        except discord.Forbidden:
            missing = True

    presets = []
    for key in AutoModPresets.KEYS:
        preset = AutoModPresets.fresh(key)
        assert preset is not None
        presets.append(_serialize(key, _merge(preset, rules.get(preset["name"]))))
    return {"missing_permission": missing, "presets": presets}


@router.put("/automod/{preset}")
async def put_automod_preset(guild: GuildDep, bot: BotDep, preset: str, body: PutPresetBody) -> dict:
    """Create or edit the Discord rule for one preset."""
    from app.cogs.automation import automod

    if preset not in automod.AutoModPresets.KEYS:
        raise _bad(f"Unknown AutoMod preset {preset!r}.")
    if not _can_manage(guild):
        raise _bad(MISSING_PERMISSION)

    roles = []
    for role_id in dict.fromkeys(body.exempt_role_ids):
        role = guild.get_role(role_id)
        if role is None:
            raise _bad(f"Role {role_id} is not in this server.")
        roles.append(role)
    channels = []
    for channel_id in dict.fromkeys(body.exempt_channel_ids):
        channel = guild.get_channel(channel_id)
        if channel is None:
            raise _bad(f"Channel {channel_id} is not in this server.")
        channels.append(channel)

    try:
        current = await _load(bot, guild, preset)
    except discord.Forbidden:
        raise _bad(MISSING_PERMISSION) from None

    if body.keywords is not None:
        if not _takes_keywords(current):
            raise _bad(f"{current['name']} does not use a keyword list.")
        keywords = list(dict.fromkeys(kw.strip() for kw in body.keywords if kw.strip()))
        if len(keywords) > MAX_KEYWORDS:
            raise _bad(f"You can add up to {MAX_KEYWORDS} keywords; you sent {len(keywords)}.")
        too_long = next((kw for kw in keywords if len(kw) > MAX_KEYWORD_LENGTH), None)
        if too_long is not None:
            raise _bad(f"Keywords can be at most {MAX_KEYWORD_LENGTH} characters: {too_long[:20]!r}... is longer.")
        current["trigger"].keyword_filter = keywords

    current["enabled"] = body.enabled
    current["exempt_roles"] = roles
    current["exempt_channels"] = channels

    try:
        await automod.save_preset(bot, guild.id, current, actor="the Percy dashboard")
    except discord.Forbidden:
        raise _bad(MISSING_PERMISSION) from None
    except discord.HTTPException as exc:
        raise _bad(f"Discord rejected the rule: {exc.text or exc}") from None
    return {"ok": True, **_serialize(preset, current)}
