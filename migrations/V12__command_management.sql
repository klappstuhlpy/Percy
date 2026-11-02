-- Command management: permission overrides, rules, custom commands, aliases

-- == V35__command_permission_overrides ==
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

-- == V72__command_rules ==
-- Per-command allow/deny rules. ``subject`` is a command qualified_name, ``cog:<CogName>`` or ``*``;
-- ``target_id`` is the user/role/channel/category id (the guild id for ``guild``).
CREATE TABLE IF NOT EXISTS command_rules
(
    guild_id    BIGINT  NOT NULL,
    subject     TEXT    NOT NULL,
    target_type TEXT    NOT NULL CHECK (target_type IN ('user', 'role', 'channel', 'category', 'guild')),
    target_id   BIGINT  NOT NULL,
    allow       BOOLEAN NOT NULL,
    PRIMARY KEY (guild_id, subject, target_type, target_id)
);

-- == V73__custom_commands ==
-- Guild-defined custom commands and command aliases (app/cogs/custom_commands.py).
-- ``signature`` uses the syntax parsed by app/services/custom_commands/signature.py;
-- ``required_permissions`` is a discord.Permissions bitmask.
CREATE TABLE IF NOT EXISTS custom_commands
(
    id                   SERIAL PRIMARY KEY,
    guild_id             BIGINT    NOT NULL,
    name                 TEXT      NOT NULL,
    aliases              TEXT[]    NOT NULL DEFAULT '{}',
    signature            TEXT      NOT NULL DEFAULT '',
    responses            TEXT[]    NOT NULL,
    required_permissions BIGINT    NULL,
    allowed_role_ids     BIGINT[]  NOT NULL DEFAULT '{}',
    cooldown_rate        INT       NULL,
    cooldown_per         REAL      NULL,
    delete_invocation    BOOLEAN   NOT NULL DEFAULT FALSE,
    owner_id             BIGINT,
    uses                 INT       NOT NULL DEFAULT 0,
    created_at           TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
    UNIQUE (guild_id, name)
);

CREATE TABLE IF NOT EXISTS command_aliases
(
    guild_id  BIGINT NOT NULL,
    alias     TEXT   NOT NULL,
    expansion TEXT   NOT NULL,
    owner_id  BIGINT,
    PRIMARY KEY (guild_id, alias)
);
