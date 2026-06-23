-- Migration: Add tracking token for email open tracking
-- This token is included in a tracking pixel in pitch emails

-- Add tracking_token column to creator_pipeline
ALTER TABLE creator_pipeline
ADD COLUMN IF NOT EXISTS tracking_token VARCHAR(64);

-- Create index for fast lookups
CREATE INDEX IF NOT EXISTS idx_creator_pipeline_tracking_token
ON creator_pipeline(tracking_token)
WHERE tracking_token IS NOT NULL;

-- Add open_count to track multiple opens
ALTER TABLE creator_pipeline
ADD COLUMN IF NOT EXISTS email_open_count INT DEFAULT 0;
