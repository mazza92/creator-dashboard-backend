-- Opportunities Feature Database Schema
-- Migration: Add opportunities (brand casting calls) and applications tables

-- ============================================
-- 1. OPPORTUNITIES TABLE (Brand posting)
-- ============================================
CREATE TABLE IF NOT EXISTS opportunities (
    id SERIAL PRIMARY KEY,

    -- Brand Info
    brand_name VARCHAR(255) NOT NULL,
    brand_website VARCHAR(500),
    brand_email VARCHAR(255) NOT NULL,
    brand_category VARCHAR(100),
    brand_logo_url VARCHAR(500),

    -- Campaign Details
    product_name VARCHAR(255) NOT NULL,
    campaign_description TEXT NOT NULL,
    pr_value_usd INTEGER,
    creator_count_range VARCHAR(50),  -- '3-5' | '5-10' | '10-20'

    -- Targeting
    shipping_regions TEXT[],          -- ['US', 'UK', 'AU']
    follower_ranges TEXT[],           -- ['1K-10K', '10K-50K']
    content_types TEXT[],             -- ['TikTok', 'Reel']
    creator_niches TEXT[],            -- ['Fitness', 'Wellness']

    -- Additional
    additional_notes TEXT,
    application_deadline DATE,

    -- Spots Management
    spots_total INTEGER NOT NULL DEFAULT 5,
    spots_filled INTEGER NOT NULL DEFAULT 0,

    -- Status & Timestamps
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                                      -- pending | live | paused | closed | rejected
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    published_at TIMESTAMP,
    closes_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for fast queries
CREATE INDEX idx_opportunities_status ON opportunities(status);
CREATE INDEX idx_opportunities_niches ON opportunities USING GIN(creator_niches);
CREATE INDEX idx_opportunities_regions ON opportunities USING GIN(shipping_regions);
CREATE INDEX idx_opportunities_created ON opportunities(created_at DESC);
CREATE INDEX idx_opportunities_closes ON opportunities(closes_at);

-- ============================================
-- 2. OPPORTUNITY APPLICATIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS opportunity_applications (
    id SERIAL PRIMARY KEY,

    -- Relations
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    -- Timestamps
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Status
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                                      -- pending | approved | declined

    -- Notification Tracking
    brand_notified_at TIMESTAMP,
    creator_notified_at TIMESTAMP,

    -- Ensure one application per creator per opportunity
    UNIQUE(opportunity_id, creator_id)
);

-- Indexes for performance
CREATE INDEX idx_opp_applications_opportunity ON opportunity_applications(opportunity_id);
CREATE INDEX idx_opp_applications_creator ON opportunity_applications(creator_id);
CREATE INDEX idx_opp_applications_status ON opportunity_applications(status);

-- ============================================
-- 3. TRIGGER: Update timestamps automatically
-- ============================================
CREATE TRIGGER update_opportunities_updated_at
    BEFORE UPDATE ON opportunities
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- MIGRATION COMPLETE
-- ============================================
