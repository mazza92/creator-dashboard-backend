-- Migration: Add open_pr_featured column to pr_brands table
-- Purpose: Tag brands for the "Open PR Applications" featured section in directory
-- Run this in your Supabase SQL Editor

-- Step 1: Add open_pr_featured column
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS open_pr_featured BOOLEAN DEFAULT FALSE;

-- Step 2: Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_pr_brands_open_pr_featured ON pr_brands(open_pr_featured) WHERE open_pr_featured = TRUE;

-- Step 3: Verify the migration
SELECT 'Migration complete!' as status;

-- View the new column
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'pr_brands' AND column_name = 'open_pr_featured';
