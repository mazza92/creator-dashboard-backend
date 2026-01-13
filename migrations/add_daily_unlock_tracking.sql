-- Migration: Add Daily Unlock Tracking for Free Tier Quota
-- Date: 2026-01-13
-- Purpose: Implement daily 5-unlock limit for free users instead of total limit

-- Add new columns for daily unlock tracking
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS daily_unlocks_used INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_unlock_date DATE;

-- Create index for efficient daily reset queries
CREATE INDEX IF NOT EXISTS idx_creators_last_unlock_date
ON creators(last_unlock_date)
WHERE subscription_tier = 'free' OR subscription_tier IS NULL;

-- Add comments for documentation
COMMENT ON COLUMN creators.daily_unlocks_used IS 'Number of brand unlocks/saves used today (resets daily for free tier)';
COMMENT ON COLUMN creators.last_unlock_date IS 'Date of last unlock action (used to reset daily_unlocks_used)';

-- Note: The daily reset logic is handled in application code:
-- 1. When user clicks "save/unlock":
--    - Check if last_unlock_date IS NULL or != today
--    - If different day: Reset daily_unlocks_used to 0
--    - Increment daily_unlocks_used by 1
--    - Update last_unlock_date to today
-- 2. Free tier limit: daily_unlocks_used >= 5 blocks action
-- 3. Pro/Elite: Unlimited (no checks)
