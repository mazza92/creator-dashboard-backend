-- Migration: Add total_unlocks column to users table
-- Purpose: Track total usage/credits consumed by each user
-- Run this once in your Supabase SQL Editor

-- Step 1: Add the column (if it doesn't exist)
ALTER TABLE users ADD COLUMN IF NOT EXISTS total_unlocks INTEGER DEFAULT 0;

-- Step 2: Backfill from existing brand_unlocks data
UPDATE users u
SET total_unlocks = (
    SELECT COUNT(*)
    FROM brand_unlocks bu
    JOIN creators c ON bu.creator_id = c.id
    WHERE c.user_id = u.id
)
WHERE EXISTS (
    SELECT 1 FROM creators c WHERE c.user_id = u.id
);

-- Verify the migration
SELECT
    u.id,
    u.email,
    u.role,
    u.total_unlocks,
    u.created_at
FROM users u
WHERE u.total_unlocks > 0
ORDER BY u.total_unlocks DESC
LIMIT 20;
