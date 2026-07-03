"""Tests for the forward-only :class:`~app.database.migrations.MigrationRunner`.

The filesystem logic (discovery, validation, stub creation, checksums) is pure and tested
directly. The database reconciliation (bootstrap/backfill, pending, upgrade, integrity) is
exercised against :class:`FakeConnection`, an in-memory stand-in that actually tracks
``schema_migrations`` rows so the runner's decisions can be asserted end-to-end.
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

import pytest

from app.database.migrations import MIGRATIONS_TABLE, MigrationError, MigrationRunner

if TYPE_CHECKING:
    from pathlib import Path


# -- helpers ---------------------------------------------------------------


def write_migration(directory: Path, version: int, name: str, body: str = "SELECT 1;") -> Path:
    path = directory / f"V{version}__{name}.sql"
    path.write_text(f"-- Reason: {name}\n{body}\n", encoding="utf-8", newline="\n")
    return path


class FakeConnection:
    """Minimal in-memory asyncpg.Connection stand-in for the migration table."""

    def __init__(self, *, existing_tables: tuple[str, ...] = ()) -> None:
        self.tables: set[str] = set(existing_tables)
        self.rows: dict[int, dict[str, Any]] = {}
        self.ran_sql: list[str] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        q = " ".join(query.split())
        if q.startswith("SELECT to_regclass($1)"):
            return object() if str(args[0]).split(".")[-1] in self.tables else None
        if f"EXISTS (SELECT 1 FROM {MIGRATIONS_TABLE})" in q:
            return len(self.rows) > 0
        if "COALESCE(MAX(version), 0)" in q:
            return max(self.rows, default=0)
        return None

    async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
        return [self.rows[v] for v in sorted(self.rows)]

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        if q.startswith(f"CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE}"):
            self.tables.add(MIGRATIONS_TABLE)
        elif q.startswith(f"INSERT INTO {MIGRATIONS_TABLE}"):
            version, description, checksum = args
            self.rows[version] = {
                "version": version,
                "description": description,
                "checksum": checksum,
                "applied_at": datetime.datetime.now(datetime.UTC),
            }
        elif q.startswith(f"UPDATE {MIGRATIONS_TABLE} SET checksum"):
            version, checksum, description = args  # applied_at intentionally left untouched
            self.rows[version]["checksum"] = checksum
            self.rows[version]["description"] = description
        else:
            self.ran_sql.append(q)  # an actual migration body
        return "OK"

    def transaction(self) -> Any:
        class _Tx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        return _Tx()


# -- filesystem (pure) -----------------------------------------------------


def test_discover_orders_and_parses(tmp_path: Path) -> None:
    write_migration(tmp_path, 2, "second")
    write_migration(tmp_path, 1, "first_thing")
    (tmp_path / "not_a_migration.sql").write_text("noise", encoding="utf-8")

    runner = MigrationRunner(directory=tmp_path)

    assert [m.version for m in runner.migrations] == [1, 2]
    assert runner.migrations[0].title == "first thing"
    assert runner.latest_version == 2


def test_duplicate_version_raises(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "alpha")
    write_migration(tmp_path, 1, "beta")

    with pytest.raises(MigrationError, match="Duplicate migration version 1"):
        MigrationRunner(directory=tmp_path)


def test_validate_flags_gaps_and_bad_start(tmp_path: Path) -> None:
    write_migration(tmp_path, 2, "second")
    write_migration(tmp_path, 4, "fourth")

    problems = MigrationRunner(directory=tmp_path).validate()

    assert any("start at V1" in p for p in problems)
    assert any("V3" in p for p in problems)  # the gap


def test_create_writes_next_stub(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)

    migration = runner.create("add cool feature")

    assert migration.version == 2
    assert migration.path.name == "V2__add_cool_feature.sql"
    assert migration.path.exists()
    assert "Revises: V1" in migration.path.read_text(encoding="utf-8")
    assert runner.latest_version == 2  # appended to the in-memory list


def test_checksum_and_transaction_directive(tmp_path: Path) -> None:
    plain = write_migration(tmp_path, 1, "plain")
    runner = MigrationRunner(directory=tmp_path)
    first = runner.migrations[0].checksum

    plain.write_text("SELECT 2;\n", encoding="utf-8", newline="\n")
    assert MigrationRunner(directory=tmp_path).migrations[0].checksum != first

    write_migration(tmp_path, 2, "concurrent", body="-- migration: no-transaction\nCREATE INDEX CONCURRENTLY x;")
    runner = MigrationRunner(directory=tmp_path)
    assert runner.by_version()[1].is_transactional is True
    assert runner.by_version()[2].is_transactional is False


# -- database reconciliation ----------------------------------------------


async def test_fresh_database_applies_everything(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    write_migration(tmp_path, 2, "second")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()  # no tables, no revisions.json

    backfilled = await runner.bootstrap(conn)
    applied = await runner.upgrade(conn)

    assert backfilled == 0
    assert [m.version for m in applied] == [1, 2]
    assert await runner.current_version(conn) == 2
    assert await runner.pending(conn) == []


async def test_legacy_database_backfills_then_applies_remainder(tmp_path: Path) -> None:
    for v, name in [(1, "first"), (2, "second"), (3, "third")]:
        write_migration(tmp_path, v, name)
    (tmp_path / "revisions.json").write_text(json.dumps({"version": 2}), encoding="utf-8")
    runner = MigrationRunner(directory=tmp_path)
    # Legacy DB: application schema exists but schema_migrations does not.
    conn = FakeConnection(existing_tables=("guild_config",))

    backfilled = await runner.bootstrap(conn)
    assert backfilled == 2  # V1, V2 recorded without re-running
    assert conn.ran_sql == []  # nothing executed during backfill

    applied = await runner.upgrade(conn)
    assert [m.version for m in applied] == [3]  # only the un-backfilled remainder
    assert await runner.current_version(conn) == 3


async def test_upgrade_respects_target(tmp_path: Path) -> None:
    for v in (1, 2, 3):
        write_migration(tmp_path, v, f"v{v}")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()

    applied = await runner.upgrade(conn, target=2)

    assert [m.version for m in applied] == [1, 2]
    assert [m.version for m in await runner.pending(conn)] == [3]


async def test_integrity_detects_drift_and_missing_file(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()
    await runner.upgrade(conn)  # records V1 with the correct checksum

    assert await runner.check_integrity(conn) == []

    # Tamper with the recorded checksum -> drift.
    conn.rows[1]["checksum"] = "deadbeef"
    # And record a version whose file does not exist.
    conn.rows[2] = {"version": 2, "description": "ghost", "checksum": "x", "applied_at": datetime.datetime.now(datetime.UTC)}

    problems = await runner.check_integrity(conn)
    assert any("modified after it was applied" in p for p in problems)
    assert any("file is missing" in p for p in problems)


async def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()

    assert len(await runner.upgrade(conn)) == 1
    assert await runner.upgrade(conn) == []  # second run applies nothing


async def test_reseal_syncs_checksum_without_running_sql(tmp_path: Path) -> None:
    path = write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()
    await runner.upgrade(conn)  # records V1 with the current checksum
    applied_at = conn.rows[1]["applied_at"]

    # Deliberately edit the already-applied file (a "fresh-DB-only" fix).
    path.write_text("-- Reason: first\nSELECT 42;\n", encoding="utf-8", newline="\n")
    runner = MigrationRunner(directory=tmp_path)  # re-discover the edited file
    assert await runner.check_integrity(conn)  # drift is now reported
    assert [m.version for m in await runner.drifted(conn)] == [1]
    sql_before = list(conn.ran_sql)

    old, new = await runner.reseal(conn, 1)

    assert old != new
    assert conn.rows[1]["checksum"] == new == runner.by_version()[1].checksum
    assert conn.rows[1]["applied_at"] == applied_at  # apply time preserved
    assert conn.ran_sql == sql_before  # reseal ran no additional migration SQL
    assert await runner.check_integrity(conn) == []  # drift resolved


async def test_reseal_dry_run_leaves_row_untouched(tmp_path: Path) -> None:
    path = write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()
    await runner.upgrade(conn)
    recorded = conn.rows[1]["checksum"]

    path.write_text("-- Reason: first\nSELECT 42;\n", encoding="utf-8", newline="\n")
    runner = MigrationRunner(directory=tmp_path)

    old, new = await runner.reseal(conn, 1, dry_run=True)

    assert old != new  # reports what *would* change
    assert conn.rows[1]["checksum"] == recorded  # but the row is untouched
    assert await runner.check_integrity(conn)  # still drifted


async def test_reseal_is_noop_when_in_sync(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()
    await runner.upgrade(conn)

    old, new = await runner.reseal(conn, 1)

    assert old == new  # nothing drifted
    assert await runner.drifted(conn) == []


async def test_reseal_rejects_unapplied_version(tmp_path: Path) -> None:
    write_migration(tmp_path, 1, "first")
    runner = MigrationRunner(directory=tmp_path)
    conn = FakeConnection()  # nothing applied yet

    with pytest.raises(MigrationError, match="not applied"):
        await runner.reseal(conn, 1)
