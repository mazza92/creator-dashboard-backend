-- Migration: Sync niche to pr_wishlist for all existing creators
-- This automatically populates pr_wishlist from niche for creators who haven't set PR preferences yet
-- Run this once to backfill existing creators
--
-- IMPORTANT: This migration ONLY READS from the niche column and NEVER MODIFIES it.
-- The niche column remains completely unchanged. We only convert/parse the niche value
-- when writing to pr_wishlist.

-- NOTE: All direct SQL modifications (UPDATE/INSERT/DELETE) have been removed to avoid breaking the app.
-- 
-- To backfill pr_wishlist from niche, use the Python script instead:
--   python scripts/backfill_pr_wishlist_from_niche.py
--
-- The Python script uses the safe sync_niche_to_pr_wishlist() function which:
-- - Only reads from niche (never modifies it)
-- - Handles all niche formats (JSON array string, single string, list)
-- - Properly converts format to match pr_wishlist requirements
-- - Works with both JSONB column and separate table approaches
-- - Includes proper error handling and logging

-- Verify the update
SELECT 
  COUNT(*) as total_creators,
  COUNT(CASE WHEN niche IS NOT NULL AND niche != '' THEN 1 END) as creators_with_niche,
  COUNT(CASE WHEN pr_wishlist IS NOT NULL AND pr_wishlist != '[]'::jsonb THEN 1 END) as creators_with_pr_wishlist
FROM creators;

