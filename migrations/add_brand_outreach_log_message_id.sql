-- Add message_id anchor for precise bounce reconciliation
ALTER TABLE brand_outreach_log
ADD COLUMN IF NOT EXISTS message_id TEXT;

CREATE INDEX IF NOT EXISTS idx_brand_outreach_log_message_id
ON brand_outreach_log(message_id);
