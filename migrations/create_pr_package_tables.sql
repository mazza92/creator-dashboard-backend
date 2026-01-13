-- =====================================================
-- PR Package Feature: Database Schema
-- =====================================================
-- Creates tables for PR Package offers and submissions
-- Run this in your PostgreSQL database (Supabase SQL Editor)
-- =====================================================

-- Table 1: pr_offers
-- Stores PR package offers from brands to creators
CREATE TABLE IF NOT EXISTS pr_offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    
    -- Offer details
    offer_title VARCHAR(255) NOT NULL,
    products_offered TEXT NOT NULL, -- JSON or text description
    products_value DECIMAL(10, 2), -- Optional: total value of products
    
    -- Deliverables (stored as JSON array)
    deliverables_required JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Example: ["1 x TikTok Video (1 min)", "1 x Instagram Reel (1 min)", "1 x Instagram Post (Static)", "3 x Story Frames with Link"]
    
    -- Requirements
    mandatory_requirements TEXT, -- e.g., "Must tag @GlowySkincare and use #GlowySerum"
    content_deadline_days INTEGER DEFAULT 14, -- Days after receiving product
    
    -- State machine status
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    -- Possible values: 'pending', 'accepted', 'declined', 'awaiting_shipment', 
    --                  'shipped', 'product_received', 'content_in_progress', 
    --                  'content_submitted', 'completed', 'cancelled'
    
    -- Shipping info
    shipping_address JSONB, -- Creator's shipping address (stored when offer is accepted)
    tracking_number VARCHAR(255),
    shipped_at TIMESTAMP,
    product_received_at TIMESTAMP,
    
    -- Content submission
    content_submitted_at TIMESTAMP,
    content_urls JSONB DEFAULT '[]'::jsonb, -- Array of content URLs
    
    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMP,
    declined_at TIMESTAMP,
    declined_reason TEXT,
    completed_at TIMESTAMP,
    
    -- Metadata
    notes TEXT, -- Internal notes or brand notes
    
    CONSTRAINT valid_status CHECK (status IN (
        'pending', 'accepted', 'declined', 'awaiting_shipment', 
        'shipped', 'product_received', 'content_in_progress', 
        'content_submitted', 'completed', 'cancelled'
    ))
);

-- Table 2: pr_submissions
-- Stores individual content submissions for PR packages
CREATE TABLE IF NOT EXISTS pr_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id UUID NOT NULL REFERENCES pr_offers(id) ON DELETE CASCADE,
    
    -- Content details
    content_url TEXT NOT NULL,
    content_type VARCHAR(50), -- 'tiktok_video', 'instagram_reel', 'instagram_post', 'story', etc.
    platform VARCHAR(50), -- 'tiktok', 'instagram', etc.
    
    -- Metadata
    submitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_pr_offers_brand_id ON pr_offers(brand_id);
CREATE INDEX IF NOT EXISTS idx_pr_offers_creator_id ON pr_offers(creator_id);
CREATE INDEX IF NOT EXISTS idx_pr_offers_status ON pr_offers(status);
CREATE INDEX IF NOT EXISTS idx_pr_offers_created_at ON pr_offers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pr_submissions_offer_id ON pr_submissions(offer_id);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_pr_offers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update updated_at
CREATE TRIGGER trigger_update_pr_offers_updated_at
    BEFORE UPDATE ON pr_offers
    FOR EACH ROW
    EXECUTE FUNCTION update_pr_offers_updated_at();

-- =====================================================
-- Migration Complete!
-- =====================================================
-- Verify tables were created:
-- SELECT table_name FROM information_schema.tables 
-- WHERE table_name IN ('pr_offers', 'pr_submissions');
-- =====================================================

