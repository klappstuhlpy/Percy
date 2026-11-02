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
