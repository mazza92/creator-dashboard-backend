-- Migration: Add brand enrichment columns for dynamic pitch generation
-- These columns store AI-extracted data from brand websites

-- Hero product: the brand's most well-known or bestselling product
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS hero_product VARCHAR(255);

-- Target audience: who the brand sells to (e.g. "women 25-40 into skincare")
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS target_audience VARCHAR(255);

-- Brand tone: premium / casual / wellness / functional / luxury / playful
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS tone VARCHAR(50);

-- Price point: estimated single product price in USD
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS price_point INTEGER;

-- Track when enrichment was last run
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMP;

-- Index for finding unenriched brands
CREATE INDEX IF NOT EXISTS idx_pr_brands_enriched_at ON pr_brands(enriched_at);

COMMENT ON COLUMN pr_brands.hero_product IS 'Brand''s most well-known or bestselling product, extracted via AI from website';
COMMENT ON COLUMN pr_brands.target_audience IS 'Who the brand targets, e.g. "women 25-40 into skincare and wellness"';
COMMENT ON COLUMN pr_brands.tone IS 'Brand voice: premium, casual, wellness, functional, luxury, or playful';
COMMENT ON COLUMN pr_brands.price_point IS 'Estimated single product price in USD';
COMMENT ON COLUMN pr_brands.enriched_at IS 'Timestamp when AI enrichment was last run';
