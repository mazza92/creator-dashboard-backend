-- Migration: Create media_kits table for creator media kit builder
-- Run this migration against your database

-- Create media_kits table
CREATE TABLE IF NOT EXISTS media_kits (
    id SERIAL PRIMARY KEY,
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    -- Basic Info (Step 1)
    display_name VARCHAR(255) NOT NULL,
    username VARCHAR(100) NOT NULL,
    tagline VARCHAR(255),
    profile_photo_url VARCHAR(500),
    location VARCHAR(100),

    -- Social Stats (Step 2)
    total_followers INT DEFAULT 0,
    engagement_rate DECIMAL(5, 2),
    platforms JSONB DEFAULT '[]'::jsonb,

    -- Content Categories (Step 3)
    niches JSONB DEFAULT '[]'::jsonb,
    content_types JSONB DEFAULT '[]'::jsonb,

    -- Portfolio - Past Collaborations (Step 4)
    collaborations JSONB DEFAULT '[]'::jsonb,

    -- Rates & Packages (Step 5)
    rates JSONB DEFAULT '[]'::jsonb,
    currency VARCHAR(3) DEFAULT 'USD',
    accepts_gifted BOOLEAN DEFAULT true,
    accepts_paid BOOLEAN DEFAULT true,

    -- Template & Styling
    template_id INT DEFAULT 1,

    -- Publishing Status
    is_published BOOLEAN DEFAULT false,
    published_at TIMESTAMP,
    publish_count INT DEFAULT 0,

    -- Analytics (Pro feature)
    view_count INT DEFAULT 0,

    -- Draft Auto-save
    draft_data JSONB,
    last_draft_at TIMESTAMP,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Constraints
    CONSTRAINT media_kits_creator_unique UNIQUE (creator_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_media_kits_creator ON media_kits(creator_id);
CREATE INDEX IF NOT EXISTS idx_media_kits_username ON media_kits(username);
CREATE INDEX IF NOT EXISTS idx_media_kits_published ON media_kits(is_published) WHERE is_published = true;

-- Add columns to creators table for quick lookups
ALTER TABLE creators ADD COLUMN IF NOT EXISTS has_media_kit BOOLEAN DEFAULT false;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS media_kit_url VARCHAR(500);

-- Create view analytics table (for Pro users)
CREATE TABLE IF NOT EXISTS media_kit_views (
    id SERIAL PRIMARY KEY,
    media_kit_id INT NOT NULL REFERENCES media_kits(id) ON DELETE CASCADE,
    viewer_ip VARCHAR(50),
    referrer VARCHAR(500),
    viewed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_kit_views_kit ON media_kit_views(media_kit_id);
CREATE INDEX IF NOT EXISTS idx_media_kit_views_date ON media_kit_views(viewed_at);
