-- Migration: Add currency column to sponsor_drafts table
-- This migration adds a currency column to store the currency for each ad slot
-- Default value is 'EUR' to maintain backward compatibility

-- Check if column exists before adding (PostgreSQL)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'sponsor_drafts' 
        AND column_name = 'currency'
    ) THEN
        ALTER TABLE sponsor_drafts 
        ADD COLUMN currency VARCHAR(3) DEFAULT 'EUR' NOT NULL;
        
        -- Update existing rows to have EUR as default
        UPDATE sponsor_drafts 
        SET currency = 'EUR' 
        WHERE currency IS NULL;
    END IF;
END $$;

-- For SQLite (if using SQLite instead of PostgreSQL)
-- Uncomment the following if using SQLite:
-- ALTER TABLE sponsor_drafts ADD COLUMN currency VARCHAR(3) DEFAULT 'EUR';

