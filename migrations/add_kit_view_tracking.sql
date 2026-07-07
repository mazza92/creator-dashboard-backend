-- Migration: Enhanced Kit View Tracking for Pro Conversion
-- Adds brand attribution to kit views for "Who Viewed Your Kit" feature

-- Add kit_token to creator_pipeline for tracking which pitch led to a view
ALTER TABLE creator_pipeline ADD COLUMN IF NOT EXISTS kit_token VARCHAR(16) UNIQUE;
CREATE INDEX IF NOT EXISTS idx_creator_pipeline_kit_token ON creator_pipeline(kit_token);

-- Add brand attribution columns to kit_views (if table exists)
-- If kit_views doesn't exist with these columns, create it fresh
DO $$
BEGIN
    -- Check if brand_id column exists
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'kit_views' AND column_name = 'brand_id'
    ) THEN
        ALTER TABLE kit_views ADD COLUMN brand_id INTEGER REFERENCES pr_brands(id);
        ALTER TABLE kit_views ADD COLUMN pipeline_id INTEGER REFERENCES creator_pipeline(id);
        ALTER TABLE kit_views ADD COLUMN view_count INTEGER DEFAULT 1;
        CREATE INDEX idx_kit_views_brand ON kit_views(brand_id);
        CREATE INDEX idx_kit_views_pipeline ON kit_views(pipeline_id);
    END IF;
END $$;

-- Create kit_views table if it doesn't exist (with full schema)
CREATE TABLE IF NOT EXISTS kit_views (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER REFERENCES pr_brands(id),
    pipeline_id INTEGER REFERENCES creator_pipeline(id),
    viewed_at TIMESTAMP DEFAULT NOW(),
    ip_hash VARCHAR(64),
    referrer TEXT,
    view_count INTEGER DEFAULT 1,
    interaction_type VARCHAR(50) DEFAULT 'page_view'
);

CREATE INDEX IF NOT EXISTS idx_kit_views_creator ON kit_views(creator_id);
CREATE INDEX IF NOT EXISTS idx_kit_views_creator_brand ON kit_views(creator_id, brand_id);
CREATE INDEX IF NOT EXISTS idx_kit_views_viewed_at ON kit_views(viewed_at);
