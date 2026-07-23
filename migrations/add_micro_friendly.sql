-- Add micro_friendly flag to pr_brands so admins can curate which brands
-- genuinely work with micro-creators (replaces the frontend min_followers heuristic).
-- Run in Supabase SQL Editor.

-- Step 1: Add column
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS micro_friendly BOOLEAN DEFAULT FALSE;

-- Step 2: Backfill using the old frontend heuristic (min_followers empty/0/<=10k)
-- so the badge doesn't disappear overnight. Admins should review and untick
-- brands that aren't actually micro-friendly.
UPDATE pr_brands
SET micro_friendly = TRUE
WHERE micro_friendly IS NOT TRUE
  AND (min_followers IS NULL OR min_followers = 0 OR min_followers <= 10000);

-- Step 3: Partial index for the Discover filter
CREATE INDEX IF NOT EXISTS idx_pr_brands_micro_friendly
  ON pr_brands(micro_friendly) WHERE micro_friendly = TRUE;
