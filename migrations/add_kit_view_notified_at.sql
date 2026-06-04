-- Migration: Add kit_view_notified_at column to creators table
-- Purpose: Track when creator was last notified about kit views (LinkedIn-style notifications)
-- Date: 2024-01-XX

-- Add the column to track kit view notification timing
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS kit_view_notified_at TIMESTAMP DEFAULT NULL;

-- Add index for efficient querying during cron jobs
CREATE INDEX IF NOT EXISTS idx_creators_kit_view_notified_at
ON creators (kit_view_notified_at)
WHERE kit_published = true;

-- Add comment for documentation
COMMENT ON COLUMN creators.kit_view_notified_at IS 'Timestamp of last kit view notification email sent to creator';
