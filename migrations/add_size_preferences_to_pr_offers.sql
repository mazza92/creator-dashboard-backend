-- =====================================================
-- Add size_preferences column to pr_offers table
-- =====================================================
-- This allows brands to see creator size preferences
-- when viewing accepted PR offers
-- =====================================================

-- Add size_preferences column (JSONB to store size preferences)
ALTER TABLE pr_offers 
ADD COLUMN IF NOT EXISTS size_preferences JSONB;

-- =====================================================
-- Migration Complete!
-- =====================================================
-- The size_preferences JSONB column will store:
-- {
--   "clothing": {
--     "shirt": "M",
--     "pants": "32",
--     "shoes": "10"
--   },
--   "skincare": "sensitive",
--   "other": "Any additional preferences"
-- }
-- =====================================================

