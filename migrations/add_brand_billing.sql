-- Brand Gifted UGC billing ($299/mo). Separate from scrape drafts / creator Pro.
-- Schema is also auto-created by services.brand_billing.ensure_brand_billing_schema.

CREATE TABLE IF NOT EXISTS brand_billing (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL UNIQUE REFERENCES pr_brands(id) ON DELETE CASCADE,
    email TEXT,
    plan VARCHAR(64) NOT NULL DEFAULT 'gifted_ugc_299',
    status VARCHAR(32) NOT NULL DEFAULT 'none',
    free_campaigns_used INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT brand_billing_status_check CHECK (
        status IN ('none', 'trial_used', 'active', 'past_due', 'canceled')
    ),
    CONSTRAINT brand_billing_free_campaigns_check CHECK (free_campaigns_used >= 0)
);

CREATE INDEX IF NOT EXISTS idx_brand_billing_stripe_customer
    ON brand_billing(stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_brand_billing_stripe_subscription
    ON brand_billing(stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_brand_billing_status
    ON brand_billing(status);

COMMENT ON TABLE brand_billing IS
    'Stripe billing for Brand Gifted UGC ($299/mo). First roster campaign is free; next mint requires status=active.';
COMMENT ON COLUMN brand_billing.free_campaigns_used IS
    'Incremented when the free trial campaign is marked shipped (or inferred from prior shipped campaigns).';
COMMENT ON COLUMN brand_billing.plan IS
    'Product key. Default gifted_ugc_299 maps to STRIPE_PRICE_ID_BRAND_GIFTED_MONTHLY.';
