from __future__ import annotations

from typing import TYPE_CHECKING

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import datetime

    import asyncpg

__all__ = ('EconomyRepository',)


class EconomyRepository(BaseRepository):
    """Data access for the shop, inventory and daily-reward tables.

    Covers ``economy_items`` (the per-guild shop), ``economy_inventory`` (what each
    member owns) and ``economy_dailies`` (daily-claim bookkeeping). Wallet balances
    remain on the :class:`~app.database.base.Balance` record. Methods return raw
    records/scalars.
    """

    # -- shop items -------------------------------------------------------

    async def get_items(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches a guild's shop items, cheapest first."""
        return await self.fetch(
            'SELECT * FROM economy_items WHERE guild_id = $1 ORDER BY price, name;', guild_id)

    async def get_item(self, guild_id: int, name: str) -> asyncpg.Record | None:
        """Fetches a single shop item by case-insensitive name."""
        return await self.fetchrow(
            'SELECT * FROM economy_items WHERE guild_id = $1 AND lower(name) = lower($2);', guild_id, name)

    async def create_item(
        self, guild_id: int, name: str, description: str | None, price: int
    ) -> asyncpg.Record | None:
        """Inserts a shop item, returning the row (or ``None`` if the name already exists)."""
        query = """
            INSERT INTO economy_items (guild_id, name, description, price)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, name, description, price)

    async def delete_item(self, guild_id: int, name: str) -> asyncpg.Record | None:
        """Deletes a shop item by name, returning the deleted row (or ``None``)."""
        query = """
            DELETE FROM economy_items
            WHERE guild_id = $1 AND lower(name) = lower($2)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, name)

    # -- inventory --------------------------------------------------------

    async def get_inventory(self, user_id: int, guild_id: int) -> list[asyncpg.Record]:
        """Fetches a member's owned items joined with their shop metadata."""
        query = """
            SELECT i.item_id, i.quantity, e.name, e.description, e.price
            FROM economy_inventory i
            JOIN economy_items e ON e.id = i.item_id
            WHERE i.user_id = $1 AND i.guild_id = $2 AND i.quantity > 0
            ORDER BY e.name;
        """
        return await self.fetch(query, user_id, guild_id)

    async def get_quantity(self, user_id: int, guild_id: int, item_id: int) -> int:
        """Returns how many of an item a member owns (0 if none)."""
        value = await self.fetchval(
            'SELECT quantity FROM economy_inventory WHERE user_id = $1 AND guild_id = $2 AND item_id = $3;',
            user_id, guild_id, item_id,
        )
        return value or 0

    async def add_to_inventory(self, user_id: int, guild_id: int, item_id: int, quantity: int) -> int:
        """Adds ``quantity`` of an item to a member's inventory, returning the new total."""
        query = """
            INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, guild_id, item_id)
                DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity
            RETURNING quantity;
        """
        return await self.fetchval(query, user_id, guild_id, item_id, quantity)

    async def remove_from_inventory(self, user_id: int, guild_id: int, item_id: int, quantity: int) -> int:
        """Removes up to ``quantity`` of an item, clamping at zero; returns the new total."""
        query = """
            UPDATE economy_inventory
            SET quantity = GREATEST(quantity - $4, 0)
            WHERE user_id = $1 AND guild_id = $2 AND item_id = $3
            RETURNING quantity;
        """
        return await self.fetchval(query, user_id, guild_id, item_id, quantity) or 0

    # -- daily rewards ----------------------------------------------------

    async def get_daily(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's daily-claim row (last_claim, streak), or ``None``."""
        return await self.fetchrow(
            'SELECT last_claim, streak FROM economy_dailies WHERE user_id = $1 AND guild_id = $2;',
            user_id, guild_id,
        )

    async def set_daily(
        self, user_id: int, guild_id: int, last_claim: datetime.datetime, streak: int
    ) -> None:
        """Records a member's latest daily claim and streak."""
        query = """
            INSERT INTO economy_dailies (user_id, guild_id, last_claim, streak)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, guild_id)
                DO UPDATE SET last_claim = EXCLUDED.last_claim, streak = EXCLUDED.streak;
        """
        await self.execute(query, user_id, guild_id, last_claim, streak)
