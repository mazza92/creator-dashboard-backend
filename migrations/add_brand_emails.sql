-- Roster-waiting brand emails.
-- Logs every automated send so we can measure which trigger / subject
-- actually gets a brand to pick creators (brand activation).

CREATE TABLE IF NOT EXISTS brand_emails (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    campaign_id INTEGER REFERENCES brand_pr_campaigns(id) ON DELETE SET NULL,
    trigger_number INTEGER NOT NULL,
    email_type VARCHAR(64) NOT NULL,
    to_email VARCHAR(255) NOT NULL,
    subject TEXT NOT NULL,
    subject_variant VARCHAR(8) NOT NULL DEFAULT 'A',
    applicant_count INTEGER NOT NULL DEFAULT 0,
    roster_link TEXT,
    message_id VARCHAR(255),
    status VARCHAR(32) NOT NULL DEFAULT 'sent',
    skip_reason TEXT,
    roster_opened_at TIMESTAMPTZ,
    roster_clicked_at TIMESTAMPTZ,
    engaged BOOLEAN NOT NULL DEFAULT FALSE,
    engaged_at TIMESTAMPTZ,
    engaged_reason VARCHAR(64),
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_emails_brand_sent
    ON brand_emails (brand_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_brand_emails_trigger
    ON brand_emails (brand_id, trigger_number, sent_at DESC)
    WHERE status = 'sent';

CREATE INDEX IF NOT EXISTS idx_brand_emails_type
    ON brand_emails (email_type, sent_at DESC);

COMMENT ON TABLE brand_emails IS
    'Automated roster-waiting emails to brands. Key metric: engaged (brand picked creators).';
COMMENT ON COLUMN brand_emails.trigger_number IS
    '1 first nudge (3+), 2 volume (10+), 3 weekly reminder, 4 final archive.';
COMMENT ON COLUMN brand_emails.subject_variant IS
    'A/B subject: A default, B second-batch test.';
COMMENT ON COLUMN brand_emails.engaged IS
    'True after reply, pick, or founder handoff. Stops further automated mail.';
