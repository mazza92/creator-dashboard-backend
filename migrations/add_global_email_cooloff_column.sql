-- Migration: Add global email cooloff tracking column
-- Purpose: Prevent users from receiving multiple emails on the same day
-- The last_any_email_sent column tracks when ANY email was last sent to a creator,
-- regardless of email type. All email cron jobs check this before sending.

-- Add the column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'creators' AND column_name = 'last_any_email_sent'
    ) THEN
        ALTER TABLE creators ADD COLUMN last_any_email_sent TIMESTAMP WITH TIME ZONE;

        -- Create index for efficient querying
        CREATE INDEX IF NOT EXISTS idx_creators_last_any_email_sent
        ON creators(last_any_email_sent);

        RAISE NOTICE 'Added last_any_email_sent column to creators table';
    ELSE
        RAISE NOTICE 'last_any_email_sent column already exists';
    END IF;
END $$;

-- Initialize the column for existing users based on their most recent email timestamp
-- This prevents all existing users from being bombarded with emails after migration
UPDATE creators
SET last_any_email_sent = GREATEST(
    COALESCE(last_reminder_sent, '1970-01-01'),
    COALESCE(last_new_brands_email_sent, '1970-01-01'),
    COALESCE(last_limit_warning_sent, '1970-01-01'),
    COALESCE(last_upgrade_email_sent, '1970-01-01'),
    COALESCE(last_reengagement_sent, '1970-01-01'),
    COALESCE(last_monthly_reset_sent, '1970-01-01')
)
WHERE last_any_email_sent IS NULL
  AND (
    last_reminder_sent IS NOT NULL
    OR last_new_brands_email_sent IS NOT NULL
    OR last_limit_warning_sent IS NOT NULL
    OR last_upgrade_email_sent IS NOT NULL
    OR last_reengagement_sent IS NOT NULL
    OR last_monthly_reset_sent IS NOT NULL
  );

-- Verify the migration
SELECT
    COUNT(*) as total_creators,
    COUNT(last_any_email_sent) as creators_with_global_timestamp,
    MIN(last_any_email_sent) as earliest_email,
    MAX(last_any_email_sent) as latest_email
FROM creators;
