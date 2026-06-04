from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = ('RoleMenusRepository',)


class RoleMenusRepository(BaseRepository):
    """Data access for the ``role_menus`` and ``role_menu_entries`` tables.

    A role menu is a posted message with one button per offered role; ``role_menu_entries``
    holds those role/emoji/label rows. Methods return raw records; the ``RoleMenus`` cog
    renders them into a persistent button view.
    """

    # -- menus ------------------------------------------------------------

    async def create_menu(
        self, guild_id: int, channel_id: int, title: str, description: str | None
    ) -> asyncpg.Record:
        """Creates a menu (without a message yet) and returns the row."""
        query = """
            INSERT INTO role_menus (guild_id, channel_id, title, description)
            VALUES ($1, $2, $3, $4)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, channel_id, title, description)

    async def set_message(self, menu_id: int, message_id: int) -> None:
        """Stores the id of the posted menu message."""
        await self.execute('UPDATE role_menus SET message_id = $2 WHERE id = $1;', menu_id, message_id)

    async def set_unique(self, menu_id: int, unique: bool) -> None:
        """Toggles radio-style (single-choice) behaviour for a menu."""
        await self.execute('UPDATE role_menus SET unique_roles = $2 WHERE id = $1;', menu_id, unique)

    async def get_menu(self, menu_id: int) -> asyncpg.Record | None:
        """Fetches a single menu by id."""
        return await self.fetchrow('SELECT * FROM role_menus WHERE id = $1;', menu_id)

    async def get_guild_menu(self, guild_id: int, menu_id: int) -> asyncpg.Record | None:
        """Fetches a menu by id, scoped to a guild."""
        return await self.fetchrow(
            'SELECT * FROM role_menus WHERE id = $1 AND guild_id = $2;', menu_id, guild_id)

    async def get_guild_menus(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches all menus for a guild, newest first."""
        return await self.fetch('SELECT * FROM role_menus WHERE guild_id = $1 ORDER BY id DESC;', guild_id)

    async def delete_menu(self, menu_id: int) -> asyncpg.Record | None:
        """Deletes a menu (cascading its entries) and returns the deleted row."""
        return await self.fetchrow('DELETE FROM role_menus WHERE id = $1 RETURNING *;', menu_id)

    # -- entries ----------------------------------------------------------

    async def get_entries(self, menu_id: int) -> list[asyncpg.Record]:
        """Fetches a menu's role entries in display order."""
        return await self.fetch(
            'SELECT * FROM role_menu_entries WHERE menu_id = $1 ORDER BY position, role_id;', menu_id)

    async def count_entries(self, menu_id: int) -> int:
        """Counts how many roles a menu offers."""
        return await self.fetchval('SELECT COUNT(*) FROM role_menu_entries WHERE menu_id = $1;', menu_id)

    async def add_entry(
        self, menu_id: int, role_id: int, emoji: str | None, label: str | None, position: int
    ) -> asyncpg.Record | None:
        """Adds a role to a menu, returning the row (or ``None`` if already present)."""
        query = """
            INSERT INTO role_menu_entries (menu_id, role_id, emoji, label, position)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (menu_id, role_id) DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, menu_id, role_id, emoji, label, position)

    async def remove_entry(self, menu_id: int, role_id: int) -> asyncpg.Record | None:
        """Removes a role from a menu, returning the deleted row (or ``None``)."""
        query = 'DELETE FROM role_menu_entries WHERE menu_id = $1 AND role_id = $2 RETURNING *;'
        return await self.fetchrow(query, menu_id, role_id)
