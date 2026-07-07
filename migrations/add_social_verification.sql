-- Migration: Add Social Verification System
-- Run via Supabase SQL Editor
-- Date: 2026-07-04

-- ============================================================================
-- PART 1: Add social verification columns to creators table
-- ============================================================================

-- Platform and handle
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_platform VARCHAR(20);
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_handle VARCHAR(100);

-- Metrics from connected account
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_follower_count INTEGER DEFAULT 0;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_media_count INTEGER DEFAULT 0;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_is_public BOOLEAN DEFAULT FALSE;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_account_type VARCHAR(50);

-- Timestamps
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_connected_at TIMESTAMPTZ;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_last_checked_at TIMESTAMPTZ;

-- Verification status
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_verification_status VARCHAR(50) DEFAULT 'pending';
-- Values: 'pending' | 'verified' | 'failed_private' | 'failed_followers' | 'failed_posts' | 'failed_region' | 'failed_account_type'

-- OAuth tokens (encrypted)
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_oauth_token TEXT;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_oauth_refresh_token TEXT;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_token_expires_at TIMESTAMPTZ;

-- Grandfathering for existing users
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_verification_required_by TIMESTAMPTZ;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_verification_grandfathered BOOLEAN DEFAULT FALSE;

-- Performance index
CREATE INDEX IF NOT EXISTS idx_creators_social_verified ON creators(social_verified);
CREATE INDEX IF NOT EXISTS idx_creators_social_last_checked ON creators(social_last_checked_at);
CREATE INDEX IF NOT EXISTS idx_creators_social_platform ON creators(social_platform);


-- ============================================================================
-- PART 2: Create audit log table for verification checks
-- ============================================================================

CREATE TABLE IF NOT EXISTS social_verification_checks (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER REFERENCES creators(id) ON DELETE CASCADE,

    -- Check metadata
    check_type VARCHAR(50) NOT NULL,  -- 'initial' | 'weekly' | 'manual' | 'region_precheck'
    platform VARCHAR(20),              -- 'instagram' | 'tiktok' | null (for region-only checks)

    -- 5-Gate results
    gate_1_oauth_connected BOOLEAN DEFAULT FALSE,
    gate_2_account_public BOOLEAN DEFAULT FALSE,
    gate_3_follower_min_met BOOLEAN DEFAULT FALSE,   -- >= 500
    gate_4_content_min_met BOOLEAN DEFAULT FALSE,    -- >= 5 posts
    gate_5_region_allowed BOOLEAN DEFAULT FALSE,     -- NOT IN (India, Pakistan)

    -- Raw data captured
    raw_follower_count INTEGER,
    raw_media_count INTEGER,
    raw_account_type VARCHAR(50),
    raw_is_public BOOLEAN,
    user_country VARCHAR(10),

    -- Result
    verification_passed BOOLEAN DEFAULT FALSE,
    failure_reason VARCHAR(100),
    -- Values: 'restricted_region' | 'oauth_expired' | 'private' | 'below_follower_min' | 'below_post_min' | 'personal_account'

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    api_response_snapshot JSONB  -- Store raw API response for debugging
);

CREATE INDEX IF NOT EXISTS idx_verification_checks_creator ON social_verification_checks(creator_id);
CREATE INDEX IF NOT EXISTS idx_verification_checks_created ON social_verification_checks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_verification_checks_passed ON social_verification_checks(verification_passed);


-- ============================================================================
-- PART 3: Verification query
-- ============================================================================

-- Verify migration success:
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'creators'
AND column_name LIKE 'social_%'
ORDER BY column_name;

-- Check table created:
SELECT EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_name = 'social_verification_checks'
);
