-- =====================================================
-- Add Target Categories to PR Offers Table
-- =====================================================
-- This migration adds target_categories column to pr_offers
-- to enable matching PR offers with creator wishlists
-- =====================================================

-- Add target_categories column (JSONB to store array of category strings)
ALTER TABLE pr_offers 
ADD COLUMN IF NOT EXISTS target_categories JSONB DEFAULT '[]'::jsonb;

-- Add index for faster matching queries
CREATE INDEX IF NOT EXISTS idx_pr_offers_target_categories ON pr_offers USING GIN (target_categories);

-- =====================================================
-- Migration Complete!
-- =====================================================
-- The target_categories JSONB column will store:
-- ["Skincare & Beauty", "Wellness & Fitness"]
--
-- This enables matching offers to creators who have
-- these categories in their PR wishlist
-- =====================================================

