"""Pure helpers for the outbound-webhook (event subscription) system.

This module is Discord-free and unit-testable: it builds the JSON envelope sent to
subscribers, signs it, and validates event names against the catalogue. The actual
event listening and HTTP delivery live in the ``Webhooks`` cog (``app/cogs/webhooks.py``);
turning Discord objects into the ``data`` payload also happens there (it needs discord),
so everything here operates on plain dicts and bytes only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

__all__ = (
    'SIGNATURE_HEADER',
    'WEBHOOK_EVENTS',
    'build_envelope',
    'serialize_envelope',
    'sign_body',
    'valid_events',
)

#: HTTP header carrying the HMAC-SHA256 signature of the request body.
SIGNATURE_HEADER = 'X-Percy-Signature'

#: The catalogue of events a guild may subscribe to. Keep this in sync with what the
#: dispatcher cog actually emits — an event not listed here can never be delivered.
WEBHOOK_EVENTS: frozenset[str] = frozenset({
    'member.join',
    'member.remove',
    'member.ban',
    'member.unban',
    'role.create',
    'role.delete',
    'level.up',
    'moderation.case',
    'giveaway.ended',
})


def valid_events(events: list[str] | tuple[str, ...]) -> list[str]:
    """Filter an arbitrary list down to known events, de-duplicated and order-preserving.

    Unknown event names are dropped rather than raising, so a client that asks for a
    mix of valid and invalid events still gets a working subscription for the valid ones.
    """
    seen: set[str] = set()
    result: list[str] = []
    for event in events:
        if event in WEBHOOK_EVENTS and event not in seen:
            seen.add(event)
            result.append(event)
    return result


def build_envelope(event: str, guild_id: int, data: dict) -> dict:
    """Wrap an event payload in the standard delivery envelope.

    Every delivery carries a unique ``id`` (so receivers can de-duplicate retries), the
    ``event`` name, the originating ``guild_id`` (as a string, to survive JS number limits),
    an ISO-8601 UTC ``sent_at`` timestamp, and the event-specific ``data`` object.
    """
    return {
        'id': str(uuid.uuid4()),
        'event': event,
        'guild_id': str(guild_id),
        'sent_at': datetime.now(UTC).isoformat(),
        'data': data,
    }


def serialize_envelope(envelope: dict) -> bytes:
    """Serialize an envelope to the exact bytes that get signed and sent.

    Uses compact separators and ``default=str`` so any stray non-JSON-native value
    (datetime, Decimal, ...) degrades to its string form instead of raising. The signature
    is computed over *these* bytes, so the caller must send them verbatim.
    """
    return json.dumps(envelope, separators=(',', ':'), default=str).encode('utf-8')


def sign_body(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` HMAC signature for a request body (GitHub-style).

    Receivers recompute ``HMAC-SHA256(secret, raw_body)`` and compare in constant time to
    authenticate that the delivery genuinely came from Percy and was not tampered with.
    """
    digest = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'
