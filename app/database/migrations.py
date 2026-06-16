"""Forward-only SQL migration runner.

Two sources of truth, cleanly separated:

* **Available** migrations are the ``migrations/V<n>__<description>.sql`` files on disk.
* **Applied** migrations are rows in the ``schema_migrations`` table, which records the
  version, description, content checksum and apply time of every migration that has run.

This replaces the old ``revisions.json`` integer counter. Keeping the applied state in
the database (next to the data it describes) means it survives repo moves, works across
machines/environments without a shared file, and lets us detect drift — an already-applied
migration whose file has since been edited — via checksums.

The first run against a database created by the legacy system transparently *backfills*
``schema_migrations`` from ``revisions.json`` (see :meth:`MigrationRunner.bootstrap`), so the
switch-over needs no manual steps. ``revisions.json`` is never written again afterwards.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import asyncpg

__all__ = ("AppliedMigration", "Migration", "MigrationError", "MigrationRunner")

log = logging.getLogger("migrations")

#: Name of the table that records applied migrations.
MIGRATIONS_TABLE = "schema_migrations"

#: A table created by the very first migration; its presence marks a database whose schema
#: was already built by the legacy (``revisions.json``) system and therefore needs backfilling.
LEGACY_SENTINEL_TABLE = "guild_config"

#: Legacy version file, read once for backfill and never written.
LEGACY_REVISIONS_FILE = "revisions.json"

_FILENAME_RE = re.compile(r"^(?P<kind>[VU])(?P<version>\d+)__(?P<description>.+)\.sql$")
#: A migration whose first lines contain ``-- migration: no-transaction`` is executed
#: outside an explicit transaction (e.g. for ``CREATE INDEX CONCURRENTLY``).
_NO_TRANSACTION_RE = re.compile(r"^--\s*migration:\s*no-transaction\b", re.IGNORECASE | re.MULTILINE)


class MigrationError(RuntimeError):
    """Raised for unrecoverable problems with the migration set (duplicates, bad files)."""


@dataclasses.dataclass(frozen=True, slots=True)
class Migration:
    """A single migration file available on disk."""

    version: int
    description: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        """SHA-256 of the file contents — used to detect post-apply edits."""
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def is_transactional(self) -> bool:
        return _NO_TRANSACTION_RE.search(self.sql) is None

    @property
    def label(self) -> str:
        return f"V{self.version:03d}"

    @property
    def title(self) -> str:
        return self.description.replace("_", " ")


@dataclasses.dataclass(frozen=True, slots=True)
class AppliedMigration:
    """A migration recorded as applied in ``schema_migrations``."""

    version: int
    description: str
    checksum: str
    applied_at: datetime.datetime


class MigrationRunner:
    """Discovers migration files and reconciles them against the ``schema_migrations`` table.

    Filesystem discovery happens eagerly in ``__init__`` (cheap, no DB needed). Every method
    that reconciles against the database takes an :class:`asyncpg.Connection`, so the caller
    controls connection lifetime — the bot reuses a pooled connection on startup, while the
    CLI opens a short-lived one.
    """

    _CREATE_TABLE_SQL: ClassVar[str] = f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            version     INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            checksum    TEXT NOT NULL,
            applied_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        );
    """

    def __init__(self, *, directory: str | Path = "migrations") -> None:
        self.directory: Path = Path(directory)
        self.migrations: list[Migration] = self._discover()

    # -- filesystem (no database) -----------------------------------------

    def _discover(self) -> list[Migration]:
        """Parses the migration directory into an ordered, de-duplicated list."""
        found: dict[int, Migration] = {}
        for file in self.directory.glob("*.sql"):
            match = _FILENAME_RE.match(file.name)
            if match is None:
                continue
            version = int(match.group("version"))
            if version in found:
                raise MigrationError(
                    f"Duplicate migration version {version}: "
                    f"{found[version].path.name!r} and {file.name!r}"
                )
            found[version] = Migration(version=version, description=match.group("description"), path=file)
        return [found[v] for v in sorted(found)]

    @property
    def latest_version(self) -> int:
        """Highest version present on disk (0 if there are none)."""
        return self.migrations[-1].version if self.migrations else 0

    def by_version(self) -> dict[int, Migration]:
        return {m.version: m for m in self.migrations}

    def validate(self) -> list[str]:
        """Static (DB-free) sanity checks on the file set. Returns human-readable problems."""
        problems: list[str] = []
        versions = [m.version for m in self.migrations]
        if not versions:
            return problems
        if versions[0] != 1:
            problems.append(f"Migrations should start at V1 but the lowest is V{versions[0]}.")
        expected = set(range(versions[0], versions[-1] + 1))
        missing = sorted(expected - set(versions))
        if missing:
            problems.append("Gap in migration sequence — missing: " + ", ".join(f"V{v}" for v in missing))
        return problems

    def create(self, reason: str, *, kind: str = "V") -> Migration:
        """Writes a new, empty migration stub one version above the latest file."""
        version = self.latest_version + 1
        slug = re.sub(r"\s+", "_", reason.strip())
        path = self.directory / f"{kind}{version}__{slug}.sql"
        path.write_text(
            f"-- Revises: V{self.latest_version}\n"
            f"-- Creation Date: {datetime.datetime.now(datetime.UTC)} UTC\n"
            f"-- Reason: {reason}\n"
            f"-- (add '-- migration: no-transaction' below to run outside a transaction)\n\n",
            encoding="utf-8",
            newline="\n",
        )
        migration = Migration(version=version, description=slug, path=path)
        self.migrations.append(migration)
        return migration

    # -- database reconciliation ------------------------------------------

    async def ensure_table(self, conn: asyncpg.Connection) -> bool:
        """Creates ``schema_migrations`` if absent. Returns ``True`` if it was just created."""
        existed = await conn.fetchval("SELECT to_regclass($1)", f"public.{MIGRATIONS_TABLE}") is not None
        if not existed:
            await conn.execute(self._CREATE_TABLE_SQL)
        return not existed

    async def fetch_applied(self, conn: asyncpg.Connection) -> dict[int, AppliedMigration]:
        """Returns the applied migrations keyed by version."""
        rows = await conn.fetch(
            f"SELECT version, description, checksum, applied_at FROM {MIGRATIONS_TABLE} ORDER BY version;"
        )
        return {
            row["version"]: AppliedMigration(
                version=row["version"],
                description=row["description"],
                checksum=row["checksum"],
                applied_at=row["applied_at"],
            )
            for row in rows
        }

    async def current_version(self, conn: asyncpg.Connection) -> int:
        return await conn.fetchval(f"SELECT COALESCE(MAX(version), 0) FROM {MIGRATIONS_TABLE};") or 0

    async def bootstrap(self, conn: asyncpg.Connection) -> int:
        """Ensures the table exists and backfills legacy state on first use.

        A database built by the old system has the application schema (``guild_config``)
        but no ``schema_migrations`` table. In that case the legacy ``revisions.json``
        version is authoritative for what has already run, so we record every migration up
        to it as applied. Returns the number of rows backfilled.
        """
        await self.ensure_table(conn)
        if await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {MIGRATIONS_TABLE});"):
            return 0  # already tracked — nothing to backfill

        legacy_version = self._legacy_version()
        schema_present = await conn.fetchval("SELECT to_regclass($1)", f"public.{LEGACY_SENTINEL_TABLE}") is not None
        if legacy_version <= 0 or not schema_present:
            # Fresh database (or no legacy marker): let ``upgrade`` build everything from V1.
            return 0

        backfill = [m for m in self.migrations if m.version <= legacy_version]
        async with conn.transaction():
            for migration in backfill:
                await self._record(conn, migration)
        log.info("Backfilled %d legacy migration(s) into %s (up to V%d).", len(backfill), MIGRATIONS_TABLE, legacy_version)
        return len(backfill)

    async def pending(self, conn: asyncpg.Connection, *, target: int | None = None) -> list[Migration]:
        """Migrations on disk not yet applied (optionally capped at ``target``)."""
        applied = await self.fetch_applied(conn)
        result = [m for m in self.migrations if m.version not in applied]
        if target is not None:
            result = [m for m in result if m.version <= target]
        return result

    async def check_integrity(self, conn: asyncpg.Connection) -> list[str]:
        """Compares applied rows against the files. Returns human-readable problems.

        Flags two situations: an applied migration whose file was edited afterwards
        (checksum mismatch), and an applied migration whose file no longer exists.
        """
        problems: list[str] = []
        files = self.by_version()
        for version, record in (await self.fetch_applied(conn)).items():
            migration = files.get(version)
            if migration is None:
                problems.append(f"V{version} ({record.description.replace('_', ' ')}) is applied but its file is missing.")
            elif migration.checksum != record.checksum:
                problems.append(f"{migration.label} ({migration.title}) was modified after it was applied.")
        return problems

    async def upgrade(self, conn: asyncpg.Connection, *, target: int | None = None) -> list[Migration]:
        """Applies all pending migrations (up to ``target``) and records each.

        Each migration runs in its own transaction together with its ``schema_migrations``
        insert, so a failure leaves earlier migrations committed and recorded — re-running
        resumes from the failure. Returns the migrations that were applied.
        """
        await self.bootstrap(conn)

        # Drift is surfaced but never blocks here: refusing to boot the bot over an edited
        # historical file would be worse than the drift. ``verify``/``status`` flag it loudly.
        for problem in await self.check_integrity(conn):
            log.warning("Migration integrity: %s", problem)

        applied: list[Migration] = []
        for migration in await self.pending(conn, target=target):
            await self._apply(conn, migration)
            applied.append(migration)
            log.info("Applied %s — %s", migration.label, migration.title)
        return applied

    async def _apply(self, conn: asyncpg.Connection, migration: Migration) -> None:
        sql = migration.sql
        if migration.is_transactional:
            async with conn.transaction():
                await conn.execute(sql)
                await self._record(conn, migration)
        else:
            await conn.execute(sql)
            await self._record(conn, migration)

    async def _record(self, conn: asyncpg.Connection, migration: Migration) -> None:
        await conn.execute(
            f"""
            INSERT INTO {MIGRATIONS_TABLE} (version, description, checksum)
            VALUES ($1, $2, $3)
            ON CONFLICT (version)
                DO UPDATE SET description = EXCLUDED.description,
                              checksum    = EXCLUDED.checksum,
                              applied_at  = now();
            """,
            migration.version,
            migration.description,
            migration.checksum,
        )

    def _legacy_version(self) -> int:
        try:
            data = json.loads((self.directory / LEGACY_REVISIONS_FILE).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return 0
        try:
            return int(data.get("version", 0))
        except (TypeError, ValueError):
            return 0
