"""Data access for guild custom commands and command aliases (``migrations/V12``).

Every mutation busts ``custom_commands_changed`` so the cached dispatch index
(``db.get_custom_command_index``) sees it on the next message. ``bump_uses`` deliberately does
not: the cached ``uses`` is never shown (``info`` and the API read fresh rows) and the value it
returns is what ``{uses}`` renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository
from app.services.custom_commands.models import EDITABLE_FIELDS, CommandAlias, CustomCommand

if TYPE_CHECKING:
    import asyncpg

__all__ = ('CustomCommandsRepository',)

_FIELDS = EDITABLE_FIELDS


def _command(row: asyncpg.Record) -> CustomCommand:
    return CustomCommand(
        id=row['id'],
        guild_id=row['guild_id'],
        name=row['name'],
        aliases=tuple(row['aliases'] or ()),
        signature=row['signature'],
        responses=tuple(row['responses'] or ()),
        required_permissions=row['required_permissions'],
        allowed_role_ids=tuple(row['allowed_role_ids'] or ()),
        cooldown_rate=row['cooldown_rate'],
        cooldown_per=row['cooldown_per'],
        delete_invocation=row['delete_invocation'],
        owner_id=row['owner_id'],
        uses=row['uses'],
        created_at=row['created_at'],
    )


class CustomCommandsRepository(BaseRepository):
    def _changed(self, guild_id: int) -> None:
        self.invalidate_cache('custom_commands_changed', guild_id)

    # -- custom commands ------------------------------------------------

    async def list_commands(self, guild_id: int) -> list[CustomCommand]:
        rows = await self.fetch('SELECT * FROM custom_commands WHERE guild_id = $1 ORDER BY name;', guild_id)
        return [_command(r) for r in rows]

    async def get_command(self, guild_id: int, command_id: int) -> CustomCommand | None:
        row = await self.fetchrow('SELECT * FROM custom_commands WHERE guild_id = $1 AND id = $2;', guild_id, command_id)
        return _command(row) if row else None

    async def count_commands(self, guild_id: int) -> int:
        return await self.fetchval('SELECT COUNT(*) FROM custom_commands WHERE guild_id = $1;', guild_id)

    async def create_command(self, guild_id: int, owner_id: int | None, **values: Any) -> CustomCommand:
        columns = [k for k in _FIELDS if k in values]
        placeholders = ', '.join(f'${i}' for i in range(3, len(columns) + 3))
        row = await self.fetchrow(
            f'INSERT INTO custom_commands (guild_id, owner_id, {", ".join(columns)}) '
            f'VALUES ($1, $2, {placeholders}) RETURNING *;',
            guild_id, owner_id, *(values[k] for k in columns),
        )
        self._changed(guild_id)
        assert row is not None
        return _command(row)

    async def update_command(self, guild_id: int, command_id: int, **values: Any) -> CustomCommand | None:
        """Set the given columns (names from a fixed whitelist, never user input)."""
        columns = [k for k in _FIELDS if k in values]
        if not columns:
            return await self.get_command(guild_id, command_id)
        assignments = ', '.join(f'{k} = ${i}' for i, k in enumerate(columns, start=3))
        row = await self.fetchrow(
            f'UPDATE custom_commands SET {assignments} WHERE guild_id = $1 AND id = $2 RETURNING *;',
            guild_id, command_id, *(values[k] for k in columns),
        )
        self._changed(guild_id)
        return _command(row) if row else None

    async def delete_command(self, guild_id: int, command_id: int) -> bool:
        status = await self.execute('DELETE FROM custom_commands WHERE guild_id = $1 AND id = $2;', guild_id, command_id)
        self._changed(guild_id)
        return not status.endswith(' 0')

    async def bump_uses(self, command_id: int) -> int:
        return await self.fetchval('UPDATE custom_commands SET uses = uses + 1 WHERE id = $1 RETURNING uses;', command_id) or 0

    # -- aliases --------------------------------------------------------

    async def list_aliases(self, guild_id: int) -> list[CommandAlias]:
        rows = await self.fetch('SELECT * FROM command_aliases WHERE guild_id = $1 ORDER BY alias;', guild_id)
        return [CommandAlias(r['guild_id'], r['alias'], r['expansion'], r['owner_id']) for r in rows]

    async def put_alias(self, guild_id: int, alias: str, expansion: str, owner_id: int | None) -> None:
        await self.execute(
            'INSERT INTO command_aliases (guild_id, alias, expansion, owner_id) VALUES ($1, $2, $3, $4) '
            'ON CONFLICT (guild_id, alias) DO UPDATE SET expansion = EXCLUDED.expansion;',
            guild_id, alias, expansion, owner_id,
        )
        self._changed(guild_id)

    async def delete_alias(self, guild_id: int, alias: str) -> bool:
        status = await self.execute('DELETE FROM command_aliases WHERE guild_id = $1 AND alias = $2;', guild_id, alias)
        self._changed(guild_id)
        return not status.endswith(' 0')
