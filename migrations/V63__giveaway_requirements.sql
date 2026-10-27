-- Giveaway requirements (required role / account age / join age) live in giveaways.metadata
-- ('kwargs'), so they need no columns. This migration only adds the pending tag-transfer
-- bookkeeping: who sent it and when the request lapses (24h, checked on access).
CREATE TABLE IF NOT EXISTS tag_transfers
(
    tag_id     INTEGER   PRIMARY KEY REFERENCES tags (id) ON DELETE CASCADE,
    from_id    BIGINT    NOT NULL,
    expires_at TIMESTAMP NOT NULL
);
