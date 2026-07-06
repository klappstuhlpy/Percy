"""Internal API endpoints for config backup / restore and shareable templates.

Export produces a portable JSON snapshot of a guild's ID-free configuration (portable
guild scalars, autoresponders, tags); import applies one back — into the same guild
(disaster recovery) or a different one (cloning). Publishing a backup as a *template*
lets other servers apply the same setup by slug. Portability rules live in
``app.services.backup``; the actual repo reads/writes live here (they need bot + guild).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.services import (
    PORTABLE_SECTIONS,
    build_backup,
    select_sections,
    summarize_sections,
    validate_backup,
)

from ..dependencies import BotDep, GuildDep, verify_token
from .guild import _build_config_updates

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Backup & Templates"], dependencies=[Depends(verify_token)])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ImportBackupBody(BaseModel):
    data: dict
    sections: list[str] | None = None


class PublishTemplateBody(BaseModel):
    slug: str
    name: str
    description: str | None = None
    public: bool = False
    #: Optional caller-supplied backup blob; when omitted the current guild is exported.
    data: dict | None = None


# ---------------------------------------------------------------------------
# Section collection / application (shared by backup + templates)
# ---------------------------------------------------------------------------


async def _collect_sections(bot, guild) -> dict[str, object]:
    """Read a guild's portable config into the backup section shape."""
    gc = await bot.db.get_guild_config(guild.id)
    config_section = {
        "flags": {
            "audit_log": gc.flags.audit_log,
            "raid": gc.flags.raid,
            "alerts": gc.flags.alerts,
            "sentinel": gc.flags.sentinel,
            "mentions": gc.flags.mentions,
        },
        "prefixes": list(gc.prefixes),
        "mention_count": gc.mention_count,
        "use_music_panel": gc.use_music_panel,
    }

    autoresponders = [
        {
            "trigger": r["trigger"],
            "response": r["response"],
            "match_type": r["match_type"],
            "ignore_case": r.get("ignore_case", True),
        }
        for r in await bot.db.autoresponders.get_all(guild.id)
    ]

    tags = [{"name": r["name"], "content": r["content"]} for r in await bot.db.tags.export_tags(guild.id)]

    return {"config": config_section, "autoresponders": autoresponders, "tags": tags}


async def _apply_config(bot, guild, section: object) -> dict:
    if not isinstance(section, dict):
        return {"applied": 0}
    gc = await bot.db.get_guild_config(guild.id)
    updates = _build_config_updates(section, gc)
    if updates:
        await gc.update(**updates)
    return {"applied": len(updates)}


async def _apply_autoresponders(bot, guild, section: object) -> dict:
    created = skipped = failed = 0
    if not isinstance(section, list):
        return {"created": 0, "skipped": 0, "failed": 0}
    for item in section[:1000]:
        if not isinstance(item, dict):
            failed += 1
            continue
        trigger = (item.get("trigger") or "").strip()
        response = (item.get("response") or "").strip()
        if not trigger or not response:
            failed += 1
            continue
        result = await bot.db.autoresponders.create(
            guild.id, trigger, response,
            match_type=item.get("match_type", "contains"),
            ignore_case=bool(item.get("ignore_case", True)),
            created_by=bot.user.id,
        )
        if result is None:  # duplicate trigger
            skipped += 1
        else:
            created += 1
    return {"created": created, "skipped": skipped, "failed": failed}


async def _apply_tags(bot, guild, section: object) -> dict:
    created = skipped = failed = 0
    if not isinstance(section, list):
        return {"created": 0, "skipped": 0, "failed": 0}

    root = bot.get_command("tag")
    reserved = set(root.all_commands) if root else set()
    seen: set[str] = set()

    for item in section[:1000]:
        if not isinstance(item, dict):
            failed += 1
            continue
        name = (item.get("name") or "").strip()
        content = (item.get("content") or "").strip()
        if not name or not content or len(name) > 100 or len(content) > 2000:
            failed += 1
            continue
        lname = name.lower()
        if lname.partition(" ")[0] in reserved or lname in seen:
            skipped += 1
            continue
        seen.add(lname)
        if await bot.db.tags.get_tag_record(name, location_id=guild.id) is not None:
            skipped += 1
            continue
        try:
            await bot.db.tags.create_tag(name, content, bot.user.id, guild.id)
            created += 1
        except Exception:
            failed += 1
    return {"created": created, "skipped": skipped, "failed": failed}


async def _apply_sections(bot, guild, sections: dict[str, object]) -> dict:
    """Apply selected sections to a guild, returning a per-section result report."""
    report: dict[str, object] = {}
    if "config" in sections:
        report["config"] = await _apply_config(bot, guild, sections["config"])
    if "autoresponders" in sections:
        report["autoresponders"] = await _apply_autoresponders(bot, guild, sections["autoresponders"])
    if "tags" in sections:
        report["tags"] = await _apply_tags(bot, guild, sections["tags"])
    return report


# ---------------------------------------------------------------------------
# Backup export / import
# ---------------------------------------------------------------------------


@router.get("/backup/export")
async def export_backup(guild: GuildDep, bot: BotDep) -> dict:
    """Export the guild's portable config as a downloadable backup blob."""
    sections = await _collect_sections(bot, guild)
    return build_backup(guild.id, sections)


@router.post("/backup/import")
async def import_backup(
    guild: GuildDep,
    bot: BotDep,
    body: ImportBackupBody,
    dry_run: bool = Query(default=True, description="Preview counts without writing"),
) -> dict:
    """Apply a backup blob to this guild. Additive: existing tags/triggers are skipped, not overwritten.

    ``dry_run=true`` (the default) returns the section item counts that *would* be applied,
    without writing anything. Set ``dry_run=false`` to actually restore.
    """
    ok, error = validate_backup(body.data)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    sections = select_sections(body.data, body.sections)
    if not sections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"no applicable sections; expected some of {list(PORTABLE_SECTIONS)}",
        )

    if dry_run:
        return {"ok": True, "dry_run": True, "plan": summarize_sections(sections)}

    report = await _apply_sections(bot, guild, sections)
    return {"ok": True, "dry_run": False, "applied": report}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_templates(guild: GuildDep, bot: BotDep, limit: int = Query(default=50, le=100)) -> dict:
    """Public templates (the gallery) plus the ones this guild has published."""
    public = await bot.db.templates.list_public(limit=limit)
    own = await bot.db.templates.list_for_guild(guild.id)

    def _pub(r) -> dict:
        return {
            "slug": r["slug"], "name": r["name"], "description": r["description"],
            "downloads": r["downloads"], "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }

    return {
        "public": [_pub(r) for r in public],
        "own": [
            {**_pub(r), "public": r["public"]}
            for r in own
        ],
    }


@router.post("/templates")
async def publish_template(guild: GuildDep, bot: BotDep, body: PublishTemplateBody) -> dict:
    """Publish a template from the current guild's config (or a supplied backup blob)."""
    slug = body.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="slug must be 3-50 chars: lowercase letters, digits and hyphens",
        )
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")

    if body.data is not None:
        ok, error = validate_backup(body.data)
        if not ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
        data = body.data
    else:
        data = build_backup(guild.id, await _collect_sections(bot, guild))

    record = await bot.db.templates.create(
        slug, name, body.description.strip() if body.description else None,
        guild.id, bot.user.id, data, body.public,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="that slug is already taken")
    return {"ok": True, "slug": slug}


@router.post("/templates/{slug}/apply")
async def apply_template(
    guild: GuildDep,
    bot: BotDep,
    slug: str,
    dry_run: bool = Query(default=True, description="Preview counts without writing"),
) -> dict:
    """Apply a template (public or your own) to this guild. Additive, like a backup import."""
    template = await bot.db.templates.get(slug)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    if not template["public"] and template["author_guild_id"] != guild.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")

    data = template["data"]
    ok, error = validate_backup(data)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"template data is invalid: {error}")

    sections = select_sections(data, None)
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": summarize_sections(sections)}

    report = await _apply_sections(bot, guild, sections)
    await bot.db.templates.increment_downloads(slug)
    return {"ok": True, "dry_run": False, "applied": report}


@router.delete("/templates/{slug}")
async def delete_template(guild: GuildDep, bot: BotDep, slug: str) -> dict:
    """Delete a template this guild authored."""
    template = await bot.db.templates.get(slug)
    if template is None or template["author_guild_id"] != guild.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    await bot.db.templates.delete(slug, guild.id)
    return {"ok": True}
