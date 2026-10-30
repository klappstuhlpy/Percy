-- Revises: V67
-- Creation Date: 2026-10-30 00:00:00.000000+00:00 UTC
-- Reason: economy items get a picture shown on their item card

-- https URL of an image shown as the item card's thumbnail; NULL falls back to a custom emoji's image.
ALTER TABLE economy_items ADD COLUMN IF NOT EXISTS image_url TEXT;
