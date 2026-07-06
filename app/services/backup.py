"""Pure helpers for guild config backup / restore and shareable templates.

A *backup* is a portable JSON snapshot of a guild's content configuration. This module
owns the envelope shape and validation only; the actual data collection (reading repos)
and restore (writing repos, resolving channel/role IDs against the target guild) live in
the ``backup`` internal-API router, which needs the bot and guild objects. Everything here
is Discord-free and unit-testable.
"""
from __future__ import annotations

from datetime import UTC, datetime

__all__ = (
    'BACKUP_KIND',
    'BACKUP_VERSION',
    'PORTABLE_SECTIONS',
    'build_backup',
    'select_sections',
    'summarize_sections',
    'validate_backup',
)

#: Bumped only on a breaking change to the envelope/section shape.
BACKUP_VERSION = 1

#: Discriminator so a backup blob can't be confused with some other JSON document.
BACKUP_KIND = 'percy.backup'

#: The sections a backup can carry, in a stable apply order. ``config`` (portable guild
#: scalars) is applied first, then the content collections. Only ID-free sections are
#: portable so a backup restores cleanly into *any* guild; channel/role-bound features
#: (comics, temp-channels, log channels) are deliberately excluded. Anything not in this
#: tuple is ignored by both export and restore.
PORTABLE_SECTIONS: tuple[str, ...] = (
    'config',
    'autoresponders',
    'tags',
)


def build_backup(guild_id: int, sections: dict[str, object]) -> dict:
    """Wrap collected section data in the versioned backup envelope.

    Only keys in :data:`PORTABLE_SECTIONS` are retained, so a caller can pass a superset
    without leaking unknown data into the blob.
    """
    return {
        'kind': BACKUP_KIND,
        'version': BACKUP_VERSION,
        'guild_id': str(guild_id),
        'created_at': datetime.now(UTC).isoformat(),
        'sections': {name: sections[name] for name in PORTABLE_SECTIONS if name in sections},
    }


def validate_backup(blob: object) -> tuple[bool, str | None]:
    """Return ``(ok, error)`` for an untrusted blob before any restore is attempted.

    Checks the discriminator, a supported version, and that ``sections`` is an object. The
    per-section item validation is left to the restore handler (it depends on the section).
    """
    if not isinstance(blob, dict):
        return False, 'backup must be a JSON object'
    if blob.get('kind') != BACKUP_KIND:
        return False, 'not a Percy backup (missing or wrong "kind")'
    version = blob.get('version')
    if not isinstance(version, int) or version > BACKUP_VERSION:
        return False, f'unsupported backup version: {version!r}'
    if not isinstance(blob.get('sections'), dict):
        return False, '"sections" must be an object'
    return True, None


def select_sections(blob: dict, requested: list[str] | None) -> dict[str, object]:
    """Return the subset of a validated blob's sections the caller asked for.

    ``requested is None`` means "every portable section present in the blob". Unknown or
    non-portable section names are always filtered out.
    """
    sections = blob.get('sections', {})
    if not isinstance(sections, dict):
        return {}
    wanted = PORTABLE_SECTIONS if requested is None else tuple(s for s in requested if s in PORTABLE_SECTIONS)
    return {name: sections[name] for name in wanted if name in sections}


def summarize_sections(sections: dict[str, object]) -> dict[str, int]:
    """Count the items in each section, for a dry-run import summary.

    ``config`` counts the number of set fields; every other (list-shaped) section counts
    its entries. Sections whose value isn't a recognised shape report ``0``.
    """
    summary: dict[str, int] = {}
    for name, value in sections.items():
        if isinstance(value, (list, dict)):
            summary[name] = len(value)
        else:
            summary[name] = 0
    return summary
