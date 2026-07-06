"""Data access for platform-integration features: outbound webhook subscriptions
(event push) and shareable guild-setup templates.

Both are dashboard/API-facing and hold no cached guild/user config, so no cache
signals are fired here. Methods return raw records/scalars; the API layer shapes them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.database.repositories.base import BaseRepository

if TYPE_CHECKING:
    import asyncpg

__all__ = (
    'EventWebhooksRepository',
    'GuildTemplatesRepository',
)

#: How many delivery-log rows to keep per subscription; older rows are pruned on insert.
_DELIVERY_LOG_KEEP = 50


class EventWebhooksRepository(BaseRepository):
    """Data access for ``webhook_subscriptions`` and ``webhook_deliveries``.

    A subscription is a per-guild (url, secret, events) tuple; the dispatcher cog reads the
    ones :meth:`matching_for_event` returns when an event fires and records each attempt via
    :meth:`record_attempt`.
    """

    # -- subscriptions ----------------------------------------------------

    async def list_for_guild(self, guild_id: int) -> list[asyncpg.Record]:
        """Every subscription for a guild, newest first."""
        return await self.fetch(
            "SELECT * FROM webhook_subscriptions WHERE guild_id = $1 ORDER BY created_at DESC;",
            guild_id,
        )

    async def get(self, sub_id: int, guild_id: int) -> asyncpg.Record | None:
        """A single subscription scoped to its guild (guild-scoping prevents cross-guild access)."""
        return await self.fetchrow(
            "SELECT * FROM webhook_subscriptions WHERE id = $1 AND guild_id = $2;",
            sub_id, guild_id,
        )

    async def create(
        self, guild_id: int, url: str, secret: str, events: list[str], label: str | None,
    ) -> asyncpg.Record:
        """Insert a subscription and return the created row."""
        return await self.fetchrow(
            """
            INSERT INTO webhook_subscriptions (guild_id, url, secret, events, label)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *;
            """,
            guild_id, url, secret, events, label,
        )

    async def update(self, sub_id: int, guild_id: int, values: dict[str, Any]) -> asyncpg.Record | None:
        """Update whitelisted columns of a subscription, returning the refreshed row.

        ``values`` keys must be real column names; callers pass a fixed, validated set.
        """
        return await self.update_returning(
            'webhook_subscriptions', ('id', 'guild_id'), (sub_id, guild_id), values,
        )

    async def delete(self, sub_id: int, guild_id: int) -> str:
        """Delete a subscription (its delivery rows cascade)."""
        return await self.delete_where('webhook_subscriptions', ('id', 'guild_id'), (sub_id, guild_id))

    async def active_guild_ids(self) -> set[int]:
        """The set of guild IDs that have at least one enabled subscription.

        The dispatcher caches this so an event in a guild with no webhooks is dropped without
        a per-event query.
        """
        rows = await self.fetch("SELECT DISTINCT guild_id FROM webhook_subscriptions WHERE enabled = TRUE;")
        return {r["guild_id"] for r in rows}

    async def matching_for_event(self, guild_id: int, event: str) -> list[asyncpg.Record]:
        """Enabled subscriptions in a guild whose event list includes ``event``."""
        return await self.fetch(
            """
            SELECT * FROM webhook_subscriptions
            WHERE guild_id = $1 AND enabled = TRUE AND $2 = ANY (events);
            """,
            guild_id, event,
        )

    # -- deliveries -------------------------------------------------------

    async def record_attempt(
        self,
        sub_id: int,
        event: str,
        *,
        success: bool,
        status_code: int | None,
        attempts: int,
        error: str | None,
    ) -> int:
        """Log one delivery attempt and roll the subscription's health counters.

        Inserts a ``webhook_deliveries`` row, updates the subscription's ``last_delivery_at`` /
        ``last_status`` and its consecutive ``failure_count`` (reset to 0 on success, else +1),
        and prunes the delivery log to the most recent :data:`_DELIVERY_LOG_KEEP` rows — all in
        one transaction. Returns the new consecutive ``failure_count`` so the caller can decide
        whether to auto-disable the subscription.
        """
        async with self.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO webhook_deliveries (subscription_id, event, success, status_code, attempts, error)
                VALUES ($1, $2, $3, $4, $5, $6);
                """,
                sub_id, event, success, status_code, attempts, error,
            )
            row = await conn.fetchrow(
                """
                UPDATE webhook_subscriptions
                SET last_delivery_at = (now() AT TIME ZONE 'utc'),
                    last_status = $2,
                    failure_count = CASE WHEN $3 THEN 0 ELSE failure_count + 1 END
                WHERE id = $1
                RETURNING failure_count;
                """,
                sub_id, status_code, success,
            )
            await conn.execute(
                """
                DELETE FROM webhook_deliveries
                WHERE subscription_id = $1
                  AND id NOT IN (
                      SELECT id FROM webhook_deliveries
                      WHERE subscription_id = $1
                      ORDER BY created_at DESC
                      LIMIT $2
                  );
                """,
                sub_id, _DELIVERY_LOG_KEEP,
            )
        return row['failure_count'] if row else 0

    async def get_deliveries(self, sub_id: int, *, limit: int = 25) -> list[asyncpg.Record]:
        """The most recent delivery attempts for a subscription, newest first."""
        return await self.fetch(
            """
            SELECT event, success, status_code, attempts, error, created_at
            FROM webhook_deliveries
            WHERE subscription_id = $1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            sub_id, limit,
        )


class GuildTemplatesRepository(BaseRepository):
    """Data access for ``guild_templates`` — shareable server-setup snapshots.

    A template stores a backup blob (``data``) plus discovery metadata. ``slug`` is the
    stable public identifier used to apply a template to another guild.
    """

    async def list_public(self, *, limit: int = 50, offset: int = 0) -> list[asyncpg.Record]:
        """Public templates for the browse gallery, most recent first."""
        return await self.fetch(
            """
            SELECT slug, name, description, author_guild_id, downloads, created_at
            FROM guild_templates
            WHERE public = TRUE
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2;
            """,
            limit, offset,
        )

    async def list_for_guild(self, guild_id: int) -> list[asyncpg.Record]:
        """Templates authored by a guild (public or not)."""
        return await self.fetch(
            """
            SELECT slug, name, description, public, downloads, created_at
            FROM guild_templates
            WHERE author_guild_id = $1
            ORDER BY created_at DESC;
            """,
            guild_id,
        )

    async def get(self, slug: str) -> asyncpg.Record | None:
        """Fetch a template (including its ``data`` blob) by slug."""
        return await self.fetchrow("SELECT * FROM guild_templates WHERE slug = $1;", slug)

    async def create(
        self,
        slug: str,
        name: str,
        description: str | None,
        author_guild_id: int,
        author_id: int | None,
        data: dict,
        public: bool,
    ) -> asyncpg.Record | None:
        """Insert a template. Returns ``None`` if the slug is already taken."""
        return await self.fetchrow(
            """
            INSERT INTO guild_templates (slug, name, description, author_guild_id, author_id, data, public)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (slug) DO NOTHING
            RETURNING slug;
            """,
            slug, name, description, author_guild_id, author_id, data, public,
        )

    async def delete(self, slug: str, author_guild_id: int) -> str:
        """Delete a template, but only one the requesting guild authored."""
        return await self.delete_where(
            'guild_templates', ('slug', 'author_guild_id'), (slug, author_guild_id),
        )

    async def increment_downloads(self, slug: str) -> None:
        """Bump the download counter when a template is applied."""
        await self.execute("UPDATE guild_templates SET downloads = downloads + 1 WHERE slug = $1;", slug)
