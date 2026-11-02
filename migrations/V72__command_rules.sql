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
