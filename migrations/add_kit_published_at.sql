-- Migration: Add kit_published_at column to track when kit was last published
-- Run this migration against your database

ALTER TABLE creators
    ADD COLUMN IF NOT EXISTS kit_published_at TIMESTAMPTZ;

-- Backfill: Set kit_published_at to now() for any already-published kits
UPDATE creators
SET kit_published_at = NOW()
WHERE kit_published = true AND kit_published_at IS NULL;

-- Add index for queries filtering by publish date
CREATE INDEX IF NOT EXISTS idx_creators_kit_published_at ON creators(kit_published_at DESC)
WHERE kit_published = true;
