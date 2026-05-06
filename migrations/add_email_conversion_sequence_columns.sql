-- Migration: Add columns for email conversion sequence tracking
-- Run once on deploy

ALTER TABLE creators
  ADD COLUMN IF NOT EXISTS first_pitch_sent_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_limit_warning_sent TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_upgrade_email_sent TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_reengagement_sent TIMESTAMP,
  ADD COLUMN IF NOT EXISTS last_monthly_reset_sent TIMESTAMP;

-- Add indexes for efficient cron queries
CREATE INDEX IF NOT EXISTS idx_creators_first_pitch_sent_at ON creators(first_pitch_sent_at);
CREATE INDEX IF NOT EXISTS idx_creators_subscription_tier ON creators(subscription_tier);

-- Comment on columns
COMMENT ON COLUMN creators.first_pitch_sent_at IS 'Timestamp when creator sent their first pitch';
COMMENT ON COLUMN creators.last_limit_warning_sent IS 'Last time we sent the "1 contact left" warning email';
COMMENT ON COLUMN creators.last_upgrade_email_sent IS 'Last time we sent the "limit reached" upgrade email';
COMMENT ON COLUMN creators.last_reengagement_sent IS 'Last time we sent re-engagement email to dormant user';
COMMENT ON COLUMN creators.last_monthly_reset_sent IS 'Last time we notified about monthly contact reset';
