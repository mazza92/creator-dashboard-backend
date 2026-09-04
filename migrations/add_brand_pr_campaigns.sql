-- Brand PR roster campaigns (magic-link access for brands to pick / ship / collect content).
-- Safe to run locally. Schema is also auto-created by brand_pr_roster_routes._ensure_schema.

CREATE TABLE IF NOT EXISTS brand_pr_campaigns (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    headline TEXT,
    lede TEXT,
    deal_chips JSONB NOT NULL DEFAULT '[]'::jsonb,
    slot_limit INTEGER NOT NULL DEFAULT 5,
    sku_note TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    selected_application_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    locked_at TIMESTAMPTZ,
    shipped_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT brand_pr_campaigns_slot_limit_check CHECK (slot_limit >= 1 AND slot_limit <= 50),
    CONSTRAINT brand_pr_campaigns_status_check CHECK (status IN ('active', 'locked', 'shipped', 'closed'))
);

CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_brand
    ON brand_pr_campaigns(brand_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_token
    ON brand_pr_campaigns(token);

ALTER TABLE brand_pr_applications
    ADD COLUMN IF NOT EXISTS campaign_id INTEGER REFERENCES brand_pr_campaigns(id) ON DELETE SET NULL;

ALTER TABLE brand_pr_applications
    ADD COLUMN IF NOT EXISTS declined_at TIMESTAMPTZ;

ALTER TABLE brand_pr_applications
    ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_brand_pr_applications_campaign
    ON brand_pr_applications(campaign_id)
    WHERE campaign_id IS NOT NULL;

-- Allow brand-side roster events without a creator
ALTER TABLE brand_pr_events
    ALTER COLUMN creator_id DROP NOT NULL;

COMMENT ON TABLE brand_pr_campaigns IS
    'Magic-link PR roster for a brand: pick N creators, ship product, collect content. Status: active|locked|shipped|closed.';
COMMENT ON COLUMN brand_pr_campaigns.selected_application_ids IS
    'Application IDs approved into the tray before lock. After lock, those rows are status=ships.';
COMMENT ON COLUMN brand_pr_applications.campaign_id IS
    'Optional link to a roster campaign. NULL applications for the brand still appear in active campaigns.';
