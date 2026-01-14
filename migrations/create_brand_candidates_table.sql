-- Migration: Create brand_candidates staging table for PR Hunter automation
-- Purpose: Store discovered brands before manual approval/publishing to live pr_brands table
-- Created: 2026-01-14

CREATE TABLE IF NOT EXISTS brand_candidates (
    id SERIAL PRIMARY KEY,

    -- Discovery Data (Basic brand information)
    brand_name VARCHAR(255) NOT NULL,
    website_url VARCHAR(255),
    instagram_handle VARCHAR(255),
    tiktok_handle VARCHAR(255),

    -- Enrichment Data (The Human - PR contact person)
    pr_manager_name VARCHAR(255),
    pr_manager_linkedin VARCHAR(255),
    pr_manager_title VARCHAR(255),

    -- Contact Data (The Email)
    contact_email VARCHAR(255),
    email_source VARCHAR(50), -- e.g., 'Hunter', 'Apollo', 'Manual'
    verification_score INT DEFAULT 0, -- 0 to 100
    verification_status VARCHAR(20), -- 'valid', 'catch-all', 'invalid', 'unknown'
    is_catch_all BOOLEAN DEFAULT FALSE,

    -- Additional metadata
    domain VARCHAR(255), -- Cleaned domain (e.g., 'glowrecipe.com')
    logo_url VARCHAR(500), -- Fetched from Clearbit or similar
    description TEXT,

    -- Pipeline Status
    status VARCHAR(20) DEFAULT 'PENDING', -- PENDING, APPROVED, REJECTED
    discovery_source VARCHAR(100), -- e.g., 'TikTok Search: Skincare', 'Listicle: K-Beauty 2025'
    rejection_reason TEXT, -- Why it was rejected (for learning)

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    approved_by INTEGER REFERENCES users(id), -- Admin who approved

    -- Constraints
    UNIQUE(domain), -- Prevent duplicate domains
    UNIQUE(contact_email) -- Prevent duplicate emails
);

-- Indexes for performance
CREATE INDEX idx_brand_candidates_status ON brand_candidates(status);
CREATE INDEX idx_brand_candidates_created_at ON brand_candidates(created_at DESC);
CREATE INDEX idx_brand_candidates_verification_score ON brand_candidates(verification_score DESC);
CREATE INDEX idx_brand_candidates_domain ON brand_candidates(domain);

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_brand_candidates_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER brand_candidates_updated_at_trigger
BEFORE UPDATE ON brand_candidates
FOR EACH ROW
EXECUTE FUNCTION update_brand_candidates_updated_at();

-- Comments for documentation
COMMENT ON TABLE brand_candidates IS 'Staging table for PR Hunter automation - stores discovered brands before manual approval';
COMMENT ON COLUMN brand_candidates.verification_score IS 'Email verification score from 0-100 (95-100 = safe, <90 = risky, catch-all = 50)';
COMMENT ON COLUMN brand_candidates.status IS 'Workflow status: PENDING (needs review), APPROVED (moved to pr_brands), REJECTED (not suitable)';
COMMENT ON COLUMN brand_candidates.discovery_source IS 'Tracks how this brand was found (for optimizing future searches)';
