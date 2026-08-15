-- =====================================================
-- Supabase SQL: Brand Content Hub submissions table
-- =====================================================
-- Run this in Supabase SQL Editor (production).
-- Safe to re-run (IF NOT EXISTS).
-- =====================================================

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

COMMENT ON TABLE creator_content_submissions IS 'Brand Content Hub: organic branded posts submitted by creators for admin review and manual brand outreach';
COMMENT ON COLUMN creator_content_submissions.status IS 'pending_review | approved | rejected | flagged | pushed_to_brand | brand_responded';
COMMENT ON COLUMN creator_content_submissions.content_type IS 'unboxing | review | grwm | haul | tutorial | lifestyle | other';
COMMENT ON COLUMN creator_content_submissions.brand_name_freetext IS 'Used only when brand_id is null; admin adds the brand to pr_brands during review';
COMMENT ON COLUMN creator_content_submissions.brand_response_status IS 'interested | not_interested | wants_more_content | wants_paid_collab | no_response';
