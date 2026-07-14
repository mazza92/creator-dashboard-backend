-- Migration: Add AI Coaching columns to pr_packages table
-- This ensures we store the full AI strategy content when a brand is unlocked,
-- so revisiting shows the exact same coaching data (not regenerated)

-- Step 1: Add AI coaching columns to pr_packages
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_status VARCHAR(20);
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_coaching JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_verdict JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_reasons JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_quick_wins JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_better_matches JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ai_profile_snapshot JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS is_coaching BOOLEAN DEFAULT FALSE;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS is_low_follower BOOLEAN DEFAULT FALSE;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS ugc_guide JSONB;
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS fit_tier VARCHAR(20);
ALTER TABLE pr_packages ADD COLUMN IF NOT EXISTS used_ai_depth BOOLEAN DEFAULT FALSE;

-- Step 2: Add index for querying by status
CREATE INDEX IF NOT EXISTS idx_pr_packages_ai_status ON pr_packages(ai_status);

-- Step 3: Verify the migration
SELECT 'Migration complete! Added AI coaching columns to pr_packages.' as status;

-- View new columns
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'pr_packages'
  AND column_name IN ('ai_status', 'ai_coaching', 'ai_verdict', 'ai_reasons',
                      'ai_quick_wins', 'ai_better_matches', 'ai_profile_snapshot',
                      'is_coaching', 'is_low_follower', 'ugc_guide', 'fit_tier', 'used_ai_depth');
