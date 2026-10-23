-- Public API (/api/v1): per-guild bearer tokens and the idempotency ledger for money writes.
-- Only the SHA-256 hex of a token is stored; the plaintext is shown once at creation.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    token_hash   TEXT    NOT NULL UNIQUE,
    scopes       TEXT    NOT NULL,           -- space-separated, e.g. "economy:read items:read"
    created_by   TEXT    NOT NULL,           -- Discord user id of the Manage Server admin
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE INDEX IF NOT EXISTS api_tokens_guild_idx ON api_tokens (guild_id);

-- (token, Idempotency-Key) -> the stored response, for 24 h. status = 0 marks a request still running.
CREATE TABLE IF NOT EXISTS api_idempotency (
    token_id     INTEGER NOT NULL,
    idem_key     TEXT    NOT NULL,
    request_hash TEXT    NOT NULL,
    status       INTEGER NOT NULL DEFAULT 0,
    body         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (token_id, idem_key)
);

CREATE INDEX IF NOT EXISTS api_idempotency_created_idx ON api_idempotency (created_at);
