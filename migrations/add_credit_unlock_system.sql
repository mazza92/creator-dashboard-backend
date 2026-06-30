-- Credit Unlock System Migration
-- Run this BEFORE deploying the unlock function
-- ============================================

-- Step 1: Add unlock tracking columns to creators table
-- (Using creators since subscription_tier is already here)
ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_remaining INT DEFAULT 5;
ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_tier VARCHAR(20) DEFAULT 'free';
ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_reset_at TIMESTAMP;

-- Step 2: Create brand_unlocks table for permanent unlock tracking
CREATE TABLE IF NOT EXISTS brand_unlocks (
    id BIGSERIAL PRIMARY KEY,
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INT NOT NULL,  -- References pr_brands(id)
    unlocked_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(creator_id, brand_id)  -- Enforces "unlock once, free forever"
);

-- Create index for fast lookups
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_creator ON brand_unlocks(creator_id);
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_brand ON brand_unlocks(brand_id);

-- Step 3: Set Pro users to unlimited tier (no reset needed)
-- Uses existing subscription_tier from creators table
UPDATE creators
SET unlocks_tier = 'pro',
    unlocks_remaining = NULL,
    unlocks_reset_at = NULL
WHERE subscription_tier IN ('pro', 'elite');

-- Step 4: Set Free users to 5 unlocks, reset in 30 days
UPDATE creators
SET unlocks_tier = 'free',
    unlocks_remaining = 5,
    unlocks_reset_at = NOW() + INTERVAL '30 days'
WHERE subscription_tier IS NULL
   OR subscription_tier = 'free'
   OR subscription_tier = '';

-- Step 5: Backfill existing pitches as unlocks
-- CRITICAL: This preserves access to brands users already pitched
-- INNER JOIN ensures we only backfill brands that exist in pr_brands
-- The ON CONFLICT handles duplicate creator_id/brand_id pairs
INSERT INTO brand_unlocks (creator_id, brand_id, unlocked_at)
SELECT DISTINCT
    cp.creator_id,
    cp.brand_id,
    MIN(COALESCE(cp.pitched_at, cp.created_at)) as unlocked_at
FROM creator_pipeline cp
INNER JOIN pr_brands pb ON pb.id = cp.brand_id
WHERE cp.brand_id IS NOT NULL
  AND (cp.pitched_at IS NOT NULL OR cp.send_confirmed = TRUE)
GROUP BY cp.creator_id, cp.brand_id
ON CONFLICT (creator_id, brand_id) DO NOTHING;

-- Verification queries (run these to sanity-check before commit):
-- SELECT 'Backfilled ' || COUNT(*) || ' unlocks across ' || COUNT(DISTINCT creator_id) || ' creators' FROM brand_unlocks;
-- SELECT unlocks_tier, COUNT(*) FROM creators GROUP BY unlocks_tier;
