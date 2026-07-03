from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository
from app.utils.timetools import ensure_utc

if TYPE_CHECKING:
    import datetime
    from collections.abc import Sequence

    import asyncpg

__all__ = (
    'EconomyRepository',
    'LevelingRepository',
)


# -- Economy (shop, inventory, boosts, dailies, lottery) --------------------


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
                   e.effect, e.effect_value, e.duration_minutes
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

    async def add_lottery_tickets(self, guild_id: int, user_id: int, tickets: int, cost: int) -> int:
        """Adds tickets for a member and grows the jackpot; returns the member's new ticket total."""
        async with self.acquire() as conn, conn.transaction():
            await conn.execute(
                'UPDATE economy_lottery SET jackpot = jackpot + $2 WHERE guild_id = $1;', guild_id, cost)
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
            record = await self.fetchrow(
                "INSERT INTO levels (user_id, guild_id) VALUES ($1, $2) RETURNING *;", user_id, guild_id)
        return record

    async def get_user_level(self, user_id: int, guild_id: int) -> asyncpg.Record | None:
        """Fetches a member's level row without creating one, or ``None`` if absent."""
        return await self.fetchrow(
            "SELECT * FROM levels WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)

    async def get_user_levels(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every member level row for a guild."""
        return await self.fetch("SELECT * FROM levels WHERE guild_id = $1;", guild_id)

    async def get_leaderboard(self, guild_id: int, *, limit: int = 10) -> list[asyncpg.Record]:
        """Fetches the top members of a guild ordered by message count."""
        query = """
            SELECT user_id, level, xp, messages
            FROM levels
            WHERE guild_id = $1 AND messages > 0
            ORDER BY messages DESC
            LIMIT $2;
        """
        return await self.fetch(query, guild_id, limit)

    async def get_rank(
            self, user_id: int, guild_id: int, *, connection: asyncpg.Connection | None = None
    ) -> int:
        """Returns a member's XP rank within their guild, or ``0`` if they have none."""
        query = """
            SELECT rank
            FROM (SELECT user_id, guild_id, row_number() OVER (ORDER BY xp DESC) AS rank
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
