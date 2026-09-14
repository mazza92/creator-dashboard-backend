-- TikTok Login Kit: stable open_id + light video snapshot from video.list
-- Safe to re-run (IF NOT EXISTS).

ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_open_id VARCHAR(128);
ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_oauth_videos JSONB;

CREATE INDEX IF NOT EXISTS idx_creators_social_open_id
  ON creators(social_open_id)
  WHERE social_open_id IS NOT NULL;
