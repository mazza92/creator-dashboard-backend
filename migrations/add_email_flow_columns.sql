-- Migration: Add email flow columns per emailflowbrief.md
-- Purpose: Support max 3 emails/week limit and 7-day delayed quota emails
--
-- New columns:
--   - emails_sent_this_week: Track weekly email count (max 3/week)
--   - quota_email_send_at: Schedule quota email for 7 days after hitting limit
--   - quota_email_sent_month: De-duplicate quota emails (one per month max)
--   - last_pitch_at: Track when user last sent a pitch (for Stage 5 timing)

-- Add last_pitch_at column (for tracking when user last pitched)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'creators' AND column_name = 'last_pitch_at'
    ) THEN
        ALTER TABLE creators ADD COLUMN last_pitch_at TIMESTAMP WITH TIME ZONE;
        RAISE NOTICE 'Added last_pitch_at column to creators table';
    ELSE
        RAISE NOTICE 'last_pitch_at column already exists';
    END IF;
END $$;

-- Add emails_sent_this_week column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'creators' AND column_name = 'emails_sent_this_week'
    ) THEN
        ALTER TABLE creators ADD COLUMN emails_sent_this_week INT DEFAULT 0;
        RAISE NOTICE 'Added emails_sent_this_week column to creators table';
    ELSE
        RAISE NOTICE 'emails_sent_this_week column already exists';
    END IF;
END $$;

-- Add quota_email_send_at column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'creators' AND column_name = 'quota_email_send_at'
    ) THEN
        ALTER TABLE creators ADD COLUMN quota_email_send_at TIMESTAMP WITH TIME ZONE;

        -- Create index for efficient cron query
        CREATE INDEX IF NOT EXISTS idx_creators_quota_email_send_at
        ON creators(quota_email_send_at)
        WHERE quota_email_send_at IS NOT NULL;

        RAISE NOTICE 'Added quota_email_send_at column to creators table';
    ELSE
        RAISE NOTICE 'quota_email_send_at column already exists';
    END IF;
END $$;

-- Add quota_email_sent_month column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'creators' AND column_name = 'quota_email_sent_month'
    ) THEN
        ALTER TABLE creators ADD COLUMN quota_email_sent_month VARCHAR(7);
        RAISE NOTICE 'Added quota_email_sent_month column to creators table';
    ELSE
        RAISE NOTICE 'quota_email_sent_month column already exists';
    END IF;
END $$;

-- Initialize emails_sent_this_week to 0 for all existing creators
UPDATE creators
SET emails_sent_this_week = 0
WHERE emails_sent_this_week IS NULL;

-- Initialize last_pitch_at for existing users who have pitched
-- Use the most recent pitched_at from their pipeline
UPDATE creators c
SET last_pitch_at = (
    SELECT MAX(cp.pitched_at)
    FROM creator_pipeline cp
    WHERE cp.creator_id = c.id AND cp.pitched_at IS NOT NULL
)
WHERE c.last_pitch_at IS NULL
  AND EXISTS (
    SELECT 1 FROM creator_pipeline cp
    WHERE cp.creator_id = c.id AND cp.pitched_at IS NOT NULL
  );

-- For users who hit their limit recently (in last 7 days) and haven't received
-- the quota email yet, schedule them for the upgrade email
-- This ensures the 7-day delay is respected for existing users
UPDATE creators
SET quota_email_send_at = last_pitch_at + INTERVAL '7 days'
WHERE subscription_tier = 'free'
  AND pitches_sent_this_week >= 3
  AND last_pitch_at IS NOT NULL
  AND last_pitch_at > NOW() - INTERVAL '7 days'
  AND quota_email_send_at IS NULL
  AND (quota_email_sent_month IS NULL OR quota_email_sent_month != TO_CHAR(NOW(), 'YYYY-MM'));

-- Verify the migration
SELECT
    COUNT(*) as total_creators,
    COUNT(emails_sent_this_week) as with_weekly_count,
    COUNT(quota_email_send_at) as with_scheduled_quota_email,
    COUNT(quota_email_sent_month) as with_quota_email_sent
FROM creators;
