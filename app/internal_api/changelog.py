"""Parse CHANGELOG.md into structured releases for the /bot/changelog endpoint.

The repo-root CHANGELOG.md is the single source of truth. This module reads it
once at import time (like klappstuhl_me's include_str! approach) and exposes a
validated list of releases. The format contract is documented in
`.claude/CHANGELOG_GUIDE.md`; violations log a warning rather than crashing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CHANGELOG_PATH = Path(__file__).parents[2] / 'CHANGELOG.md'

ALLOWED_CATEGORIES = ('Added', 'Changed', 'Deprecated', 'Removed', 'Fixed', 'Security')

_RELEASE_RE = re.compile(r'^\#\#\s+\[(\d+\.\d+\.\d+)\]\s+-\s+(\d{4}-\d{2}-\d{2})\s*$')
_UNRELEASED_RE = re.compile(r'^\#\#\s+\[Unreleased\]\s*$', re.IGNORECASE)
_CATEGORY_RE = re.compile(r'^\#\#\#\s+(\w+)\s*$')
_BULLET_RE = re.compile(r'^-\s+(.+)$')


@dataclass
class Section:
    name: str
    slug: str
    entries: list[str] = field(default_factory=list)


@dataclass
class Release:
    version: str
    date: str
    sections: list[Section] = field(default_factory=list)


def parse(source: str) -> list[Release]:
    """Parse changelog source text into a list of releases (newest first).

    Skips the [Unreleased] block. Returns an empty list on malformed input
    (with a logged warning).
    """
    releases: list[Release] = []
    current_release: Release | None = None
    current_section: Section | None = None
    in_unreleased = False

    for line in source.splitlines():
        line_stripped = line.strip()

        if _UNRELEASED_RE.match(line_stripped):
            in_unreleased = True
            current_release = None
            current_section = None
            continue

        release_match = _RELEASE_RE.match(line_stripped)
        if release_match:
            in_unreleased = False
            current_release = Release(
                version=release_match.group(1),
                date=release_match.group(2),
            )
            releases.append(current_release)
            current_section = None
            continue

        if in_unreleased:
            continue

        if current_release is None:
            continue

        cat_match = _CATEGORY_RE.match(line_stripped)
        if cat_match:
            cat_name = cat_match.group(1)
            if cat_name not in ALLOWED_CATEGORIES:
                log.warning('CHANGELOG.md: unknown category %r in release %s', cat_name, current_release.version)
                continue
            current_section = Section(name=cat_name, slug=cat_name.lower())
            current_release.sections.append(current_section)
            continue

        bullet_match = _BULLET_RE.match(line_stripped)
        if bullet_match and current_section is not None:
            current_section.entries.append(bullet_match.group(1))

    return releases


def _load_releases() -> list[Release]:
    """Load and parse the CHANGELOG.md file. Returns empty on any error."""
    try:
        source = CHANGELOG_PATH.read_text(encoding='utf-8')
    except OSError:
        log.warning('CHANGELOG.md not found at %s', CHANGELOG_PATH)
        return []

    releases = parse(source)
    if not releases:
        log.warning('CHANGELOG.md parsed but contains no releases')
    else:
        log.info('Loaded %d releases from CHANGELOG.md (latest: v%s)', len(releases), releases[0].version)
    return releases


RELEASES: list[Release] = _load_releases()
