-- Ensure email_campaigns table has all required columns

-- Add html_content_override column if missing
ALTER TABLE email_campaigns ADD COLUMN IF NOT EXISTS html_content_override TEXT;

-- Add segment_filters column if missing (stores JSON)
ALTER TABLE email_campaigns ADD COLUMN IF NOT EXISTS segment_filters JSONB DEFAULT '{}';

-- Add created_by column if missing
ALTER TABLE email_campaigns ADD COLUMN IF NOT EXISTS created_by VARCHAR(100) DEFAULT 'admin';
