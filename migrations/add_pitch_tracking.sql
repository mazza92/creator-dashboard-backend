-- Migration: Add Weekly Pitch Tracking for Free Tier Quota
-- Date: 2026-04-09
-- Purpose: Track weekly AI pitch usage (3 free per week, unlimited for Pro/Elite)

-- Add new columns for weekly pitch tracking
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS pitches_sent_this_week INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_pitch_reset DATE;

-- Create index for efficient weekly reset queries
CREATE INDEX IF NOT EXISTS idx_creators_last_pitch_reset
ON creators(last_pitch_reset)
WHERE subscription_tier = 'free' OR subscription_tier IS NULL;

-- Add comments for documentation
COMMENT ON COLUMN creators.pitches_sent_this_week IS 'Number of AI pitches sent this week (resets weekly for free tier)';
COMMENT ON COLUMN creators.last_pitch_reset IS 'Start of the week when pitches were last reset (Monday)';

-- Note: The weekly reset logic is handled in application code:
-- 1. When user sends a pitch:
--    - Calculate current week's Monday
--    - If last_pitch_reset IS NULL or < this week's Monday: Reset pitches_sent_this_week to 0
--    - Check: pitches_sent_this_week >= 3 blocks free users
--    - Increment pitches_sent_this_week by 1
--    - Update last_pitch_reset to this week's Monday
-- 2. Free tier limit: 3 pitches per week
-- 3. Pro/Elite: Unlimited (no checks)
