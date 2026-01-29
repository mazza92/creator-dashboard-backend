-- Migration: Add total_unlocks tracking to users table
-- Purpose: Track total usage/credits consumed by each user
-- Run this once in your Supabase SQL Editor

-- Step 1: Create brand_unlocks table for tracking unlock events
CREATE TABLE IF NOT EXISTS brand_unlocks (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    unlocked_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(creator_id, brand_id)
);

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_creator ON brand_unlocks(creator_id);
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_brand ON brand_unlocks(brand_id);

-- Step 2: Add total_unlocks column to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS total_unlocks INTEGER DEFAULT 0;

-- Step 3: Verify the migration
SELECT 'Migration complete!' as status;

-- View users table structure
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'total_unlocks';
