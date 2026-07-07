-- Migration: Add pitch optimization fields for improved AI pitch generation

-- primary_format: Creator's main content format
-- Options: TikTok videos, Instagram Reels, YouTube Shorts, Instagram posts
ALTER TABLE creators ADD COLUMN IF NOT EXISTS primary_format VARCHAR(100);

COMMENT ON COLUMN creators.primary_format IS 'Primary content format: TikTok videos, Instagram Reels, YouTube Shorts, Instagram posts';
