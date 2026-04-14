-- Migration: Add roundup_featured column to pr_brands table
-- Purpose: Tag brands to be featured in the next weekly email roundup
-- Run this in your Supabase SQL Editor

-- Step 1: Add roundup_featured column
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS roundup_featured BOOLEAN DEFAULT FALSE;

-- Step 2: Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_pr_brands_roundup_featured ON pr_brands(roundup_featured) WHERE roundup_featured = TRUE;

-- Step 3: Verify the migration
SELECT 'Migration complete!' as status;

-- View the new column
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'pr_brands' AND column_name = 'roundup_featured';
