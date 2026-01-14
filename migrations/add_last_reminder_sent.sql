-- Add last_reminder_sent column to track onboarding reminder emails
-- This prevents sending too many reminder emails to the same user

ALTER TABLE creators
ADD COLUMN IF NOT EXISTS last_reminder_sent TIMESTAMP DEFAULT NULL;

-- Create index for efficient querying
CREATE INDEX IF NOT EXISTS idx_creators_last_reminder_sent ON creators(last_reminder_sent);

-- Comment for documentation
COMMENT ON COLUMN creators.last_reminder_sent IS 'Timestamp of last onboarding reminder email sent to this creator';
