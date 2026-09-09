-- UGC supply (creator prospects). NOT pr_brands.
-- Qualified shape: UGC + niche + public email + 1k followers (location optional).

CREATE TABLE IF NOT EXISTS ugc_supply (
    id SERIAL PRIMARY KEY,
    handle VARCHAR(64) NOT NULL UNIQUE,
    display_name TEXT,
    bio TEXT,
    contact_email TEXT,
    niche VARCHAR(64),
    location TEXT,
    followers INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    bio_link TEXT,
    avatar_url TEXT,
    profile_url TEXT,
    source VARCHAR(64) NOT NULL DEFAULT 'tiktok_ugc_search',
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    qualified BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ugc_supply_status_check CHECK (
        status IN ('draft', 'review', 'contacted', 'skipped')
    )
);

CREATE INDEX IF NOT EXISTS idx_ugc_supply_qualified
    ON ugc_supply (qualified, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ugc_supply_email
    ON ugc_supply (contact_email)
    WHERE contact_email IS NOT NULL;

CREATE TABLE IF NOT EXISTS ugc_supply_outreach_tracking (
    lead_id INTEGER PRIMARY KEY REFERENCES ugc_supply(id) ON DELETE CASCADE,
    outreach_count INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TIMESTAMPTZ,
    last_subject TEXT,
    last_response_status VARCHAR(64),
    response_notes TEXT,
    last_response_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ugc_supply_outreach_log (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES ugc_supply(id) ON DELETE CASCADE,
    email_sent_to TEXT,
    subject TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'sent',
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_ugc_supply_outreach_log_lead
    ON ugc_supply_outreach_log (lead_id, sent_at DESC);

COMMENT ON TABLE ugc_supply IS
    'Scraped UGC creator supply (UGC + niche + email + 1k followers). Keep separate from pr_brands.';
