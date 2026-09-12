-- Manual "push to For You / active PR campaigns" flag for cold-emailed rosters.
-- Schema is also auto-created by services.roster_demand.ensure_campaign_spotlight_column.

ALTER TABLE brand_pr_campaigns
    ADD COLUMN IF NOT EXISTS creator_spotlighted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_spotlight
    ON brand_pr_campaigns (creator_spotlighted_at DESC)
    WHERE creator_spotlighted_at IS NOT NULL AND status = 'active';
