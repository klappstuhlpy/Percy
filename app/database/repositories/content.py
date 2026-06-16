from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, cast

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = (
    'AutoRespondersRepository',
    'ComicsRepository',
    'RoleMenusRepository',
    'StatCountersRepository',
    'TempChannelsRepository',
)


# -- Autoresponders --------------------------------------------------------


class AutoRespondersRepository(BaseRepository):
    """Data access for the ``autoresponders`` table.

    Autoresponders fire a canned response when a message matches a trigger phrase
    (unlike tags, which are explicitly invoked). Methods return raw records/scalars;
    matching logic lives in the cog's engine so it stays Discord-free and testable.
    """

    async def get_all(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every autoresponder for a guild, oldest first."""
        return await self.fetch(
            'SELECT * FROM autoresponders WHERE guild_id = $1 ORDER BY id;', guild_id)

    async def get_enabled(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches only the enabled autoresponders for a guild (the on-message hot path)."""
        return await self.fetch(
            'SELECT * FROM autoresponders WHERE guild_id = $1 AND enabled ORDER BY id;', guild_id)

    async def get(self, guild_id: int, trigger: str) -> asyncpg.Record | None:
        """Fetches a single autoresponder by case-insensitive trigger."""
        return await self.fetchrow(
            'SELECT * FROM autoresponders WHERE guild_id = $1 AND lower(trigger) = lower($2);',
            guild_id, trigger,
        )

    async def create(
        self,
        guild_id: int,
        trigger: str,
        response: str,
        *,
        match_type: str,
        ignore_case: bool,
        created_by: int,
    ) -> asyncpg.Record | None:
        """Inserts an autoresponder, returning the row (or ``None`` if the trigger exists)."""
        query = """
            INSERT INTO autoresponders (guild_id, trigger, response, match_type, ignore_case, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger, response, match_type, ignore_case, created_by)

    async def delete(self, guild_id: int, trigger: str) -> asyncpg.Record | None:
        """Deletes an autoresponder by trigger, returning the deleted row (or ``None``)."""
        query = """
            DELETE FROM autoresponders
            WHERE guild_id = $1 AND lower(trigger) = lower($2)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger)

    async def set_enabled(self, guild_id: int, trigger: str, enabled: bool) -> asyncpg.Record | None:
        """Toggles an autoresponder on or off, returning the updated row (or ``None``)."""
        query = """
            UPDATE autoresponders
            SET enabled = $3
            WHERE guild_id = $1 AND lower(trigger) = lower($2)
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, trigger, enabled)

    async def increment_uses(self, responder_id: int) -> None:
        """Bumps the usage counter for a fired autoresponder."""
        await self.execute('UPDATE autoresponders SET uses = uses + 1 WHERE id = $1;', responder_id)


# -- Comics ----------------------------------------------------------------


class ComicsRepository(BaseRepository):
    """Data access for the ``comic_config`` table.

    Each row is a per-guild, per-brand comic feed subscription. Methods return raw
    records; the comic cog wraps them in ``ComicFeed`` records.
    """

    async def get_config(self, guild_id: int, brand: str) -> asyncpg.Record | None:
        """Fetches a guild's feed configuration for a single brand."""
        return await self.fetchrow(
            "SELECT * FROM comic_config WHERE guild_id = $1 AND brand = $2;", guild_id, brand)

    async def get_next_scheduled(
            self, days: int = 7, *, connection: asyncpg.Connection | None = None
    ) -> asyncpg.Record | None:
        """Fetches the earliest feed due within ``days``, or ``None`` if none is ready."""
        query = """
            SELECT *
            FROM comic_config
            WHERE (next_pull AT TIME ZONE 'UTC') < (CURRENT_TIMESTAMP + $1::interval)
            ORDER BY next_pull
            LIMIT 1;
        """
        return await (connection or self.db).fetchrow(query, datetime.timedelta(days=days))

    async def create_config(self, config: dict[str, Any]) -> None:
        """Inserts a new comic feed configuration.

        ``config`` must provide the columns in order: ``guild_id``, ``channel_id``,
        ``brand``, ``format``, ``day``, ``ping``, ``pin``, ``next_pull``.
        """
        query = """
            INSERT INTO comic_config (guild_id, channel_id, brand, format, day, ping, pin, next_pull)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
        """
        await self.execute(query, *config.values())

    async def update_config(
            self,
            config_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a comic_config row and returns the full updated record."""
        return cast(
            'asyncpg.Record',
            await self.update_returning("comic_config", ("id",), (config_id,), values, connection=connection),
        )

    async def set_next_pull(self, next_pull: datetime.datetime, guild_id: int, brand: str) -> None:
        """Updates the scheduled next-pull time for a guild's brand feed."""
        await self.execute(
            "UPDATE comic_config SET next_pull = $1 WHERE guild_id = $2 AND brand = $3;",
            next_pull, guild_id, brand)

    async def delete_config(self, guild_id: int, brand: str) -> None:
        """Removes a guild's feed configuration for a single brand."""
        await self.delete_where("comic_config", ("guild_id", "brand"), (guild_id, brand))


# -- Temp Channels ---------------------------------------------------------


class TempChannelsRepository(BaseRepository):
    """Data access for the ``temp_channels`` table.

    Each row marks a voice channel as a hub that spawns temporary voice channels,
    together with the naming ``format`` to apply. Methods return raw records; the
    ``TempChannels`` cog wraps them in ``TempChannel`` records.
    """

    async def update_channel(
            self,
            guild_id: int,
            channel_id: int,
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> asyncpg.Record:
        """Updates a temp_channels row and returns the full updated record."""
        return cast(
            'asyncpg.Record',
            await self.update_returning(
                "temp_channels", ("guild_id", "channel_id"), (guild_id, channel_id),
                values, connection=connection,
            ),
        )

    async def get_guild_channels(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every temp-channel hub configured in a guild."""
        return await self.fetch("SELECT * FROM temp_channels WHERE guild_id = $1;", guild_id)

    async def get_channel(self, guild_id: int, channel_id: int) -> asyncpg.Record | None:
        """Fetches a single temp-channel hub by guild and channel."""
        return await self.fetchrow(
            "SELECT * FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;", guild_id, channel_id)

    async def create_channel(self, guild_id: int, channel_id: int, fmt: str) -> None:
        """Registers a voice channel as a temp-channel hub with the given name format."""
        await self.execute(
            "INSERT INTO temp_channels (guild_id, channel_id, format) VALUES ($1, $2, $3);",
            guild_id, channel_id, fmt)

    async def delete_channel(self, guild_id: int, channel_id: int) -> None:
        """Removes a single temp-channel hub."""
        await self.delete_where("temp_channels", ("guild_id", "channel_id"), (guild_id, channel_id))

    async def delete_guild_channels(self, guild_id: int) -> None:
        """Removes every temp-channel hub in a guild."""
        await self.execute("DELETE FROM temp_channels WHERE guild_id = $1;", guild_id)


# -- Stat Counters ---------------------------------------------------------


class StatCountersRepository(BaseRepository):
    """Data access for the ``guild_stat_counters`` table.

    A stat counter binds a voice channel to a live server statistic (member count,
    boosts, ...); a periodic loop in the cog renames the channel from its ``template``.
    Methods return raw records/scalars.
    """

    async def get_all(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every stat counter configured for a guild."""
        return await self.fetch(
            'SELECT * FROM guild_stat_counters WHERE guild_id = $1 ORDER BY id;', guild_id)

    async def get_every(self) -> list[asyncpg.Record]:
        """Fetches every stat counter across all guilds (for the refresh loop)."""
        return await self.fetch('SELECT * FROM guild_stat_counters ORDER BY guild_id;')

    async def get_by_channel(self, channel_id: int) -> asyncpg.Record | None:
        """Fetches the counter bound to a channel, or ``None``."""
        return await self.fetchrow(
            'SELECT * FROM guild_stat_counters WHERE channel_id = $1;', channel_id)

    async def create(
        self, guild_id: int, channel_id: int, kind: str, template: str
    ) -> asyncpg.Record | None:
        """Binds a channel to a statistic, returning the row (or ``None`` if already bound)."""
        query = """
            INSERT INTO guild_stat_counters (guild_id, channel_id, kind, template)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (channel_id) DO NOTHING
            RETURNING *;
        """
        return await self.fetchrow(query, guild_id, channel_id, kind, template)

    async def delete_by_channel(self, channel_id: int) -> asyncpg.Record | None:
        """Removes the counter bound to a channel, returning the deleted row (or ``None``)."""
        return await self.fetchrow(
            'DELETE FROM guild_stat_counters WHERE channel_id = $1 RETURNING *;', channel_id)


# -- Role Menus ------------------------------------------------------------


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
