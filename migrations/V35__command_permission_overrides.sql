-- Revises: V34
-- Creation Date: 2026-07-03 00:00:00.000000 UTC
-- Reason: command_permission_overrides

-- Per-guild overrides for who may run a command. Enforced by the runtime permission
-- check (covers both prefix and slash invocations). ``permissions`` is a Discord
-- permission bitmask that replaces the command's default *user* requirement (NULL keeps
-- the default); ``allowed_roles`` is an allow-list of role IDs that may always run it.
CREATE TABLE IF NOT EXISTS command_permission_overrides
(
    guild_id      BIGINT   NOT NULL,
    command       TEXT     NOT NULL,
    permissions   BIGINT,
    allowed_roles BIGINT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (guild_id, command)
);
