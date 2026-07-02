from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.database.repositories.base import BaseRepository
from app.utils.timetools import ensure_utc

if TYPE_CHECKING:
    import datetime

    import asyncpg

__all__ = (
    'AniListRepository',
    'PlaylistsRepository',
    'UsersRepository',
    'VotesRepository',
)


# -- Users (settings, balance, personal data) ------------------------------


class UsersRepository(BaseRepository):
    """Data access for the ``user_settings`` and ``economy`` tables.

    The methods return raw records and scalars; mapping them onto the
    :class:`~app.database.base.UserConfig` / :class:`~app.database.base.Balance`
    domain objects (and caching the result) is left to :class:`~app.database.base.Database`.
    """

    # -- user_settings ----------------------------------------------------

    async def get_settings_record(self, user_id: int) -> asyncpg.Record:
        """Fetches the settings row for a user, inserting a default row if absent."""
        record = await self.fetchrow("SELECT * FROM user_settings WHERE id = $1;", user_id)
        if record is None:
            record = await self.fetchrow("INSERT INTO user_settings (id) VALUES ($1) RETURNING *;", user_id)
        return record

    async def get_timezone(self, user_id: int) -> str:
        """Fetches the stored timezone for a user."""
        return await self.fetchval("SELECT timezone FROM user_settings WHERE id = $1;", user_id, column='timezone')

    async def set_timezone(self, user_id: int, timezone: str) -> None:
        """Stores (or replaces) a user's timezone."""
        query = """
            INSERT INTO user_settings (id, timezone)
            VALUES ($1, $2)
                ON CONFLICT (id) DO UPDATE SET timezone = $2;
        """
        await self.execute(query, user_id, timezone)
        self.invalidate_cache("user_config_changed", user_id)

    async def clear_timezone(self, user_id: int) -> None:
        """Clears a user's stored timezone."""
        await self.execute("UPDATE user_settings SET timezone = NULL WHERE id=$1;", user_id)
        self.invalidate_cache("user_config_changed", user_id)

    async def delete_personal_data(self, user_id: int) -> None:
        """Removes a user's tracked history (presence, avatar and item) in one transaction."""
        async with self.acquire(timeout=300.0) as conn, conn.transaction():
            # asyncpg runs parameterised statements through the extended protocol, which
            # rejects multiple commands in one string — so issue each DELETE separately
            # (still atomic within the surrounding transaction).
            await conn.execute("DELETE FROM presence_history WHERE uuid = $1;", user_id)
            await conn.execute("DELETE FROM avatar_history WHERE uuid = $1;", user_id)
            await conn.execute("DELETE FROM item_history WHERE uuid = $1;", user_id)

    async def export_all_user_data(self, user_id: int) -> dict[str, object]:
        """Collects *all* personal data Percy stores about a user, for a data-access request.

        A GDPR-style access/portability export spanning every user-keyed table, mirroring
        the categories in the Privacy Policy: settings and consent-tracked history
        (presence, name/nickname, avatar), leveling/activity counts, game statistics,
        economy, content the user created (tags, notes, reminders, playlists, giveaways,
        poll answers, highlights), linked accounts, vote rewards, and the moderation cases
        that reference them. Sensitive credentials (the AniList access token) and bulky
        blobs (avatar image bytes — only the format and timestamp are exported) are
        deliberately excluded to keep the payload safe and portable.

        Reaches across domains by design: this is the single aggregation point for a user's
        data, so it queries tables owned by other cogs rather than fanning out to their
        repositories.
        """
        uid_text = str(user_id)

        settings = await self.fetchrow("SELECT * FROM user_settings WHERE id = $1;", user_id)

        # Consent-tracked history.
        presence = await self.fetch(
            "SELECT status, status_before, changed_at FROM presence_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )
        names = await self.fetch(
            "SELECT item_type, item_value, changed_at FROM item_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )
        avatars = await self.fetch(
            "SELECT format, changed_at FROM avatar_history WHERE uuid = $1 ORDER BY changed_at;",
            user_id,
        )

        # Leveling / activity counts and game statistics (per guild).
        levels = await self.fetch(
            "SELECT guild_id, level, xp, messages FROM levels WHERE user_id = $1 ORDER BY guild_id;", user_id
        )
        games = await self.fetch(
            "SELECT guild_id, game, played, won, lost, tied, wagered, profit, biggest_win, "
            "current_streak, best_streak, last_played FROM game_stats WHERE user_id = $1 ORDER BY guild_id, game;",
            user_id,
        )

        # Economy.
        balances = await self.fetch(
            "SELECT guild_id, cash, bank FROM economy WHERE user_id = $1 ORDER BY guild_id;", user_id
        )
        inventory = await self.fetch(
            "SELECT inv.guild_id, it.name, inv.quantity FROM economy_inventory inv "
            "JOIN economy_items it ON it.id = inv.item_id WHERE inv.user_id = $1 ORDER BY inv.guild_id;",
            user_id,
        )
        dailies = await self.fetch(
            "SELECT guild_id, last_claim, streak FROM economy_dailies WHERE user_id = $1 ORDER BY guild_id;", user_id
        )
        boosts = await self.fetch(
            "SELECT guild_id, kind, multiplier, expires_at FROM economy_boosts WHERE user_id = $1;", user_id
        )
        lottery = await self.fetch(
            "SELECT guild_id, tickets FROM economy_lottery_entries WHERE user_id = $1;", user_id
        )

        # Content the user created.
        tags = await self.fetch(
            "SELECT name, content, uses, location_id, created_at, use_embed FROM tags "
            "WHERE owner_id = $1 ORDER BY created_at;",
            user_id,
        )
        tag_aliases = await self.fetch(
            "SELECT name, location_id, created_at FROM tag_lookup WHERE owner_id = $1 ORDER BY created_at;", user_id
        )
        notes = await self.fetch(
            "SELECT id, topic, content, created_at FROM user_notes WHERE owner_id = $1 ORDER BY created_at;", user_id
        )
        highlights = await self.fetch(
            "SELECT location_id, lookup, blocked FROM highlights WHERE user_id = $1;", user_id
        )
        reminders = await self.fetch(
            "SELECT id, created, expires, metadata #>> '{args,2}' AS message, "
            "metadata #>> '{kwargs,recur_label}' AS recurrence FROM timers "
            "WHERE event = 'reminder' AND metadata #>> '{args,0}' = $1 ORDER BY created;",
            uid_text,
        )
        giveaways = await self.fetch(
            "SELECT id, guild_id, channel_id, message_id, metadata, "
            "(author_id = $1) AS created_by_you, ($1 = ANY(entries)) AS entered "
            "FROM giveaways WHERE author_id = $1 OR $1 = ANY(entries);",
            user_id,
        )
        poll_answers = await self.fetch(
            "SELECT p.id, p.guild_id, p.message_id, e.vote FROM polls p, unnest(p.entries) e "
            "WHERE e.user_id = $1;",
            user_id,
        )

        playlists_raw = await self.fetch("SELECT id, name, created FROM playlist WHERE user_id = $1 ORDER BY created;", user_id)
        playlists: list[dict[str, object]] = []
        for playlist in playlists_raw:
            tracks = await self.fetch("SELECT name, url FROM playlist_lookup WHERE playlist_id = $1;", playlist['id'])
            playlists.append({**dict(playlist), 'tracks': [dict(track) for track in tracks]})

        # Linked accounts — the access token is intentionally NOT exported.
        anilist = await self.fetchrow("SELECT expires_at FROM anilist_users WHERE user_id = $1;", user_id)

        votes = await self.fetchrow(
            "SELECT multiplier, expires_at, last_source, last_voted_at, total_votes "
            "FROM vote_rewards WHERE user_id = $1;",
            user_id,
        )

        # Moderation cases that reference the user (as the target, or as the acting moderator).
        cases_against = await self.fetch(
            "SELECT guild_id, case_index, action, reason, created_at FROM mod_cases "
            "WHERE target_id = $1 ORDER BY created_at;",
            user_id,
        )
        cases_by = await self.fetch(
            "SELECT guild_id, case_index, action, reason, created_at FROM mod_cases "
            "WHERE moderator_id = $1 ORDER BY created_at;",
            user_id,
        )

        # Command-usage log: bounded to a total plus the 100 most recent to keep the export portable.
        commands_total = await self.fetchval("SELECT COUNT(*) FROM commands WHERE author_id = $1;", user_id)
        recent_commands = await self.fetch(
            "SELECT used, command, guild_id, channel_id, failed, app_command FROM commands "
            "WHERE author_id = $1 ORDER BY used DESC LIMIT 100;",
            user_id,
        )

        return {
            'user_id': user_id,
            'settings': dict(settings) if settings is not None else None,
            'presence_history': [dict(row) for row in presence],
            'name_history': [dict(row) for row in names],
            'avatar_history': [dict(row) for row in avatars],
            'leveling': [dict(row) for row in levels],
            'game_stats': [dict(row) for row in games],
            'economy': {
                'balances': [dict(row) for row in balances],
                'inventory': [dict(row) for row in inventory],
                'dailies': [dict(row) for row in dailies],
                'boosts': [dict(row) for row in boosts],
                'lottery_entries': [dict(row) for row in lottery],
            },
            'tags': [dict(row) for row in tags],
            'tag_aliases': [dict(row) for row in tag_aliases],
            'notes': [dict(row) for row in notes],
            'highlights': [dict(row) for row in highlights],
            'reminders': [dict(row) for row in reminders],
            'giveaways': [dict(row) for row in giveaways],
            'poll_answers': [dict(row) for row in poll_answers],
            'playlists': playlists,
            'linked_accounts': {
                'anilist': {'linked': anilist is not None, 'expires_at': anilist['expires_at'] if anilist else None},
            },
            'vote_rewards': dict(votes) if votes is not None else None,
            'moderation_cases': {
                'against_you': [dict(row) for row in cases_against],
                'issued_by_you': [dict(row) for row in cases_by],
            },
            'command_usage': {
                'total': commands_total,
                'recent': [dict(row) for row in recent_commands],
            },
        }

    # -- economy ----------------------------------------------------------

    async def get_balance_record(self, user_id: int, guild_id: int) -> asyncpg.Record:
        """Fetches a user's balance row for a guild, inserting an empty one if absent."""
        record = await self.fetchrow(
            "SELECT * FROM economy WHERE user_id = $1 AND guild_id = $2;", user_id, guild_id)
        if not record:
            record = await self.fetchrow(
                "INSERT INTO economy (user_id, guild_id, cash, bank) VALUES ($1, $2, 0, 0) RETURNING *;",
                user_id, guild_id)
        return record

    async def get_guild_balance_records(self, guild_id: int) -> list[asyncpg.Record]:
        """Fetches every balance row for a guild."""
        return await self.fetch("SELECT * FROM economy WHERE guild_id = $1;", guild_id)

    async def get_top_balance_records(self, guild_id: int, limit: int) -> list[asyncpg.Record]:
        """Fetches the richest members of a guild (by cash + bank), excluding empty wallets.

        A single ordered query - the leaderboard must not loop per-member balance
        lookups (that was both slow and only sampled an arbitrary subset).
        """
        return await self.fetch(
            "SELECT user_id, cash, bank, (cash + bank) AS total FROM economy "
            "WHERE guild_id = $1 AND (cash + bank) > 0 ORDER BY total DESC LIMIT $2;",
            guild_id, limit)

    async def add_cash(self, user_id: int, guild_id: int, amount: int) -> None:
        """Adds (or, with a negative ``amount``, removes) cash from a user's balance."""
        await self.execute(
            "UPDATE economy SET cash = cash + $1 WHERE user_id = $2 AND guild_id = $3;",
            amount, user_id, guild_id)



# -- Playlists -------------------------------------------------------------


class PlaylistsRepository(BaseRepository):
    """Data access for the ``playlist`` and ``playlist_lookup`` tables.

    A ``playlist`` row owns many ``playlist_lookup`` rows (its tracks). Methods
    return raw records; the music cog wraps them in ``Playlist`` / ``PlaylistTrack``
    records.
    """

    async def create_playlist(self, user_id: int, name: str, created: datetime.datetime) -> int | None:
        """Creates a playlist for a user and returns its new id."""
        return await self.fetchval(
            "INSERT INTO playlist (user_id, name, created) VALUES ($1, $2, $3) RETURNING id;",
            user_id, name, ensure_utc(created))

    async def get_playlist_by_id(self, playlist_id: int) -> asyncpg.Record | None:
        """Fetches a playlist by its id."""
        return await self.fetchrow("SELECT * FROM playlist WHERE id = $1;", playlist_id)

    async def get_playlist_by_name(self, user_id: int, name: str) -> asyncpg.Record | None:
        """Fetches a user's playlist by (case-insensitive) name."""
        return await self.fetchrow(
            "SELECT * FROM playlist WHERE LOWER(name) = $1 AND user_id = $2;", name.lower(), user_id)

    async def get_liked_songs(self, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's static ``Liked Songs`` playlist."""
        return await self.fetchrow(
            "SELECT * FROM playlist WHERE user_id = $1 AND name = 'Liked Songs' LIMIT 1;", user_id)

    async def get_user_playlists(self, user_id: int) -> list[asyncpg.Record]:
        """Fetches every playlist owned by a user."""
        return await self.fetch("SELECT * FROM playlist WHERE user_id = $1;", user_id)

    async def get_playlist_tracks(self, playlist_id: int) -> list[asyncpg.Record]:
        """Fetches every track belonging to a playlist."""
        return await self.fetch("SELECT * FROM playlist_lookup WHERE playlist_id = $1;", playlist_id)

    async def add_track(self, playlist_id: int, name: str, url: str | None) -> asyncpg.Record:
        """Adds a track to a playlist and returns the inserted row."""
        return cast(
            'asyncpg.Record',
            await self.fetchrow(
                "INSERT INTO playlist_lookup (playlist_id, name, url) VALUES ($1, $2, $3) RETURNING *;",
                playlist_id, name, url),
        )

    async def remove_track(self, track_id: int) -> None:
        """Removes a single track by its id."""
        await self.delete_where("playlist_lookup", ("id",), (track_id,))

    async def clear_tracks(self, playlist_id: int) -> None:
        """Removes every track from a playlist."""
        await self.execute("DELETE FROM playlist_lookup WHERE playlist_id = $1;", playlist_id)

    async def delete_playlist(self, playlist_id: int) -> None:
        """Deletes a playlist together with all of its tracks."""
        await self.delete_where("playlist", ("id",), (playlist_id,))
        await self.delete_where("playlist_lookup", ("playlist_id",), (playlist_id,))


# -- AniList ---------------------------------------------------------------


class AniListRepository(BaseRepository):
    """Persistent storage for AniList OAuth tokens."""

    async def get_token(self, user_id: int) -> tuple[str, datetime.datetime] | None:
        row = await self.fetchrow(
            'SELECT access_token, expires_at FROM anilist_users WHERE user_id = $1',
            user_id,
        )
        if row is None:
            return None
        return row['access_token'], row['expires_at']

    async def upsert_token(self, user_id: int, access_token: str, expires_at: datetime.datetime) -> None:
        await self.execute(
            '''INSERT INTO anilist_users (user_id, access_token, expires_at)
               VALUES ($1, $2, $3)
               ON CONFLICT (user_id) DO UPDATE
               SET access_token = EXCLUDED.access_token,
                   expires_at = EXCLUDED.expires_at''',
            user_id, access_token, ensure_utc(expires_at),
        )

    async def delete_token(self, user_id: int) -> bool:
        result = await self.delete_where("anilist_users", ("user_id",), (user_id,))
        return result == 'DELETE 1'


# -- Vote rewards (bot-list upvotes -> renewable global XP boost) ------------


class VotesRepository(BaseRepository):
    """Data access for the ``vote_rewards`` table.

    A vote on a bot list grants the user a single, global, renewable XP boost. The
    boost is applied wherever XP is awarded via :meth:`get_active_multiplier`, which
    returns ``1.0`` when no reward is currently running.
    """

    async def record_vote(
        self, user_id: int, source: str, *, multiplier: float = 1.10, duration_hours: int = 12
    ) -> datetime.datetime:
        """Grant (or renew) a user's vote reward, returning the new expiry (naive UTC).

        Each vote resets ``expires_at`` to ``now + duration_hours`` (renew, not stack)
        and bumps the running vote counter. ``source`` records the originating bot list.
        """
        query = """
            INSERT INTO vote_rewards (user_id, multiplier, expires_at, last_source, total_votes)
            VALUES ($1, $2, (now() at time zone 'utc') + make_interval(hours => $4), $3, 1)
            ON CONFLICT (user_id) DO UPDATE
                SET multiplier = EXCLUDED.multiplier,
                    expires_at = (now() at time zone 'utc') + make_interval(hours => $4),
                    last_source = EXCLUDED.last_source,
                    last_voted_at = now() at time zone 'utc',
                    total_votes = vote_rewards.total_votes + 1
            RETURNING expires_at;
        """
        return await self.fetchval(query, user_id, multiplier, source, duration_hours)

    async def get_active_multiplier(self, user_id: int) -> float:
        """The user's currently-running vote XP multiplier (``1.0`` when none active)."""
        value = await self.fetchval(
            """
            SELECT multiplier FROM vote_rewards
            WHERE user_id = $1 AND expires_at > (now() at time zone 'utc');
            """,
            user_id,
        )
        return value or 1.0

    async def get_status(self, user_id: int) -> asyncpg.Record | None:
        """Fetches a user's vote-reward row (active or expired), or ``None`` if they never voted."""
        return await self.fetchrow("SELECT * FROM vote_rewards WHERE user_id = $1;", user_id)
