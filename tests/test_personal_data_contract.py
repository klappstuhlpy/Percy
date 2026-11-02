"""Every user-id column in migrations/*.sql is erased or explicitly retained (see app/database/personal_data.py)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from app.database.personal_data import DELETED, RETAINED
from app.database.repositories.users import UsersRepository

MIGRATIONS = Path(__file__).resolve().parent.parent / 'migrations'
NAME = re.compile(
    r'^(user_id|author_id|owner_id|member_id|moderator_id|reporter_id|target_id|created_by|decided_by|from_id|'
    r'discord_id|uuid|creator_id|requester_id|actor_id)$')
TABLE = re.compile(r'(?:CREATE TABLE(?: IF NOT EXISTS)?|CREATE TYPE|ALTER TABLE(?: IF EXISTS)?(?: ONLY)?)\s+"?(\w+)', re.I)
DROP = re.compile(r'DROP TABLE(?: IF EXISTS)?\s+"?(\w+)', re.I)
TYPES = r'(?:BIGINT|INTEGER|INT|TEXT|UUID)\b'
CREATE_COL = re.compile(r'[(,]\s*"?(\w+)"?\s+' + TYPES, re.I)
ADD_COL = re.compile(r'ADD COLUMN(?:\s+IF NOT EXISTS)?\s+"?(\w+)"?\s+' + TYPES, re.I)

# Not discoverable by a column-name scan: the user's own settings row, id arrays and ids inside JSON.
CURATED = {'user_settings.id', 'giveaways.entries', 'polls.entries', 'guild_notifications.meta', 'timers.metadata'}


def scan(scripts: list[str]) -> set[str]:
    """User-id ``table.column`` keys declared by migration scripts (in order), per statement, minus dropped tables."""
    found: set[str] = set()
    for script in scripts:
        for stmt in re.sub(r'--[^\n]*', '', script).split(';'):
            if m := DROP.search(stmt):
                found = {k for k in found if k.split('.')[0] != m.group(1)}
            if not (t := TABLE.search(stmt)):
                continue
            cols = CREATE_COL if 'CREATE' in t.group(0).upper() else ADD_COL
            found |= {f'{t.group(1)}.{c}' for c in cols.findall(stmt[t.end():]) if NAME.match(c)}
    return found


def discover() -> set[str]:
    files = sorted(MIGRATIONS.glob('V*.sql'), key=lambda p: int(re.match(r'V(\d+)', p.name).group(1)))  # type: ignore[union-attr]
    return CURATED | scan([f.read_text(encoding='utf8') for f in files])


def test_scan_handles_every_statement_shape() -> None:
    found = scan([
        'ALTER TABLE a ADD COLUMN author_id BIGINT;',
        'ALTER TABLE b ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL;',
        'CREATE TABLE c (user_id BIGINT NOT NULL);',
        'CREATE TABLE IF NOT EXISTS d ( "owner_id" BIGINT, x TEXT );',
        'CREATE TABLE e\n(\n    id SERIAL,\n    -- note\n    target_id BIGINT,\n    other INT\n);',
        'CREATE TABLE gone (user_id BIGINT);',
        'DROP TABLE IF EXISTS gone;',
    ])
    assert found == {'a.author_id', 'b.user_id', 'c.user_id', 'd.owner_id', 'e.target_id'}


def test_every_user_id_column_is_classified() -> None:
    unclassified = discover() - set(DELETED) - set(RETAINED)
    assert not unclassified, f'add to DELETED or RETAINED in app/database/personal_data.py: {sorted(unclassified)}'


def test_registry_has_no_overlap_stale_or_blank_reasons() -> None:
    assert not set(DELETED) & set(RETAINED)
    assert not (set(DELETED) | set(RETAINED)) - discover(), 'registry names a column no migration declares'
    assert all(reason.strip() for reason in RETAINED.values())


async def test_delete_issues_one_statement_per_registry_entry() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    acquired = MagicMock()
    acquired.__aenter__ = AsyncMock(return_value=conn)
    acquired.__aexit__ = AsyncMock(return_value=False)
    repo = UsersRepository.__new__(UsersRepository)
    repo.acquire = MagicMock(return_value=acquired)  # type: ignore[method-assign]

    await repo.delete_personal_data(42)

    sql = [call.args[0] for call in conn.execute.await_args_list]
    assert len(sql) == len(DELETED)
    for entry, statement in zip(DELETED, sql, strict=True):
        table, column = entry.split('.')
        assert statement == f'DELETE FROM {table} WHERE {column} = $1;'
    assert all(call.args[1] == 42 for call in conn.execute.await_args_list)
