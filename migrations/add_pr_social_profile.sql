-- Cached TikTok/Instagram profile stats for the apply-step social header.
-- Filled lazily on apply-pack. Never store invented counts.
ALTER TABLE pr_brands
    ADD COLUMN IF NOT EXISTS pr_social_profile JSONB;
