-- Recipient-level source of truth for campaign delivery state
CREATE TABLE IF NOT EXISTS email_campaign_recipients (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    user_id INTEGER,
    creator_id INTEGER,
    email TEXT NOT NULL,
    first_name TEXT,
    username TEXT,
    niche TEXT,
    followers_count INTEGER NOT NULL DEFAULT 0,
    tier VARCHAR(50) NOT NULL DEFAULT 'free',
    pitches_this_week INTEGER NOT NULL DEFAULT 0,
    pitches_total INTEGER NOT NULL DEFAULT 0,
    brands_saved INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP,
    last_error TEXT,
    provider_message_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_email_campaign_recipient UNIQUE (campaign_id, email),
    CONSTRAINT chk_email_campaign_recipient_status CHECK (
        status IN ('pending', 'sending', 'sent', 'failed_temp', 'failed_perm', 'skipped')
    )
);

CREATE INDEX IF NOT EXISTS idx_email_campaign_recipients_campaign
    ON email_campaign_recipients(campaign_id);

CREATE INDEX IF NOT EXISTS idx_email_campaign_recipients_campaign_status
    ON email_campaign_recipients(campaign_id, status);

CREATE INDEX IF NOT EXISTS idx_email_campaign_recipients_email
    ON email_campaign_recipients(email);

-- Backfill one row per (campaign, email) from historical email_logs.
-- Status precedence: sent > failed_temp > pending.
INSERT INTO email_campaign_recipients (
    campaign_id,
    user_id,
    creator_id,
    email,
    first_name,
    username,
    niche,
    followers_count,
    tier,
    pitches_this_week,
    pitches_total,
    brands_saved,
    status,
    attempt_count,
    last_attempt_at,
    last_error
)
SELECT
    el.campaign_id,
    MAX(el.user_id) AS user_id,
    MAX(el.creator_id) AS creator_id,
    LOWER(el.email) AS email,
    MAX(u.first_name) AS first_name,
    MAX(c.username) AS username,
    MAX(c.niche) AS niche,
    COALESCE(MAX(c.followers_count), 0) AS followers_count,
    COALESCE(MAX(c.subscription_tier), 'free') AS tier,
    COALESCE(MAX(c.pitches_sent_this_week), 0) AS pitches_this_week,
    COALESCE(MAX(c.pitches_sent_total), 0) AS pitches_total,
    COALESCE(MAX(c.brands_saved_count), 0) AS brands_saved,
    CASE
        WHEN BOOL_OR(el.status = 'sent') THEN 'sent'
        WHEN BOOL_OR(el.status = 'failed') THEN 'failed_temp'
        ELSE 'pending'
    END AS status,
    COUNT(*) AS attempt_count,
    MAX(el.sent_at) AS last_attempt_at,
    MAX(el.error_message) FILTER (WHERE el.error_message IS NOT NULL) AS last_error
FROM email_logs el
LEFT JOIN users u ON u.id = el.user_id
LEFT JOIN creators c ON c.id = el.creator_id
WHERE el.email IS NOT NULL
GROUP BY el.campaign_id, LOWER(el.email)
ON CONFLICT (campaign_id, email) DO NOTHING;

-- Recompute campaign counters from recipient source-of-truth.
UPDATE email_campaigns ec
SET total_recipients = COALESCE(rc.total_recipients, 0),
    total_sent = COALESCE(rc.total_sent, 0)
FROM (
    SELECT
        campaign_id,
        COUNT(*) AS total_recipients,
        COUNT(*) FILTER (WHERE status = 'sent') AS total_sent
    FROM email_campaign_recipients
    GROUP BY campaign_id
) rc
WHERE ec.id = rc.campaign_id;
