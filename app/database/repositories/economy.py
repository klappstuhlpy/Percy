from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from app.database.repositories.base import BaseRepository
from app.database.repositories.users import WALLET_LOCK_SQL
from app.utils.timetools import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from app.services.economy import CatalogueItem

__all__ = (
    'EconomyRepository',
    'LevelingRepository',
)


class _TradeFailed(Exception):
    """Raised inside the trade transaction to roll it back."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# -- Economy (shop, inventory, boosts, dailies, lottery) --------------------


class EconomyRepository(BaseRepository):
    """Data access for the shop, inventory and daily-reward tables.

    Covers ``economy_items`` (the per-guild shop), ``economy_inventory`` (what each
    member owns) and ``economy_dailies`` (daily-claim bookkeeping). Wallet balances
    remain on the :class:`~app.database.base.Balance` record. Methods return raw
    records/scalars.
    """

    # -- shop items -------------------------------------------------------

    async def get_items(self, guild_id: int, *, for_sale_only: bool = False) -> list[asyncpg.Record]:
        """Fetches a guild's items, cheapest first; ``for_sale_only`` hides activity drops (the shop view)."""
        return await self.fetch(
            'SELECT * FROM economy_items WHERE guild_id = $1 AND (for_sale OR NOT $2) ORDER BY price, name;',
            guild_id, for_sale_only)

    async def ensure_builtin(self, guild_id: int, item: CatalogueItem) -> asyncpg.Record:
        """Materialize (or refresh) the guild row for a built-in item and return it.

        Name, price, emoji, rarity and ``for_sale`` are overwritten from the code-defined ``item`` on every
        call. If an admin already uses the name, the built-in is stored as ``"<name> ✦"`` instead.
        """
        query = """
            INSERT INTO economy_items
                (guild_id, name, description, price, effect, emoji, rarity, builtin_key, for_sale, effect_value)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (guild_id, builtin_key) WHERE builtin_key IS NOT NULL DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description, price = EXCLUDED.price,
                emoji = EXCLUDED.emoji, rarity = EXCLUDED.rarity, for_sale = EXCLUDED.for_sale,
                effect = EXCLUDED.effect, effect_value = EXCLUDED.effect_value
            RETURNING *;
        """
        for name in (item.name, f'{item.name} ✦'):
            try:
                return await self.fetchrow(
                    query, guild_id, name, item.description or None, item.price, item.effect, item.emoji,
                    item.rarity, item.key, item.for_sale, item.effect_value,
                )
            except asyncpg.UniqueViolationError:
                continue
        raise RuntimeError(f'could not materialize built-in item {item.key!r} for guild {guild_id}')

    async def ensure_builtins(self, guild_id: int, items: Iterable[CatalogueItem]) -> None:
        """:meth:`ensure_builtin` for each of ``items`` (used so the shop always lists the tools)."""
        for item in items:
            await self.ensure_builtin(guild_id, item)

    async def get_builtin(self, guild_id: int, key: str) -> asyncpg.Record | None:
        """The guild's materialized row for built-in ``key`` (``None`` until first needed)."""
        return await self.fetchrow(
            'SELECT * FROM economy_items WHERE guild_id = $1 AND builtin_key = $2;', guild_id, key)

    async def use_tool(self, user_id: int, guild_id: int, item_id: int, max_durability: int) -> bool:
        """Spend one use of a tool the member owns; ``False`` when they own none.

        One atomic statement: a fresh unit (``durability`` NULL) starts at ``max_durability``; when the last use
        is spent the unit is consumed and ``durability`` resets to NULL for the next one.
        """
        used = await self.fetchval(
            """
            UPDATE economy_inventory SET
                quantity = CASE WHEN COALESCE(durability, $4) <= 1 THEN quantity - 1 ELSE quantity END,
                durability = CASE WHEN COALESCE(durability, $4) <= 1 THEN NULL ELSE COALESCE(durability, $4) - 1 END
            WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= 1
            RETURNING 1;
            """,
            user_id, guild_id, item_id, max_durability,
        )
        return used is not None

    async def inventory_value(self, user_id: int, guild_id: int) -> int:
        """What a member's whole inventory sells for (``sell_price`` = floor(price x 0.5), mirrored in SQL)."""
        return await self.fetchval(
            """
            SELECT COALESCE(SUM((e.price / 2) * i.quantity), 0)::BIGINT
            FROM economy_inventory i JOIN economy_items e ON e.id = i.item_id
            WHERE i.user_id = $1 AND i.guild_id = $2 AND i.quantity > 0;
            """,
            user_id, guild_id,
        )

    async def get_item(self, guild_id: int, name: str) -> asyncpg.Record | None:
        """Fetches a single shop item by case-insensitive name."""
        return await self.fetchrow(
            'SELECT * FROM economy_items WHERE guild_id = $1 AND lower(name) = lower($2);', guild_id, name)

    async def create_item(
        self,
        guild_id: int,
        name: str,
        description: str | None,
        price: int,
        effect: str = 'none',
        effect_value: int | None = None,
        duration_minutes: int | None = None,
    ) -> asyncpg.Record | None:
        """Inserts a shop item, returning the row (or ``None`` if the name already exists).

        ``effect`` describes what using the item does (see
        :data:`app.services.economy.ITEM_EFFECTS`); ``effect_value`` carries its
        payload (cash amount, bonus percent or role id) and ``duration_minutes``
        how long boost effects last.
        """
        query = """
            INSERT INTO economy_items (guild_id, name, description, price, effect, effect_value, duration_minutes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, name, description, price, effect, effect_value, duration_minutes)

    async def get_item_by_id(self, guild_id: int, item_id: int) -> asyncpg.Record | None:
        """Fetches a single shop item by id (scoped to the guild)."""
        return await self.fetchrow('SELECT * FROM economy_items WHERE guild_id = $1 AND id = $2;', guild_id, item_id)

    async def update_item(self, guild_id: int, item_id: int, values: dict[str, Any]) -> asyncpg.Record | None:
        """Updates a shop item's columns (caller whitelists ``values``); ``None`` on a name clash or missing item."""
        try:
            return await self.update_returning('economy_items', ('guild_id', 'id'), (guild_id, item_id), values)
        except asyncpg.UniqueViolationError:
            return None

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
            SELECT i.item_id, i.quantity, e.name, e.description, e.price,
                   e.effect, e.effect_value, e.duration_minutes, e.emoji, e.rarity,
                   e.builtin_key, e.for_sale, i.durability
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

    async def consume_item(self, user_id: int, guild_id: int, item_id: int, quantity: int = 1) -> int | None:
        """Atomically takes ``quantity`` of an item; returns what is left, or ``None`` if the member lacks them.

        Unlike :meth:`remove_from_inventory` this never clamps, so two concurrent
        uses/sells of the last unit cannot both succeed.
        """
        return await self.fetchval(
            """
            UPDATE economy_inventory SET quantity = quantity - $4
            WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= $4
            RETURNING quantity;
            """,
            user_id, guild_id, item_id, quantity,
        )

    async def debit_cash(self, user_id: int, guild_id: int, amount: int) -> int | None:
        """Atomically takes ``amount`` cash; returns the cash left, or ``None`` when the member cannot afford it.

        The ``cash >= $3`` guard is the check, so two concurrent spends can never both pass a stale read.
        """
        return await self.fetchval(
            'UPDATE economy SET cash = cash - $3 WHERE user_id = $1 AND guild_id = $2 AND $3 >= 0 AND cash >= $3 RETURNING cash;',
            user_id, guild_id, amount,
        )

    async def fine_cash(self, user_id: int, guild_id: int, amount: int) -> int:
        """Atomically takes up to ``amount`` cash, clamped at what the member holds; returns what was taken."""
        return await self.fetchval(
            """
            WITH old AS (SELECT cash FROM economy WHERE user_id = $1 AND guild_id = $2 FOR UPDATE)
            UPDATE economy SET cash = economy.cash - LEAST(GREATEST(old.cash, 0), GREATEST($3, 0)) FROM old
            WHERE user_id = $1 AND guild_id = $2
            RETURNING LEAST(GREATEST(old.cash, 0), GREATEST($3, 0));
            """,
            user_id, guild_id, amount,
        ) or 0

    async def transfer_cash(self, from_user_id: int, to_user_id: int, guild_id: int, amount: int) -> int | None:
        """Atomically moves ``amount`` cash between members; returns the giver's cash left, or ``None`` if short.

        Both wallets are created if missing and locked in id order, then debit and credit commit together.
        """
        async with self.acquire() as conn, conn.transaction():
            await self._lock_wallets(conn, guild_id, [from_user_id, to_user_id])
            left = await conn.fetchval(
                'UPDATE economy SET cash = cash - $3 WHERE user_id = $1 AND guild_id = $2 AND cash >= $3 RETURNING cash;',
                from_user_id, guild_id, amount,
            )
            if left is None:
                return None
            await conn.execute(
                'UPDATE economy SET cash = cash + $3 WHERE user_id = $1 AND guild_id = $2;', to_user_id, guild_id, amount)
            return left

    async def wipe_cash(self, user_id: int, guild_id: int) -> int:
        """Zeroes a member's cash (bank untouched) and returns how much was lost."""
        return await self.fetchval(
            """
            WITH old AS (SELECT cash FROM economy WHERE user_id = $1 AND guild_id = $2 FOR UPDATE)
            UPDATE economy SET cash = 0 FROM old
            WHERE user_id = $1 AND guild_id = $2
            RETURNING old.cash;
            """,
            user_id, guild_id,
        ) or 0

    async def delete_item_by_id(self, guild_id: int, item_id: int) -> asyncpg.Record | None:
        """Deletes a shop item by id, returning the deleted row (or ``None``)."""
        return await self.fetchrow(
            'DELETE FROM economy_items WHERE guild_id = $1 AND id = $2 RETURNING *;', guild_id, item_id)

    async def give_item(self, user_id: int, guild_id: int, item_id: int, quantity: int) -> tuple[str, int]:
        """Grants ``quantity`` of an item for free, honouring ``max_per_user``.

        Returns ``(status, owned)`` with status ``ok``, ``missing`` or ``limit``. Stock is untouched: a grant is
        not a sale.
        """
        async with self.acquire() as conn, conn.transaction():
            item = await conn.fetchrow(
                'SELECT max_per_user FROM economy_items WHERE guild_id = $1 AND id = $2 FOR UPDATE;', guild_id, item_id)
            if item is None:
                return 'missing', 0
            owned = await conn.fetchval(
                'SELECT quantity FROM economy_inventory WHERE user_id = $1 AND guild_id = $2 AND item_id = $3;',
                user_id, guild_id, item_id,
            ) or 0
            if item['max_per_user'] is not None and owned + quantity > item['max_per_user']:
                return 'limit', owned
            total = await conn.fetchval(
                """
                INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, guild_id, item_id)
                    DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity
                RETURNING quantity;
                """,
                user_id, guild_id, item_id, quantity,
            )
            return 'ok', total

    async def buy_item(
        self, user_id: int, guild_id: int, item_id: int, quantity: int, unit_price: int | None = None
    ) -> tuple[str, asyncpg.Record | None]:
        """Purchases ``quantity`` of an item in one transaction: expiry, stock, holding limit and cash.

        ``unit_price`` is the effective price per unit (a daily sale lowers it); it
        defaults to the item's list price.

        Returns ``(status, item)`` where ``status`` is a
        :class:`~app.services.economy.PurchaseStatus` value. The item row is locked
        so concurrent buyers cannot oversell limited stock; cash is only debited
        when the member can afford it. The member's balance row must already exist.
        """
        async with self.acquire() as conn, conn.transaction():
            item = await conn.fetchrow(
                'SELECT * FROM economy_items WHERE guild_id = $1 AND id = $2 FOR UPDATE;', guild_id, item_id)
            if item is None:
                return 'missing', None
            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            if item['available_until'] is not None and item['available_until'] <= now:
                return 'expired', item
            if item['stock'] is not None and item['stock'] < quantity:
                return 'sold_out', item
            if item['max_per_user'] is not None:
                owned = await conn.fetchval(
                    'SELECT quantity FROM economy_inventory WHERE user_id = $1 AND guild_id = $2 AND item_id = $3;',
                    user_id, guild_id, item_id,
                ) or 0
                if owned + quantity > item['max_per_user']:
                    return 'limit', item
            paid = await conn.fetchval(
                """
                UPDATE economy SET cash = cash - $3
                WHERE user_id = $1 AND guild_id = $2 AND cash >= $3
                RETURNING cash;
                """,
                user_id, guild_id, (item['price'] if unit_price is None else unit_price) * quantity,
            )
            if paid is None:
                return 'funds', item
            await conn.execute(
                """
                INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, guild_id, item_id)
                    DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity;
                """,
                user_id, guild_id, item_id, quantity,
            )
            if item['stock'] is not None:
                await conn.execute('UPDATE economy_items SET stock = stock - $2 WHERE id = $1;', item_id, quantity)
            return 'ok', item

    async def sell_item(self, user_id: int, guild_id: int, item_id: int, quantity: int, payout: int) -> int | None:
        """Atomically removes ``quantity`` of an item and credits ``payout`` cash; ``None`` if the member lacks them."""
        async with self.acquire() as conn, conn.transaction():
            left = await conn.fetchval(
                """
                UPDATE economy_inventory SET quantity = quantity - $4
                WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= $4
                RETURNING quantity;
                """,
                user_id, guild_id, item_id, quantity,
            )
            if left is None:
                return None
            # A member who was only ever *given* items may have no wallet; without one the payout would vanish.
            await self._lock_wallets(conn, guild_id, [user_id])
            await conn.execute(
                'UPDATE economy SET cash = cash + $3 WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id, payout)
            return left

    # -- timed boosts -------------------------------------------------------

    async def add_boost(
        self, user_id: int, guild_id: int, kind: str, multiplier: float, duration_minutes: int
    ) -> datetime.datetime:
        """Activates (or extends) a timed boost, returning the new expiry (naive UTC).

        Using another item of the same ``kind`` while one is active extends the
        remaining time and overwrites the multiplier with the new item's value.
        """
        query = """
            INSERT INTO economy_boosts (user_id, guild_id, kind, multiplier, expires_at)
            VALUES ($1, $2, $3, $4, (now() at time zone 'utc') + make_interval(mins => $5))
            ON CONFLICT (user_id, guild_id, kind) DO UPDATE
                SET multiplier = EXCLUDED.multiplier,
                    expires_at = GREATEST(economy_boosts.expires_at, now() at time zone 'utc')
                                 + make_interval(mins => $5)
            RETURNING expires_at;
        """
        return await self.fetchval(query, user_id, guild_id, kind, multiplier, duration_minutes)

    async def has_active_boost(self, user_id: int, guild_id: int, kind: str) -> bool:
        """Whether the member currently has a running boost of ``kind`` (e.g. a rob shield)."""
        value = await self.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM economy_boosts
                WHERE user_id = $1 AND guild_id = $2 AND kind = $3 AND expires_at > (now() at time zone 'utc')
            );
            """,
            user_id, guild_id, kind,
        )
        return bool(value)

    async def get_passive(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """The member's passive-mode row (``enabled``, ``changed_at``), or ``None`` if they never toggled."""
        return await self.fetchrow(
            'SELECT enabled, changed_at FROM economy_passive WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)

    async def is_passive(self, user_id: int, guild_id: int) -> bool:
        """Whether the member opted out of robbing and heists."""
        value = await self.fetchval(
            'SELECT enabled FROM economy_passive WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)
        return bool(value)

    async def set_passive(self, user_id: int, guild_id: int, enabled: bool) -> None:
        """Turns passive mode on or off, stamping ``changed_at`` for the toggle cooldown."""
        await self.execute(
            """
            INSERT INTO economy_passive (user_id, guild_id, enabled) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, guild_id) DO UPDATE
                SET enabled = EXCLUDED.enabled, changed_at = now() at time zone 'utc';
            """,
            user_id, guild_id, enabled,
        )

    async def get_boost_multiplier(self, user_id: int, guild_id: int, kind: str) -> float:
        """The member's active multiplier for ``kind`` (``1.0`` when no boost is running)."""
        value = await self.fetchval(
            """
            SELECT multiplier FROM economy_boosts
            WHERE user_id = $1 AND guild_id = $2 AND kind = $3 AND expires_at > (now() at time zone 'utc');
            """,
            user_id, guild_id, kind,
        )
        return value or 1.0

    async def get_active_boosts(self, user_id: int, guild_id: int) -> list[asyncpg.Record]:
        """Fetches a member's running boosts as ``(kind, multiplier, expires_at)`` rows."""
        return await self.fetch(
            """
            SELECT kind, multiplier, expires_at FROM economy_boosts
            WHERE user_id = $1 AND guild_id = $2 AND expires_at > (now() at time zone 'utc')
            ORDER BY kind;
            """,
            user_id, guild_id,
        )

    # -- daily rewards ----------------------------------------------------

    async def get_daily(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's claim row (last_claim, streak, last_weekly, last_monthly), or ``None``."""
        return await self.fetchrow(
            """
            SELECT last_claim, streak, last_weekly, last_monthly
            FROM economy_dailies WHERE user_id = $1 AND guild_id = $2;
            """,
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
        await self.execute(query, user_id, guild_id, ensure_utc(last_claim).replace(tzinfo=None), streak)

    async def get_last_vote_claim(self, user_id: int, guild_id: int) -> datetime.datetime | None:
        """When the member last claimed a vote reward in this guild (naive UTC), or ``None``."""
        return await self.fetchval(
            'SELECT last_vote_claim FROM economy_dailies WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)

    async def claim_vote(
        self, user_id: int, guild_id: int, voted_at: datetime.datetime, now: datetime.datetime
    ) -> bool:
        """Atomically stamp a vote claim; ``True`` only for the one caller that wins (not yet claimed since ``voted_at``)."""
        won = await self.fetchval(
            """
            INSERT INTO economy_dailies (user_id, guild_id, last_claim, streak, last_vote_claim)
            VALUES ($1, $2, NULL, 0, $4)
            ON CONFLICT (user_id, guild_id) DO UPDATE SET last_vote_claim = EXCLUDED.last_vote_claim
                WHERE economy_dailies.last_vote_claim IS NULL OR economy_dailies.last_vote_claim < $3
            RETURNING 1;
            """,
            user_id, guild_id,
            ensure_utc(voted_at).replace(tzinfo=None), ensure_utc(now).replace(tzinfo=None),
        )
        return won is not None

    # -- leaderboards: (user_id, value) rows, highest first, one query each -----

    async def top_networth(self, guild_id: int, limit: int = 100) -> list[asyncpg.Record]:
        """Cash + bank + inventory sell value (``price / 2``, as :meth:`inventory_value`), in one grouped query."""
        return await self.fetch(
            """
            SELECT e.user_id, (e.cash + e.bank + COALESCE(v.value, 0))::BIGINT AS value
            FROM economy e
            LEFT JOIN (
                SELECT i.user_id, SUM((it.price / 2) * i.quantity) AS value
                FROM economy_inventory i JOIN economy_items it ON it.id = i.item_id
                WHERE i.guild_id = $1 AND i.quantity > 0
                GROUP BY i.user_id
            ) v ON v.user_id = e.user_id
            WHERE e.guild_id = $1
            ORDER BY value DESC LIMIT $2;
            """,
            guild_id, limit,
        )

    async def top_prestige(self, guild_id: int, limit: int = 100) -> list[asyncpg.Record]:
        """Members by prestige level (level 0 excluded)."""
        return await self.fetch(
            'SELECT user_id, level AS value FROM economy_prestige WHERE guild_id = $1 AND level > 0 '
            'ORDER BY level DESC LIMIT $2;', guild_id, limit)

    async def top_streak(self, guild_id: int, limit: int = 100) -> list[asyncpg.Record]:
        """Members by daily streak."""
        return await self.fetch(
            'SELECT user_id, streak AS value FROM economy_dailies WHERE guild_id = $1 AND streak > 0 '
            'ORDER BY streak DESC LIMIT $2;', guild_id, limit)

    async def top_game_profit(self, guild_id: int, limit: int = 100) -> list[asyncpg.Record]:
        """Members by net profit across all games (``game_stats``)."""
        return await self.fetch(
            'SELECT user_id, SUM(profit)::BIGINT AS value FROM game_stats WHERE guild_id = $1 '
            'GROUP BY user_id ORDER BY value DESC LIMIT $2;', guild_id, limit)

    async def set_weekly(self, user_id: int, guild_id: int, when: datetime.datetime) -> None:
        """Records a member's latest weekly claim."""
        query = """
            INSERT INTO economy_dailies (user_id, guild_id, last_claim, streak, last_weekly)
            VALUES ($1, $2, NULL, 0, $3)
            ON CONFLICT (user_id, guild_id) DO UPDATE SET last_weekly = EXCLUDED.last_weekly;
        """
        await self.execute(query, user_id, guild_id, ensure_utc(when).replace(tzinfo=None))

    async def set_monthly(self, user_id: int, guild_id: int, when: datetime.datetime) -> None:
        """Records a member's latest monthly claim."""
        query = """
            INSERT INTO economy_dailies (user_id, guild_id, last_claim, streak, last_monthly)
            VALUES ($1, $2, NULL, 0, $3)
            ON CONFLICT (user_id, guild_id) DO UPDATE SET last_monthly = EXCLUDED.last_monthly;
        """
        await self.execute(query, user_id, guild_id, ensure_utc(when).replace(tzinfo=None))

    # -- guild settings ----------------------------------------------------

    async def get_settings(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches a guild's economy settings row, or ``None`` when everything is default."""
        return await self.fetchrow('SELECT * FROM economy_settings WHERE guild_id = $1;', guild_id)

    async def update_settings(self, guild_id: int, values: dict[str, Any]) -> asyncpg.Record:
        """Upserts the given settings fields for a guild and returns the full row.

        ``values`` keys must be column names of ``economy_settings`` (the caller
        whitelists them); unspecified fields keep their current/default values.
        """
        columns = list(values)
        placeholders = ', '.join(f'${i + 2}' for i in range(len(columns)))
        updates = ', '.join(f'{column} = EXCLUDED.{column}' for column in columns)
        query = f"""
            INSERT INTO economy_settings (guild_id, {', '.join(columns)})
            VALUES ($1, {placeholders})
            ON CONFLICT (guild_id) DO UPDATE SET {updates}
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, *values.values())

    # -- jobs ---------------------------------------------------------------

    async def get_job(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's job row (job_id, shifts, hired_at), or ``None`` if never employed."""
        return await self.fetchrow(
            'SELECT job_id, shifts, hired_at FROM economy_jobs WHERE user_id = $1 AND guild_id = $2;',
            user_id, guild_id,
        )

    async def set_job(self, user_id: int, guild_id: int, job_id: str) -> None:
        """Hires a member into ``job_id``, keeping their lifetime shift count."""
        query = """
            INSERT INTO economy_jobs (user_id, guild_id, job_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, guild_id)
                DO UPDATE SET job_id = EXCLUDED.job_id, hired_at = (now() at time zone 'utc');
        """
        await self.execute(query, user_id, guild_id, job_id)

    async def add_shift(self, user_id: int, guild_id: int, default_job_id: str) -> int:
        """Counts one worked shift (hiring into ``default_job_id`` if needed); returns lifetime shifts."""
        query = """
            INSERT INTO economy_jobs (user_id, guild_id, job_id, shifts)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (user_id, guild_id)
                DO UPDATE SET shifts = economy_jobs.shifts + 1
            RETURNING shifts;
        """
        return await self.fetchval(query, user_id, guild_id, default_job_id)

    # -- prestige -----------------------------------------------------------

    async def get_prestige(self, user_id: int, guild_id: int) -> int:
        """The member's prestige level (0 if they never prestiged)."""
        value = await self.fetchval(
            'SELECT level FROM economy_prestige WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)
        return value or 0

    async def increment_prestige(self, user_id: int, guild_id: int) -> int:
        """Advances a member one prestige level, returning the new level."""
        query = """
            INSERT INTO economy_prestige (user_id, guild_id, level, last_prestige)
            VALUES ($1, $2, 1, (now() at time zone 'utc'))
            ON CONFLICT (user_id, guild_id)
                DO UPDATE SET level = economy_prestige.level + 1,
                              last_prestige = (now() at time zone 'utc')
            RETURNING level;
        """
        return await self.fetchval(query, user_id, guild_id)

    # -- achievements ---------------------------------------------------------

    async def get_achievements(self, user_id: int, guild_id: int) -> list[asyncpg.Record]:
        """Fetches a member's earned achievements as ``(achievement, earned_at)`` rows."""
        return await self.fetch(
            """
            SELECT achievement, earned_at FROM economy_achievements
            WHERE user_id = $1 AND guild_id = $2 ORDER BY earned_at;
            """,
            user_id, guild_id,
        )

    async def award_achievements(self, user_id: int, guild_id: int, achievement_ids: Sequence[str]) -> list[str]:
        """Awards the given achievements (skipping already-earned ones); returns the new ids."""
        if not achievement_ids:
            return []
        query = """
            INSERT INTO economy_achievements (user_id, guild_id, achievement)
            SELECT $1, $2, unnest($3::text[])
            ON CONFLICT DO NOTHING
            RETURNING achievement;
        """
        rows = await self.fetch(query, user_id, guild_id, list(achievement_ids))
        return [row['achievement'] for row in rows]

    # -- pets -----------------------------------------------------------------

    async def get_pet(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's pet row, or ``None`` if they have none."""
        return await self.fetchrow(
            'SELECT * FROM economy_pets WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)

    async def create_pet(self, user_id: int, guild_id: int, species: str, name: str) -> asyncpg.Record | None:
        """Adopts a pet for a member, returning the row (or ``None`` if they already own one)."""
        query = """
            INSERT INTO economy_pets (user_id, guild_id, species, name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, user_id, guild_id, species, name)

    async def update_pet(self, user_id: int, guild_id: int, values: dict[str, Any]) -> asyncpg.Record | None:
        """Updates a pet's fields (name / last_fed / last_claim) and returns the row."""
        return await self.update_returning('economy_pets', ('user_id', 'guild_id'), (user_id, guild_id), values)

    async def delete_pet(self, user_id: int, guild_id: int) -> None:
        """Removes a member's pet."""
        await self.delete_where('economy_pets', ('user_id', 'guild_id'), (user_id, guild_id))

    # -- daily quests ----------------------------------------------------------

    async def get_quests(self, user_id: int, guild_id: int, day: datetime.date) -> list[asyncpg.Record]:
        """Fetches a member's quest rows for ``day`` (empty until first ensured)."""
        return await self.fetch(
            """
            SELECT quest, kind, goal, progress, reward, completed
            FROM economy_quests
            WHERE user_id = $1 AND guild_id = $2 AND day = $3
            ORDER BY quest;
            """,
            user_id, guild_id, day,
        )

    async def create_quests(
        self, user_id: int, guild_id: int, day: datetime.date,
        quests: Sequence[tuple[str, str, int, int]],
    ) -> None:
        """Inserts a day's quest board as ``(key, kind, goal, reward)`` tuples (idempotent)."""
        query = """
            INSERT INTO economy_quests (user_id, guild_id, day, quest, kind, goal, reward)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING;
        """
        async with self.acquire() as conn:
            await conn.executemany(
                query, [(user_id, guild_id, day, key, kind, goal, reward) for key, kind, goal, reward in quests])

    async def advance_quests(
        self, user_id: int, guild_id: int, day: datetime.date, kind: str, amount: int = 1
    ) -> list[asyncpg.Record]:
        """Advances every open quest of ``kind`` by ``amount``; returns the rows that just completed."""
        query = """
            UPDATE economy_quests
            SET progress = LEAST(progress + $5, goal),
                completed = (progress + $5 >= goal)
            WHERE user_id = $1 AND guild_id = $2 AND day = $3 AND kind = $4 AND NOT completed
            RETURNING quest, goal, reward, completed;
        """
        rows = await self.fetch(query, user_id, guild_id, day, kind, amount)
        return [row for row in rows if row['completed']]

    async def count_completed_quests(self, user_id: int, guild_id: int) -> int:
        """How many daily quests a member has completed, lifetime."""
        value = await self.fetchval(
            'SELECT COUNT(*) FROM economy_quests WHERE user_id = $1 AND guild_id = $2 AND completed;',
            user_id, guild_id,
        )
        return value or 0

    # -- inventory transfers ---------------------------------------------------

    async def transfer_item(
        self, from_user_id: int, to_user_id: int, guild_id: int, item_id: int, quantity: int
    ) -> bool:
        """Atomically moves ``quantity`` of an item between members; ``False`` if the giver lacks them."""
        async with self.acquire() as conn, conn.transaction():
            removed = await conn.fetchval(
                """
                UPDATE economy_inventory
                SET quantity = quantity - $4
                WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= $4
                RETURNING quantity;
                """,
                from_user_id, guild_id, item_id, quantity,
            )
            if removed is None:
                return False
            await conn.execute(
                """
                INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, guild_id, item_id)
                    DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity;
                """,
                to_user_id, guild_id, item_id, quantity,
            )
            return True

    async def execute_trade(
        self,
        guild_id: int,
        a_id: int,
        b_id: int,
        a_items: Sequence[tuple[int, int]],
        a_cash: int,
        b_items: Sequence[tuple[int, int]],
        b_cash: int,
    ) -> str:
        """Atomically swaps two members' offers; returns ``'ok'`` or the failure (``cash``/``items``/``limit``).

        Both wallets are locked in id order first (no deadlock between opposite
        trades). Every debit is conditional (``cash >= n`` / ``quantity >= n``), all
        removals happen before any credit, and a receiver's ``max_per_user`` is
        checked against that final state. Any shortfall rolls the whole trade back.
        """
        gives = ((a_id, b_id, a_items, a_cash), (b_id, a_id, b_items, b_cash))
        try:
            async with self.acquire() as conn, conn.transaction():
                # Creates missing wallets too: crediting a receiver with no row would update nothing and burn the cash.
                await self._lock_wallets(conn, guild_id, [a_id, b_id])
                for giver, _, _, cash in gives:
                    if cash and await conn.fetchval(
                        'UPDATE economy SET cash = cash - $3 WHERE user_id = $1 AND guild_id = $2 AND cash >= $3 RETURNING cash;',
                        giver, guild_id, cash,
                    ) is None:
                        raise _TradeFailed('cash')
                for giver, _, items, _ in gives:
                    for item_id, quantity in items:
                        if await conn.fetchval(
                            """
                            UPDATE economy_inventory SET quantity = quantity - $4
                            WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= $4
                            RETURNING quantity;
                            """,
                            giver, guild_id, item_id, quantity,
                        ) is None:
                            raise _TradeFailed('items')
                for _, receiver, items, cash in gives:
                    if cash:
                        await conn.execute(
                            'UPDATE economy SET cash = cash + $3 WHERE user_id = $1 AND guild_id = $2;', receiver, guild_id, cash)
                    for item_id, quantity in items:
                        limit = await conn.fetchval(
                            'SELECT max_per_user FROM economy_items WHERE guild_id = $1 AND id = $2;', guild_id, item_id)
                        held = await conn.fetchval(
                            """
                            INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (user_id, guild_id, item_id)
                                DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity
                            RETURNING quantity;
                            """,
                            receiver, guild_id, item_id, quantity,
                        )
                        if limit is not None and held > limit:
                            raise _TradeFailed('limit')
        except _TradeFailed as failure:
            return failure.reason
        return 'ok'

    # -- player market ------------------------------------------------------

    @staticmethod
    async def _lock_wallets(conn: asyncpg.Connection, guild_id: int, user_ids: list[int]) -> None:
        """Makes sure each member has a wallet row, then locks them in id order (no deadlock between opposite buys)."""
        for user_id in sorted(user_ids):
            await conn.execute(WALLET_LOCK_SQL, user_id, guild_id)  # same gate as UsersRepository.get_balance_record
            await conn.execute(
                'INSERT INTO economy (user_id, guild_id, cash, bank) SELECT $1, $2, 0, 0 '
                'WHERE NOT EXISTS (SELECT 1 FROM economy WHERE user_id = $1 AND guild_id = $2);',
                user_id, guild_id,
            )
        await conn.fetch(
            'SELECT 1 FROM economy WHERE guild_id = $1 AND user_id = ANY($2::bigint[]) ORDER BY user_id FOR UPDATE;',
            guild_id, user_ids,
        )

    async def market_list(
        self, guild_id: int, seller_id: int, item_id: int, quantity: int, price_each: int, max_listings: int
    ) -> asyncpg.Record | str:
        """Escrows ``quantity`` of an item into a new listing; returns the row, or ``'items'``/``'listings'``.

        The seller's wallet row is locked first so two simultaneous listings cannot both pass the
        listing cap. The inventory debit is conditional, so the escrow can never exceed what is owned.
        Like trades, escrow carries no tool durability: a returned or bought tool comes back fresh.
        """
        async with self.acquire() as conn, conn.transaction():
            await self._lock_wallets(conn, guild_id, [seller_id])
            open_count = await conn.fetchval(
                'SELECT COUNT(*) FROM economy_market WHERE guild_id = $1 AND seller_id = $2;', guild_id, seller_id)
            if open_count >= max_listings:
                return 'listings'
            taken = await conn.fetchval(
                """
                UPDATE economy_inventory SET quantity = quantity - $4
                WHERE user_id = $1 AND guild_id = $2 AND item_id = $3 AND quantity >= $4
                RETURNING quantity;
                """,
                seller_id, guild_id, item_id, quantity,
            )
            if taken is None:
                return 'items'
            return await conn.fetchrow(
                """
                INSERT INTO economy_market (guild_id, seller_id, item_id, quantity, price_each)
                VALUES ($1, $2, $3, $4, $5) RETURNING *;
                """,
                guild_id, seller_id, item_id, quantity, price_each,
            )

    async def market_count(self, guild_id: int, seller_id: int) -> int:
        """How many listings a member has open."""
        return await self.fetchval(
            'SELECT COUNT(*) FROM economy_market WHERE guild_id = $1 AND seller_id = $2;', guild_id, seller_id)

    async def market_listings(
        self, guild_id: int, *, item_id: int | None = None, seller_id: int | None = None
    ) -> list[asyncpg.Record]:
        """Open listings joined with item metadata, newest first, optionally for one item or seller."""
        return await self.fetch(
            """
            SELECT m.id, m.seller_id, m.item_id, m.quantity, m.price_each, m.created_at, e.name, e.emoji, e.rarity
            FROM economy_market m JOIN economy_items e ON e.id = m.item_id
            WHERE m.guild_id = $1 AND ($2::bigint IS NULL OR m.item_id = $2) AND ($3::bigint IS NULL OR m.seller_id = $3)
            ORDER BY m.created_at DESC, m.id DESC;
            """,
            guild_id, item_id, seller_id,
        )

    async def market_cancel(self, guild_id: int, listing_id: int, seller_id: int) -> asyncpg.Record | None:
        """Removes the seller's listing and returns the escrow to them; ``None`` if it is not theirs/gone."""
        async with self.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                'DELETE FROM economy_market WHERE id = $1 AND guild_id = $2 AND seller_id = $3 RETURNING *;',
                listing_id, guild_id, seller_id,
            )
            if row is None:
                return None
            await conn.execute(
                """
                INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity) VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, guild_id, item_id)
                    DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity;
                """,
                seller_id, guild_id, row['item_id'], row['quantity'],
            )
            return row

    async def market_buy(
        self, guild_id: int, buyer_id: int, listing_id: int, quantity: int | None, gross_net: Callable[[int, int], tuple[int, int, int]]
    ) -> tuple[str, dict[str, Any]]:
        """Buys ``quantity`` (``None`` = the whole listing) from a listing in one transaction.

        ``gross_net(price_each, quantity)`` returns ``(gross, tax, net)`` (``sale_proceeds``). Returns
        ``('ok', info)`` with ``seller_id``/``item_id``/``quantity``/``gross``/``tax``/``net``, or a failure code
        (``missing``, ``own``, ``quantity``, ``cash``, ``limit``) with a (possibly empty) info dict. The listing row
        is locked (``FOR UPDATE``) so two buyers cannot take the same units, both wallets are locked in id order,
        the buyer's debit is conditional and the buyer's ``max_per_user`` is enforced; any failure rolls back.
        """
        try:
            async with self.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(
                    'SELECT * FROM economy_market WHERE id = $1 AND guild_id = $2 FOR UPDATE;', listing_id, guild_id)
                if row is None:
                    return 'missing', {}
                if row['seller_id'] == buyer_id:
                    return 'own', {}
                quantity = row['quantity'] if quantity is None else quantity
                if quantity > row['quantity']:
                    return 'quantity', {'available': row['quantity']}
                gross, tax, net = gross_net(row['price_each'], quantity)
                await self._lock_wallets(conn, guild_id, sorted([buyer_id, row['seller_id']]))
                paid = await conn.fetchval(
                    'UPDATE economy SET cash = cash - $3 WHERE user_id = $1 AND guild_id = $2 AND cash >= $3 RETURNING cash;',
                    buyer_id, guild_id, gross,
                )
                if paid is None:
                    raise _TradeFailed('cash')
                credited = await conn.execute(
                    'UPDATE economy SET cash = cash + $3 WHERE user_id = $1 AND guild_id = $2;',
                    row['seller_id'], guild_id, net,
                )
                if credited != 'UPDATE 1':
                    raise _TradeFailed('seller')
                if quantity == row['quantity']:
                    await conn.execute('DELETE FROM economy_market WHERE id = $1;', listing_id)
                else:
                    await conn.execute(
                        'UPDATE economy_market SET quantity = quantity - $2 WHERE id = $1;', listing_id, quantity)
                limit = await conn.fetchval(
                    'SELECT max_per_user FROM economy_items WHERE guild_id = $1 AND id = $2;', guild_id, row['item_id'])
                held = await conn.fetchval(
                    """
                    INSERT INTO economy_inventory (user_id, guild_id, item_id, quantity) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, guild_id, item_id)
                        DO UPDATE SET quantity = economy_inventory.quantity + EXCLUDED.quantity
                    RETURNING quantity;
                    """,
                    buyer_id, guild_id, row['item_id'], quantity,
                )
                if limit is not None and held > limit:
                    raise _TradeFailed('limit')
        except _TradeFailed as failure:
            return failure.reason, {'gross': gross}
        return 'ok', {
            'seller_id': row['seller_id'], 'item_id': row['item_id'], 'quantity': quantity,
            'gross': gross, 'tax': tax, 'net': net,
        }

    async def record_discovery(self, user_id: int, guild_id: int, builtin_key: str) -> None:
        """Note that the member has found catalogue drop ``builtin_key`` (insert-if-absent)."""
        await self.execute(
            'INSERT INTO economy_discoveries (user_id, guild_id, builtin_key) VALUES ($1, $2, $3) '
            'ON CONFLICT DO NOTHING;',
            user_id, guild_id, builtin_key,
        )

    async def get_discoveries(self, user_id: int, guild_id: int) -> list[str]:
        """The catalogue keys the member has ever found."""
        rows = await self.fetch(
            'SELECT builtin_key FROM economy_discoveries WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)
        return [r['builtin_key'] for r in rows]

    # -- profile ---------------------------------------------------------------

    async def get_profile_title(self, user_id: int, guild_id: int) -> str | None:
        """The member's equipped profile title, or ``None``."""
        return await self.fetchval(
            'SELECT title FROM economy_profiles WHERE user_id = $1 AND guild_id = $2;', user_id, guild_id)

    async def set_profile_title(self, user_id: int, guild_id: int, title: str | None) -> None:
        """Equips ``title`` (``None`` clears it)."""
        await self.execute(
            'INSERT INTO economy_profiles (user_id, guild_id, title) VALUES ($1, $2, $3) '
            'ON CONFLICT (user_id, guild_id) DO UPDATE SET title = EXCLUDED.title;',
            user_id, guild_id, title,
        )

    async def count_items(self, user_id: int, guild_id: int) -> int:
        """The total number of items (summed quantities) a member owns."""
        value = await self.fetchval(
            'SELECT COALESCE(SUM(quantity), 0) FROM economy_inventory WHERE user_id = $1 AND guild_id = $2;',
            user_id, guild_id,
        )
        return int(value or 0)

    # -- lottery ----------------------------------------------------------

    async def get_lottery(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches the active lottery for a guild, or ``None`` if none is running."""
        return await self.fetchrow('SELECT * FROM economy_lottery WHERE guild_id = $1;', guild_id)

    async def create_lottery(
        self, guild_id: int, channel_id: int, ticket_price: int, jackpot: int, ends_at: datetime.datetime
    ) -> asyncpg.Record | None:
        """Starts a lottery, returning the row (or ``None`` if one already runs)."""
        query = """
            INSERT INTO economy_lottery (guild_id, channel_id, ticket_price, jackpot, ends_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id) DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, channel_id, ticket_price, jackpot, ensure_utc(ends_at).replace(tzinfo=None))

    async def add_lottery_tickets(self, guild_id: int, user_id: int, tickets: int, cost: int) -> int | None:
        """Adds tickets for a member and grows the jackpot; returns the member's new ticket total.

        ``None`` when the lottery is gone (ended meanwhile): nothing is written, so the caller can refund.
        """
        async with self.acquire() as conn, conn.transaction():
            if await conn.fetchval(
                'UPDATE economy_lottery SET jackpot = jackpot + $2 WHERE guild_id = $1 RETURNING 1;', guild_id, cost
            ) is None:
                return None
            return await conn.fetchval(
                """
                INSERT INTO economy_lottery_entries (guild_id, user_id, tickets)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id)
                    DO UPDATE SET tickets = economy_lottery_entries.tickets + EXCLUDED.tickets
                RETURNING tickets;
                """,
                guild_id, user_id, tickets,
            )

    async def count_balances(self, guild_id: int) -> int:
        """Counts the wallets the balances leaderboard lists (non-empty)."""
        return await self.fetchval(
            'SELECT count(*) FROM economy WHERE guild_id = $1 AND (cash + bank) > 0;', guild_id)

    async def get_lottery_entries(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every (user_id, tickets) entry for a guild's lottery."""
        return await self.fetch(
            'SELECT user_id, tickets FROM economy_lottery_entries WHERE guild_id = $1;', guild_id)

    async def get_lottery_tickets(self, guild_id: int, user_id: int) -> int:
        """Returns how many tickets a member holds in the current lottery (0 if none)."""
        value = await self.fetchval(
            'SELECT tickets FROM economy_lottery_entries WHERE guild_id = $1 AND user_id = $2;',
            guild_id, user_id,
        )
        return value or 0

    async def delete_lottery(self, guild_id: int) -> None:
        """Ends a lottery, removing it and its entries (entries cascade)."""
        await self.delete_where("economy_lottery", ("guild_id",), (guild_id,))


# -- Leveling (level_config, levels, xp_history) ---------------------------


class LevelingRepository(BaseRepository):
    """Data access for the ``level_config`` and ``levels`` tables.

    The methods return raw records and scalars; building the
    ``GuildLevelConfig`` / ``LevelConfig`` domain objects (and caching the guild
    config) is left to the ``Leveling`` cog, which owns the ``cog`` reference each
    record needs.
    """

    # -- level_config (per-guild settings) --------------------------------

    async def get_guild_config_record(self, guild_id: int) -> asyncpg.Record | None:
        """Fetches the leveling config row for a guild, or ``None`` if unconfigured."""
        return await self.fetchrow("SELECT * FROM level_config WHERE id = $1 LIMIT 1;", guild_id)

    async def create_guild_config(self, guild_id: int, enabled: bool) -> asyncpg.Record:
        """Inserts a new leveling config row for a guild and returns it."""
        query = "INSERT INTO level_config (id, enabled) VALUES ($1, $2) RETURNING *;"
        return await self.fetchrow(query, guild_id, enabled)

    async def update_guild_config(
            self,
            guild_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record | None:
        """Updates a guild's level_config row and returns the full updated record."""
        return await self.update_returning("level_config", ("id",), (guild_id,), values, connection=connection)

    # -- levels (per-member XP rows) --------------------------------------

    async def get_or_create_user_level(self, user_id: int, guild_id: int) -> asyncpg.Record:
        """Fetches a member's level row, inserting a default one if absent."""
        record = await self.fetchrow("SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
        if not record:
            # A new member's first two messages race here; the upsert keeps the loser from a UniqueViolation.
            record = await self.fetchrow(
                "INSERT INTO levels (user_id, guild_id) VALUES ($1, $2) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET user_id = EXCLUDED.user_id RETURNING *;",
                user_id, guild_id)
        return record

    async def add_xp(self, user_id: int, guild_id: int, xp: int, messages: int = 0) -> asyncpg.Record:
        """Atomically adds XP / messages and returns the row, so concurrent gains never overwrite each other."""
        return await self.fetchrow(
            "UPDATE levels SET xp = xp + $3, messages = messages + $4 WHERE user_id = $1 AND guild_id = $2 RETURNING *;",
            user_id, guild_id, xp, messages)

    async def shift_level(
            self, user_id: int, guild_id: int, from_level: int, to_level: int, xp_delta: int
    ) -> asyncpg.Record | None:
        """Persists a level-up/down carry as a delta; a no-op (``None``) if the level moved meanwhile."""
        return await self.fetchrow(
            "UPDATE levels SET level = $4, xp = GREATEST(xp + $5, 0) "
            "WHERE user_id = $1 AND guild_id = $2 AND level = $3 RETURNING *;",
            user_id, guild_id, from_level, to_level, xp_delta)

    async def count_leaderboard(self, guild_id: int) -> int:
        """Counts the members :meth:`get_leaderboard` ranks."""
        return await self.fetchval(
            "SELECT count(*) FROM levels WHERE guild_id = $1 AND (messages > 0 OR level > 0 OR xp > 0);", guild_id)

    async def get_user_level(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's level row without creating one, or ``None`` if absent."""
        return await self.fetchrow(
            "SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)

    async def get_user_levels(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every member level row for a guild."""
        return await self.fetch("SELECT * FROM levels WHERE guild_id = $1;", guild_id)

    async def get_leaderboard(self, guild_id: int, *, limit: int = 10) -> list[asyncpg.Record]:
        """Fetches the top members of a guild, highest level (then XP) first."""
        query = """
            SELECT user_id, level, xp, messages
            FROM levels
            WHERE guild_id = $1 AND (messages > 0 OR level > 0 OR xp > 0)
            ORDER BY level DESC, xp DESC, user_id
            LIMIT $2;
        """
        return await self.fetch(query, guild_id, limit)

    async def get_rank(
            self, user_id: int, guild_id: int, *, connection: asyncpg.Connection | None = None
    ) -> int:
        """Returns a member's XP rank within their guild, or ``0`` if they have none."""
        query = """
            SELECT rank
            FROM (SELECT user_id, guild_id, row_number() OVER (ORDER BY level DESC, xp DESC, user_id) AS rank
                  FROM levels
                  WHERE guild_id = $2) AS rank
            WHERE user_id = $1
              AND guild_id = $2
            LIMIT 1;
        """
        record = await (connection or self.db).fetchval(query, user_id, guild_id)
        return int(record) if record is not None else 0

    async def update_user_level(
            self,
            user_id: int,
            guild_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a member's level row and returns the full updated record."""
        return await self.update_returning(
            "levels", ("user_id", "guild_id"), (user_id, guild_id), values, connection=connection,
        )

    async def delete_member(self, user_id: int, guild_id: int) -> None:
        """Deletes a member's level row for a guild."""
        await self.delete_where("levels", ("user_id", "guild_id"), (user_id, guild_id))

    # -- xp_history (daily per-guild XP snapshots) ------------------------

    async def record_xp_snapshot(self, guild_id: int, total_xp: int, gainers: int) -> None:
        """Upserts today's cumulative-XP snapshot for a guild.

        ``total_xp`` is the summed *total* XP across members (resolved with the
        guild's level spec by the caller); ``gainers`` is how many members carry
        any XP. Re-running on the same day overwrites that day's row.
        """
        query = """
            INSERT INTO xp_history (guild_id, day, total_xp, gainers)
            VALUES ($1, (now() at time zone 'utc')::date, $2, $3)
            ON CONFLICT (guild_id, day)
                DO UPDATE SET total_xp = EXCLUDED.total_xp, gainers = EXCLUDED.gainers;
        """
        await self.execute(query, guild_id, total_xp, gainers)

    async def get_xp_history(self, guild_id: int, *, days: int = 30) -> list[asyncpg.Record]:
        """Fetches a guild's daily XP snapshots over the last ``days`` days, oldest first."""
        query = """
            SELECT day, total_xp, gainers
            FROM xp_history
            WHERE guild_id = $1
              AND day >= (now() at time zone 'utc')::date - $2::int
            ORDER BY day;
        """
        return await self.fetch(query, guild_id, days)
