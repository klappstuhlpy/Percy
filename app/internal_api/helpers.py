"""Shared helpers for resolving Discord entities into API-friendly dicts."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    import discord

# Discord caps a timeout at 28 days; anything under a minute is not worth a round trip.
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60
# A tempmute is lifted by a timer, so it has no Discord cap -- but past a year it is a permanent
# mute in all but name, and an unbounded value overflows the expiry datetime.
MAX_MUTE_SECONDS = 365 * 24 * 60 * 60

# The largest value a Postgres BIGINT (every snowflake column) holds.
_MAX_SNOWFLAKE = 2**63 - 1


def validate_timeout_duration(duration: int | None) -> datetime.timedelta:
    """Validates a timeout duration in seconds, returning it as a timedelta."""
    if duration is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='timeout requires a duration')
    if not MIN_TIMEOUT_SECONDS <= duration <= MAX_TIMEOUT_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'duration must be between {MIN_TIMEOUT_SECONDS} seconds and 28 days',
        )
    return datetime.timedelta(seconds=duration)


def validate_mute_duration(duration: int | None) -> None:
    """Refuses a tempmute duration outside one minute to one year (``None`` is a permanent mute)."""
    if duration is not None and not MIN_TIMEOUT_SECONDS <= duration <= MAX_MUTE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'duration must be between {MIN_TIMEOUT_SECONDS} seconds and 365 days',
        )


def parse_id(value: object, name: str) -> int:
    """Parses a client-supplied snowflake, answering a malformed one with a 400 rather than a 500."""
    try:
        parsed = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        parsed = 0
    if isinstance(value, bool) or not 0 < parsed <= _MAX_SNOWFLAKE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{name} must be a Discord id')
    return parsed


def parse_timestamp(value: str | None, name: str) -> datetime.datetime | None:
    """Parses a client ISO timestamp (``...Z`` included) to naive UTC; a malformed one is a 400."""
    if not value:
        return None
    try:
        return naive_utc(datetime.datetime.fromisoformat(value))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f'{name} must be an ISO 8601 timestamp'
        ) from None


def resolve_moderator(guild: discord.Guild, moderator_id: object, permission: str) -> discord.Member:
    """The acting moderator as a member of ``guild``, refused unless they hold ``permission``.

    The bearer token only proves the caller is the dashboard; this is what proves the person
    behind it may do what they asked, with the same permission the matching command requires.
    """
    if moderator_id is None or moderator_id == '':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='moderator_id is required')
    moderator = guild.get_member(parse_id(moderator_id, 'moderator_id'))
    if moderator is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='you are not a member of this server')
    if not getattr(moderator.guild_permissions, permission):
        label = permission.replace('_', ' ').title()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'you need the {label} permission to do that')
    return moderator


def role_error(role: discord.Role, moderator: discord.Member | None) -> str | None:
    """Why ``role`` may not be given or taken through the dashboard, or ``None`` when it may.

    The same rule Discord applies to a person: only roles strictly below your own top role (the
    owner excepted), and never @everyone or a role an integration manages. ``moderator=None``
    (an older dashboard that does not send one yet) checks only Percy's side.
    """
    guild = role.guild
    if role.is_default():
        return '@everyone cannot be given or taken'
    if role.managed:
        return f'{role.name} is managed by an integration'
    if role >= guild.me.top_role:
        return f'{role.name} is equal to or above my highest role'
    if moderator is not None and moderator.id != guild.owner_id and role >= moderator.top_role:
        return f'{role.name} is equal to or above your highest role'
    return None


def naive_utc(value: datetime.datetime | None) -> datetime.datetime:
    """Coerces a client timestamp to naive UTC, defaulting to now.

    Both progress tables (``watch_progress``, ``release_progress``) store a naive ``TIMESTAMP``
    and asyncpg's encoder raises ``TypeError`` on an aware value, while clients send ISO strings
    with a ``Z`` offset -- so the conversion happens once, here, for every handler that takes a
    client-supplied ``updated_at``.
    """
    if value is None:
        return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(datetime.UTC).replace(tzinfo=None)
    return value


def resolve_channel(guild: discord.Guild, channel_id: int | None) -> dict | None:
    if channel_id is None:
        return None
    ch = guild.get_channel(channel_id)
    return {
        'id': str(channel_id),
        'name': ch.name if ch else 'deleted-channel',
        'type': str(ch.type) if ch else 'unknown',
    }


def resolve_role(guild: discord.Guild, role_id: int | None) -> dict | None:
    if role_id is None:
        return None
    role = guild.get_role(role_id)
    return {
        'id': str(role_id),
        'name': role.name if role else 'deleted-role',
        'color': role.color.value if role else 0,
    }


def resolve_entity(guild: discord.Guild, entity_id: int) -> dict:
    role = guild.get_role(entity_id)
    if role is not None:
        return {'id': str(entity_id), 'type': 'role', 'name': role.name}
    channel = guild.get_channel(entity_id)
    if channel is not None:
        return {'id': str(entity_id), 'type': 'channel', 'name': channel.name}
    member = guild.get_member(entity_id)
    if member is not None:
        return {'id': str(entity_id), 'type': 'member', 'name': member.display_name}
    return {'id': str(entity_id), 'type': 'unknown', 'name': str(entity_id)}
