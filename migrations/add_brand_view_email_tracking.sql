-- Migration: Add brand view email tracking column
-- Run this to enable rate limiting for brand view notification emails

ALTER TABLE creators
ADD COLUMN IF NOT EXISTS brand_view_email_sent_at TIMESTAMP;

-- Index for efficient lookups
CREATE INDEX IF NOT EXISTS idx_creators_brand_view_email_sent_at
ON creators(brand_view_email_sent_at)
WHERE brand_view_email_sent_at IS NOT NULL;

COMMENT ON COLUMN creators.brand_view_email_sent_at IS 'Timestamp of last brand view notification email sent (rate limit: 1 per hour)';
