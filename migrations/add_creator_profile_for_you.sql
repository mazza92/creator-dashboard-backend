-- Migration: Add creator profile fields for "For You" personalized recommendations
-- These columns power the match scores in the For You section

ALTER TABLE creators
  ADD COLUMN IF NOT EXISTS creator_niches TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS creator_followers INT DEFAULT 0;

-- creator_niches: array of strings e.g. ['beauty', 'lifestyle']
-- creator_followers: integer e.g. 12000
-- These power the match score in the "Matched for You" section

COMMENT ON COLUMN creators.creator_niches IS 'Creator niche categories for personalized brand matching';
COMMENT ON COLUMN creators.creator_followers IS 'Total follower count across platforms for brand matching';
