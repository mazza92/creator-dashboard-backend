-- Migration: Add onboarding email sequence tracking columns
-- Run this migration to add columns for tracking which onboarding emails have been sent

-- Add columns for tracking each email in the sequence (only if they don't exist)
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_1_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_1_sent_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_2_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_2_sent_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_3_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_3_sent_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_4_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_4_sent_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='onboarding_email_5_sent_at') THEN
        ALTER TABLE users ADD COLUMN onboarding_email_5_sent_at TIMESTAMP;
    END IF;
END $$;

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_users_email_1_sent ON users(onboarding_email_1_sent_at) WHERE onboarding_email_1_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_2_sent ON users(onboarding_email_2_sent_at) WHERE onboarding_email_2_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_3_sent ON users(onboarding_email_3_sent_at) WHERE onboarding_email_3_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_4_sent ON users(onboarding_email_4_sent_at) WHERE onboarding_email_4_sent_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email_5_sent ON users(onboarding_email_5_sent_at) WHERE onboarding_email_5_sent_at IS NOT NULL;

-- Note: The columns will be automatically created by the application code if they don't exist,
-- but running this migration ensures they're created upfront and indexed for performance.

