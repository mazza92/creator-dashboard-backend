-- Gifting Radar: cache how to reach a discovered brand (website email, Hunter, or TikTok DM).
ALTER TABLE discovered_brands
  ADD COLUMN IF NOT EXISTS contact_email TEXT,
  ADD COLUMN IF NOT EXISTS contact_source TEXT,
  ADD COLUMN IF NOT EXISTS contact_checked_at TIMESTAMPTZ;
