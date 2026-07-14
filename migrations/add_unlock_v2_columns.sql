-- Migration: Add columns for Unlock Modal V2 (verdict-first redesign)
-- Run this once in your database

-- Step 1: Add hero_variant column for A/B testing
-- 'A' = control (old copy), 'B' = new verdict copy (expected winner)
ALTER TABLE users ADD COLUMN IF NOT EXISTS hero_variant CHAR(1);

-- Step 2: Add fast_mode_enabled for power users who want to skip animations (v2 feature)
ALTER TABLE users ADD COLUMN IF NOT EXISTS fast_mode_enabled BOOLEAN DEFAULT FALSE;

-- Note: unlock_completed_count is NOT needed - we already have total_unlocks column
-- which tracks exactly the same thing (incremented on each new unlock)

-- Step 3: Create index on hero_variant for analytics queries
CREATE INDEX IF NOT EXISTS idx_users_hero_variant ON users(hero_variant);

-- Step 4: Verify the migration
SELECT 'Migration complete!' as status;

-- View new columns
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'users' AND column_name IN ('hero_variant', 'fast_mode_enabled', 'total_unlocks');
