-- Migration: Add onboarding email sequence tracking columns to existing users table
-- This script adds the required columns for the 5-email onboarding sequence
-- Run this on your production database

-- Add columns for tracking each email in the sequence (only if they don't exist)
DO $$ 
BEGIN
    -- Email 1: Welcome (5 minutes after signup)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_1_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_1_sent_at TIMESTAMP;
        RAISE NOTICE 'Added column: onboarding_email_1_sent_at';
    ELSE
        RAISE NOTICE 'Column onboarding_email_1_sent_at already exists';
    END IF;
    
    -- Email 2: Value Focus (30-60 minutes after signup)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_2_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_2_sent_at TIMESTAMP;
        RAISE NOTICE 'Added column: onboarding_email_2_sent_at';
    ELSE
        RAISE NOTICE 'Column onboarding_email_2_sent_at already exists';
    END IF;
    
    -- Email 3: Social Proof (24 hours after signup)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_3_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_3_sent_at TIMESTAMP;
        RAISE NOTICE 'Added column: onboarding_email_3_sent_at';
    ELSE
        RAISE NOTICE 'Column onboarding_email_3_sent_at already exists';
    END IF;
    
    -- Email 4: Support (3 days after signup)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_4_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_4_sent_at TIMESTAMP;
        RAISE NOTICE 'Added column: onboarding_email_4_sent_at';
    ELSE
        RAISE NOTICE 'Column onboarding_email_4_sent_at already exists';
    END IF;
    
    -- Email 5: Last Chance (7 days after signup)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_5_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_5_sent_at TIMESTAMP;
        RAISE NOTICE 'Added column: onboarding_email_5_sent_at';
    ELSE
        RAISE NOTICE 'Column onboarding_email_5_sent_at already exists';
    END IF;
END $$;

-- Create indexes for faster queries (only if they don't exist)
CREATE INDEX IF NOT EXISTS idx_users_email_1_sent ON users(onboarding_email_1_sent_at) WHERE onboarding_email_1_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_2_sent ON users(onboarding_email_2_sent_at) WHERE onboarding_email_2_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_3_sent ON users(onboarding_email_3_sent_at) WHERE onboarding_email_3_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_4_sent ON users(onboarding_email_4_sent_at) WHERE onboarding_email_4_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_5_sent ON users(onboarding_email_5_sent_at) WHERE onboarding_email_5_sent_at IS NOT NULL;

-- Verify the columns were added
SELECT 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns 
WHERE table_name = 'users' 
AND column_name LIKE 'onboarding_email%'
ORDER BY column_name;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed successfully!';
    RAISE NOTICE 'The onboarding email sequence system is now ready to use.';
END $$;

