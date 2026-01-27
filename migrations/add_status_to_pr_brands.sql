-- Migration: Add status column to pr_brands table
-- This enables draft/published workflow for brand admin

-- Add status column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'pr_brands' AND column_name = 'status'
    ) THEN
        ALTER TABLE pr_brands ADD COLUMN status VARCHAR(20) DEFAULT 'published';
    END IF;
END $$;

-- Add updated_at column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'pr_brands' AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE pr_brands ADD COLUMN updated_at TIMESTAMP;
    END IF;
END $$;

-- Add created_at column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'pr_brands' AND column_name = 'created_at'
    ) THEN
        ALTER TABLE pr_brands ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
    END IF;
END $$;

-- Add accepting_pr column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'pr_brands' AND column_name = 'accepting_pr'
    ) THEN
        ALTER TABLE pr_brands ADD COLUMN accepting_pr BOOLEAN DEFAULT TRUE;
    END IF;
END $$;

-- Create index on status for faster filtering
CREATE INDEX IF NOT EXISTS idx_pr_brands_status ON pr_brands(status);

-- Set all existing brands to 'published' status
UPDATE pr_brands SET status = 'published' WHERE status IS NULL;

COMMENT ON COLUMN pr_brands.status IS 'Brand status: draft or published. Only published brands show in directory.';
