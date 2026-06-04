-- Migration: Add kit_interactions table for detailed click tracking
-- Purpose: Track portfolio clicks, share clicks, social clicks, contact clicks
-- Date: 2024-01-XX

-- Create kit_interactions table for detailed analytics
CREATE TABLE IF NOT EXISTS kit_interactions (
    id SERIAL PRIMARY KEY,
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    interaction_type VARCHAR(50) NOT NULL,  -- 'portfolio_click', 'share_click', 'social_click', 'contact_click'
    target_value VARCHAR(255),  -- e.g., 'instagram', 'tiktok', post_id, etc.
    referrer VARCHAR(500),
    viewer_ip VARCHAR(45),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_kit_interactions_creator ON kit_interactions(creator_id);
CREATE INDEX IF NOT EXISTS idx_kit_interactions_type ON kit_interactions(interaction_type);
CREATE INDEX IF NOT EXISTS idx_kit_interactions_created ON kit_interactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kit_interactions_creator_date ON kit_interactions(creator_id, created_at DESC);

-- Add comment for documentation
COMMENT ON TABLE kit_interactions IS 'Tracks detailed interactions on public media kits (portfolio clicks, share, social, contact)';
