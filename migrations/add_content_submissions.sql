-- Brand Content Hub: creator organic-content submissions
-- Integer PKs to match creators.id / pr_brands.id (this app is not UUID).

CREATE TABLE IF NOT EXISTS creator_content_submissions (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    post_url TEXT NOT NULL,
    post_platform TEXT,
    brand_id INTEGER REFERENCES pr_brands(id) ON DELETE SET NULL,
    brand_name_freetext TEXT,
    content_type TEXT NOT NULL,
    description TEXT,
    consent_given BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending_review',
    admin_notes TEXT,
    rejection_reason TEXT,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    pushed_to_brand_at TIMESTAMPTZ,
    brand_response_status TEXT,
    brand_response_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (creator_id, post_url)
);

CREATE INDEX IF NOT EXISTS idx_ccs_creator ON creator_content_submissions(creator_id);
CREATE INDEX IF NOT EXISTS idx_ccs_brand ON creator_content_submissions(brand_id);
CREATE INDEX IF NOT EXISTS idx_ccs_status ON creator_content_submissions(status);
CREATE INDEX IF NOT EXISTS idx_ccs_created ON creator_content_submissions(created_at DESC);
