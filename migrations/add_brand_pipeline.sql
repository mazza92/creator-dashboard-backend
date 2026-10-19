-- Brand acquisition pipeline: stage per brand + full activity log.
-- Idempotent. Legacy rows are tagged pipeline='legacy' so the new sequence never re-mails them.

ALTER TABLE brand_outreach_tracking
    ADD COLUMN IF NOT EXISTS pipeline VARCHAR(20),
    ADD COLUMN IF NOT EXISTS stage VARCHAR(30),
    ADD COLUMN IF NOT EXISTS sequence_step INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS variant VARCHAR(30),
    ADD COLUMN IF NOT EXISTS next_action VARCHAR(30),
    ADD COLUMN IF NOT EXISTS next_action_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS contact_email VARCHAR(255),
    ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS contact_role VARCHAR(255),
    ADD COLUMN IF NOT EXISTS contact_source VARCHAR(30),
    ADD COLUMN IF NOT EXISTS ref_code VARCHAR(24),
    ADD COLUMN IF NOT EXISTS thread_subject TEXT,
    ADD COLUMN IF NOT EXISTS thread_message_id TEXT,
    ADD COLUMN IF NOT EXISTS last_inbound_message_id TEXT,
    ADD COLUMN IF NOT EXISTS last_inbound_subject TEXT,
    ADD COLUMN IF NOT EXISTS reply_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS auto_reply_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS intent VARCHAR(30),
    ADD COLUMN IF NOT EXISTS lost_reason TEXT,
    ADD COLUMN IF NOT EXISTS won_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS opportunity_id INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_ref_code
    ON brand_outreach_tracking(ref_code) WHERE ref_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bot_stage_next
    ON brand_outreach_tracking(stage, next_action_at);
CREATE INDEX IF NOT EXISTS idx_bot_contact_email
    ON brand_outreach_tracking(LOWER(contact_email));

CREATE TABLE IF NOT EXISTS brand_activity (
    id BIGSERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    kind VARCHAR(30) NOT NULL,
    actor VARCHAR(20) NOT NULL DEFAULT 'agent',
    stage_from VARCHAR(30),
    stage_to VARCHAR(30),
    intent VARCHAR(30),
    variant VARCHAR(30),
    email VARCHAR(255),
    subject TEXT,
    body TEXT,
    message_id TEXT,
    in_reply_to TEXT,
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_activity_brand ON brand_activity(brand_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_brand_activity_kind ON brand_activity(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_brand_activity_message_id ON brand_activity(message_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_brand_activity_inbound_mid
    ON brand_activity(message_id) WHERE kind = 'reply_in' AND message_id IS NOT NULL;

UPDATE brand_outreach_tracking SET
    pipeline = 'legacy',
    stage = CASE
        WHEN last_response_status IN ('wrong_email', 'bounced') THEN 'bad_contact'
        WHEN last_response_status IN ('do_not_contact', 'unsubscribe') THEN 'do_not_contact'
        WHEN last_response_status = 'not_interested' THEN 'lost'
        WHEN last_response_status = 'signed_up' THEN 'won'
        WHEN last_response_status IN ('reply', 'replied', 'interested') THEN 'replied'
        WHEN last_contacted_at IS NOT NULL THEN 'contacted'
        ELSE 'new'
    END
WHERE stage IS NULL;
