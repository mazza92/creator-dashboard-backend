-- Migration: Create portfolio builder tables for the new media kit design
-- Run this migration against your database

-- ============================================
-- 1. Create portfolio_posts table
-- ============================================
CREATE TABLE IF NOT EXISTS portfolio_posts (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    post_url TEXT,
    platform VARCHAR(20) NOT NULL,          -- instagram | tiktok | youtube
    post_type VARCHAR(20) NOT NULL,          -- reel | photo | tiktok | youtube | story
    brand_name VARCHAR(200),
    collab_type VARCHAR(20) DEFAULT 'organic', -- gifted | paid | organic | own
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    thumbnail_url TEXT,
    display_order INTEGER DEFAULT 0,
    is_featured BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_posts_creator ON portfolio_posts(creator_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_posts_order ON portfolio_posts(creator_id, display_order);

-- ============================================
-- 2. Create kit_views table (separate from media_kit_views for the new system)
-- ============================================
CREATE TABLE IF NOT EXISTS kit_views (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    viewer_ip VARCHAR(45),
    viewer_ua TEXT,
    referrer TEXT,
    viewed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kit_views_creator ON kit_views(creator_id, viewed_at DESC);

-- ============================================
-- 3. Add kit-related columns to creators table
-- ============================================
ALTER TABLE creators
    ADD COLUMN IF NOT EXISTS kit_tagline TEXT,
    ADD COLUMN IF NOT EXISTS kit_published BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS kit_slug VARCHAR(100) UNIQUE,
    ADD COLUMN IF NOT EXISTS rates_reel INTEGER,
    ADD COLUMN IF NOT EXISTS rates_tiktok INTEGER,
    ADD COLUMN IF NOT EXISTS rates_photo INTEGER,
    ADD COLUMN IF NOT EXISTS rates_gifted BOOLEAN DEFAULT true;

-- Note: kit_slug defaults to username on first publish (handled in app logic)

-- ============================================
-- Comments for reference
-- ============================================
-- portfolio_posts.platform: 'instagram', 'tiktok', 'youtube'
-- portfolio_posts.post_type: 'reel', 'photo', 'story', 'tiktok', 'youtube', 'short'
-- portfolio_posts.collab_type: 'gifted', 'paid', 'organic', 'own'
-- rates_*: Integer values in USD
