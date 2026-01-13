-- REVERT Migration: Undo the sync_niche_to_pr_wishlist UPDATE
-- Use this ONLY if you ran the UPDATE query and need to revert it
-- This will clear pr_wishlist for creators where it was auto-populated from niche

-- WARNING: This will DELETE pr_wishlist data that was auto-populated from niche.
-- Only run this if you need to undo the sync operation.

-- Option 1: Clear pr_wishlist for all creators (if you want to start fresh)
-- UPDATE creators
-- SET pr_wishlist = NULL
-- WHERE pr_wishlist IS NOT NULL;

-- Option 2: Clear pr_wishlist only for creators who have a niche
-- (This assumes pr_wishlist was populated from niche)
-- UPDATE creators
-- SET pr_wishlist = NULL
-- WHERE niche IS NOT NULL 
--   AND niche != ''
--   AND pr_wishlist IS NOT NULL;

-- Option 3: Clear pr_wishlist only if it matches the niche value
-- (More targeted - only reverts if pr_wishlist contains the niche)
-- UPDATE creators
-- SET pr_wishlist = NULL
-- WHERE niche IS NOT NULL 
--   AND niche != ''
--   AND pr_wishlist IS NOT NULL
--   AND (
--     -- If niche is a JSON array, check if pr_wishlist matches
--     (niche::text ~ '^\[.*\]$' AND pr_wishlist::text = niche::text)
--     OR
--     -- If niche is a single string, check if pr_wishlist contains it
--     (niche::text !~ '^\[.*\]$' AND pr_wishlist::jsonb @> jsonb_build_array(niche))
--   );

-- Verify before reverting
SELECT 
  COUNT(*) as total_creators,
  COUNT(CASE WHEN niche IS NOT NULL AND niche != '' THEN 1 END) as creators_with_niche,
  COUNT(CASE WHEN pr_wishlist IS NOT NULL AND pr_wishlist != '[]'::jsonb THEN 1 END) as creators_with_pr_wishlist,
  COUNT(CASE WHEN niche IS NOT NULL AND niche != '' AND pr_wishlist IS NOT NULL AND pr_wishlist != '[]'::jsonb THEN 1 END) as creators_with_both
FROM creators;

