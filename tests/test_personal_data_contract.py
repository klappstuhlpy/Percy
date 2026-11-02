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
COLUMN = re.compile(r'\s*(?:ADD COLUMN(?: IF NOT EXISTS)?\s+)?"?(\w+)"?\s+(?:BIGINT|INTEGER|INT|TEXT|UUID)\b', re.I)

# Not discoverable by a column-name scan: the user's own settings row, id arrays and ids inside JSON.
CURATED = {'user_settings.id', 'giveaways.entries', 'polls.entries', 'guild_notifications.meta', 'timers.metadata'}


def discover() -> set[str]:
    found = set(CURATED)
    for path in MIGRATIONS.glob('V*.sql'):
        table = None
        for line in path.read_text(encoding='utf8').splitlines():
            if m := TABLE.search(line):
                table = m.group(1)
            if (c := COLUMN.match(line)) and table and NAME.match(c.group(1)):
                found.add(f'{table}.{c.group(1)}')
    return found


def test_every_user_id_column_is_classified() -> None:
    unclassified = discover() - set(DELETED) - set(RETAINED)
    assert not unclassified, f'add to DELETED or RETAINED in app/database/personal_data.py: {sorted(unclassified)}'


def test_registry_has_no_overlap_stale_or_blank_reasons() -> None:
    assert not set(DELETED) & set(RETAINED)
    assert not (set(DELETED) | set(RETAINED)) - discover(), 'registry names a column no migration declares'
    assert all(reason.strip() for reason in RETAINED.values())


def test_red_parity_tables_are_classified() -> None:
    for key in ('trivia_scores.user_id', 'member_restrictions.user_id', 'command_rules.target_id',
                'custom_commands.owner_id', 'command_aliases.owner_id', 'trivia_lists.author_id',
                'mod_cases.target_id', 'guild_notifications.meta'):
        assert key in DELETED or key in RETAINED, key


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
