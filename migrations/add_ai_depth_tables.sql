-- AI Depth Upgrade Migration
-- Creates tables for creator profile enrichment, brand context, and curated fallbacks

-- ============================================================================
-- Part 1: creator_profile_data - Stores scraped + vision-analyzed creator data
-- ============================================================================
CREATE TABLE IF NOT EXISTS creator_profile_data (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id INTEGER NOT NULL UNIQUE,  -- References users.id (integer, not UUID)

  -- Platform metadata
  primary_platform VARCHAR(20),        -- 'instagram' | 'tiktok'
  handle VARCHAR(60) NOT NULL,
  full_name VARCHAR(200),
  raw_bio TEXT,
  external_url TEXT,

  -- Public stats (scraped)
  follower_count INT,
  following_count INT,
  post_count INT,
  is_verified BOOLEAN,
  is_public BOOLEAN,
  is_business_account BOOLEAN,
  business_category VARCHAR(80),

  -- Derived metrics
  engagement_rate NUMERIC(5,2),
  posting_cadence_per_week NUMERIC(4,1),
  latest_post_days_ago INT,

  -- Bio signals
  has_collab_email BOOLEAN,
  collab_email_extracted VARCHAR(200),
  bio_niche_keywords TEXT[],

  -- Vision analysis
  primary_niche VARCHAR(30),
  primary_niche_confidence INT,
  secondary_niches TEXT[],
  content_format_breakdown JSONB,
  aesthetic JSONB,                     -- full aesthetic object
  content_themes TEXT[],
  brand_readiness_signals JSONB,
  content_gaps TEXT[],
  brands_already_tagged TEXT[],

  -- Content archive
  recent_post_thumbnails TEXT[],       -- URLs of last 9 thumbnails
  recent_captions TEXT[],              -- last 12 captions

  -- Confidence + freshness
  data_confidence VARCHAR(20) DEFAULT 'scraped',
  -- 'scraped' | 'scraped_partial' | 'self_declared' | 'verified_oauth'
  vision_analysis_status VARCHAR(20) DEFAULT 'pending',
  -- 'pending' | 'success' | 'failed'
  scraped_at TIMESTAMP DEFAULT NOW(),
  last_refresh_at TIMESTAMP DEFAULT NOW(),
  next_refresh_at TIMESTAMP           -- scheduled weekly
);

CREATE INDEX IF NOT EXISTS idx_creator_profile_user ON creator_profile_data(user_id);
CREATE INDEX IF NOT EXISTS idx_creator_profile_niche ON creator_profile_data(primary_niche);
CREATE INDEX IF NOT EXISTS idx_creator_profile_refresh ON creator_profile_data(next_refresh_at)
  WHERE data_confidence = 'scraped';
CREATE INDEX IF NOT EXISTS idx_creator_profile_handle ON creator_profile_data(handle);

-- ============================================================================
-- Part 2: brand_context - Stores enriched brand data for matching
-- ============================================================================
CREATE TABLE IF NOT EXISTS brand_context (
  brand_id INTEGER PRIMARY KEY,  -- References pr_brands.id (integer, not UUID)

  -- Aesthetic (Gemini-analyzed from brand's own IG/website)
  aesthetic_color_palette VARCHAR(30),
  aesthetic_specific_colors TEXT[],
  aesthetic_style VARCHAR(40),
  aesthetic_descriptors TEXT[],

  -- Content preferences (learned from historical accepted creators)
  preferred_content_formats TEXT[],    -- e.g. ["Reels", "before_after", "GRWM"]
  preferred_content_themes TEXT[],

  -- Historical accepted creator profile (aggregated from reply data)
  accepted_follower_range_min INT,
  accepted_follower_range_max INT,
  accepted_engagement_rate_min NUMERIC(5,2),
  accepted_engagement_rate_median NUMERIC(5,2),
  accepted_niche_primary VARCHAR(30),
  accepted_niches_all TEXT[],

  -- Brand-specific signals
  hero_products TEXT[],
  recent_launches TEXT[],
  target_audience_desc TEXT,           -- e.g. "women 25-40, textured hair"
  brand_mission_summary TEXT,          -- 1-2 sentences from their site
  brand_instagram_handle VARCHAR(60),

  -- Meta
  enriched_at TIMESTAMP,
  next_enrichment_at TIMESTAMP,
  data_sources JSONB                   -- {"instagram_scraped": true, ...}
);

CREATE INDEX IF NOT EXISTS idx_brand_context_enriched ON brand_context(enriched_at);
CREATE INDEX IF NOT EXISTS idx_brand_context_next_enrich ON brand_context(next_enrichment_at);

-- ============================================================================
-- Part 3: brand_curated_fallbacks - Human-authored fallback content per brand
-- ============================================================================
CREATE TABLE IF NOT EXISTS brand_curated_fallbacks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand_id INTEGER NOT NULL,  -- References pr_brands.id (integer, not UUID)
  niche VARCHAR(30),                   -- optional: niche-specific fallback

  -- Curated reasons (3-5 per brand)
  reasons JSONB NOT NULL,              -- [{"chip_text": "...", "detail": "..."}, ...]

  -- Curated quick wins (2-3 per brand)
  quick_wins JSONB NOT NULL,           -- [{"emoji": "...", "action_title": "...", "note": "..."}, ...]

  -- Meta
  authored_by VARCHAR(100),
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),

  UNIQUE(brand_id, niche)
);

CREATE INDEX IF NOT EXISTS idx_brand_fallbacks_brand ON brand_curated_fallbacks(brand_id);

-- ============================================================================
-- Part 4: Feature flag for AI depth v2
-- ============================================================================
-- Add feature flag column if not exists
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'users' AND column_name = 'ai_depth_v2_enabled') THEN
    ALTER TABLE users ADD COLUMN ai_depth_v2_enabled BOOLEAN DEFAULT FALSE;
  END IF;
END $$;

-- ============================================================================
-- Part 5: Add unlock validation tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS unlock_validation_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id INTEGER,  -- References users.id (integer, nullable)
  brand_id INTEGER NOT NULL,  -- References pr_brands.id (integer)
  attempt_number INT NOT NULL,
  validation_issues TEXT[],
  used_fallback BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_unlock_validation_user ON unlock_validation_log(user_id);
CREATE INDEX IF NOT EXISTS idx_unlock_validation_brand ON unlock_validation_log(brand_id);
CREATE INDEX IF NOT EXISTS idx_unlock_validation_created ON unlock_validation_log(created_at);
