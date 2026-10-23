-- Polly Gifting Radar (beta): public TikTok posts -> brands gifting small creators.
-- Server-only tables: RLS on with no policies, so only the backend role can read or write.
-- Metadata and links only; never video files.

CREATE TABLE IF NOT EXISTS tiktok_videos (
  tiktok_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  caption TEXT,
  hashtags TEXT[] NOT NULL DEFAULT '{}',
  mentions TEXT[] NOT NULL DEFAULT '{}',
  views INTEGER,
  likes INTEGER,
  created_at TIMESTAMPTZ,
  cover_url TEXT,
  is_ad BOOLEAN NOT NULL DEFAULT FALSE,
  is_sponsored BOOLEAN NOT NULL DEFAULT FALSE,
  language TEXT,
  creator_handle TEXT,
  creator_followers INTEGER,
  creator_region TEXT,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tiktok_videos_created_idx ON tiktok_videos (created_at DESC);
CREATE INDEX IF NOT EXISTS tiktok_videos_followers_idx ON tiktok_videos (creator_followers);
CREATE INDEX IF NOT EXISTS tiktok_videos_creator_idx ON tiktok_videos (creator_handle);
CREATE INDEX IF NOT EXISTS tiktok_videos_hashtags_idx ON tiktok_videos USING GIN (hashtags);
CREATE INDEX IF NOT EXISTS tiktok_videos_mentions_idx ON tiktok_videos USING GIN (mentions);

CREATE TABLE IF NOT EXISTS tiktok_creators (
  handle TEXT PRIMARY KEY,
  followers INTEGER,
  region TEXT,
  bio TEXT,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS discovered_brands (
  id SERIAL PRIMARY KEY,
  brand_key TEXT NOT NULL UNIQUE,
  brand_id INTEGER REFERENCES pr_brands(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  tiktok_handle TEXT,
  website TEXT,
  status TEXT NOT NULL DEFAULT 'new',          -- new | enriching | ready | rejected
  contact_requests INTEGER NOT NULL DEFAULT 0,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_signal_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS discovered_brands_brand_idx ON discovered_brands (brand_id);
CREATE INDEX IF NOT EXISTS discovered_brands_status_idx ON discovered_brands (status);

CREATE TABLE IF NOT EXISTS brand_signals (
  discovered_brand_id INTEGER NOT NULL REFERENCES discovered_brands(id) ON DELETE CASCADE,
  tiktok_id TEXT NOT NULL REFERENCES tiktok_videos(tiktok_id) ON DELETE CASCADE,
  signal_type TEXT NOT NULL,                   -- gifted | paid | affiliate
  niche TEXT NOT NULL,
  PRIMARY KEY (discovered_brand_id, tiktok_id, signal_type, niche)
);
CREATE INDEX IF NOT EXISTS brand_signals_niche_idx ON brand_signals (niche);
CREATE INDEX IF NOT EXISTS brand_signals_video_idx ON brand_signals (tiktok_id);

CREATE TABLE IF NOT EXISTS apify_query_cache (
  cache_key TEXT PRIMARY KEY,                  -- 'hashtag:prpackage', 'search:skincare pr unboxing'
  query_type TEXT NOT NULL,                    -- hashtag | search
  query TEXT NOT NULL,
  region TEXT,
  video_ids TEXT[] NOT NULL DEFAULT '{}',
  result_count INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,4),
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS apify_query_cache_expires_idx ON apify_query_cache (expires_at);

CREATE TABLE IF NOT EXISTS discovery_runs (
  id SERIAL PRIMARY KEY,
  creator_id INTEGER,
  niche TEXT,
  region TEXT,
  status TEXT NOT NULL DEFAULT 'running',     -- running | ingesting | done | failed
  served_from TEXT NOT NULL,                   -- database | partial_topup | full_apify
  cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
  apify_run_ids TEXT[] NOT NULL DEFAULT '{}',
  queries JSONB NOT NULL DEFAULT '[]'::jsonb,
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  videos_fetched INTEGER NOT NULL DEFAULT 0,
  brands_returned INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,4) NOT NULL DEFAULT 0,
  duration_ms INTEGER,
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS discovery_runs_creator_idx ON discovery_runs (creator_id, created_at DESC);
CREATE INDEX IF NOT EXISTS discovery_runs_created_idx ON discovery_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS discovery_runs_status_idx ON discovery_runs (status) WHERE status IN ('running', 'ingesting');

ALTER TABLE tiktok_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE tiktok_creators ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovered_brands ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE apify_query_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_runs ENABLE ROW LEVEL SECURITY;
