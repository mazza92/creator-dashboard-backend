-- Opens/clicks for Polly's own emails, written by the Resend webhook (match on message_id).
ALTER TABLE polly_email_log ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ;
ALTER TABLE polly_email_log ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMPTZ;
ALTER TABLE polly_email_log ADD COLUMN IF NOT EXISTS clicked_url TEXT;

CREATE INDEX IF NOT EXISTS idx_polly_email_log_message_id
    ON polly_email_log (message_id);
