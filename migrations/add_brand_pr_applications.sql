-- Creator Apply for Brand PR (roster apply, no cold email).
-- Safe to run locally. Schema is also auto-created on first apply request.

CREATE TABLE IF NOT EXISTS brand_pr_applications (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'review',
    selected_posts JSONB NOT NULL DEFAULT '[]'::jsonb,
    shipping_address JSONB,
    agreed_at TIMESTAMPTZ,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (creator_id, brand_id)
);

CREATE INDEX IF NOT EXISTS idx_brand_pr_applications_creator
    ON brand_pr_applications(creator_id, applied_at DESC);

CREATE INDEX IF NOT EXISTS idx_brand_pr_applications_brand
    ON brand_pr_applications(brand_id);

ALTER TABLE pr_brands
    ADD COLUMN IF NOT EXISTS pr_example_posts JSONB;

ALTER TABLE creators
    ADD COLUMN IF NOT EXISTS shipping_address JSONB;

ALTER TABLE brand_pr_applications
    ADD COLUMN IF NOT EXISTS source VARCHAR(32);

CREATE TABLE IF NOT EXISTS brand_pr_events (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER,
    event VARCHAR(64) NOT NULL,
    source VARCHAR(32),
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_pr_events_event
    ON brand_pr_events(event, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_brand_pr_events_creator
    ON brand_pr_events(creator_id, created_at DESC);

COMMENT ON TABLE brand_pr_applications IS
    'Creator applied to a gifted brand PR roster. Status: review | ships | posted | declined. Source: foryou | directory | related | deeplink.';
COMMENT ON TABLE brand_pr_events IS
    'Creator apply-funnel events for adoption: apply_home_view, apply_opened, apply_submitted, apply_paywall, apply_related_click.';
COMMENT ON COLUMN pr_brands.pr_example_posts IS
    'Optional previous PR examples: [{url, title, handle}]. Empty means show no examples.';
