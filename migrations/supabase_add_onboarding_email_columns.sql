-- =====================================================
-- Supabase SQL: Add Onboarding Email Sequence Columns
-- =====================================================
-- Run this in Supabase SQL Editor
-- Copy and paste the entire script into the SQL Editor
-- =====================================================

-- Step 1: Add columns for tracking each email in the sequence
DO $$ 
BEGIN
    -- Email 1: Welcome (5 minutes after signup)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='users' AND column_name='onboarding_email_1_sent_at'
    ) THEN
        ALTER TABLE users ADD COLUMN onboarding_email_1_sent_at TIMESTAMP;
        RAISE NOTICE '✅ Added column: onboarding_email_1_sent_at';
    ELSE
        RAISE NOTICE 'ℹ️ Column onboarding_email_1_sent_at already exists';
    END IF;
    
    -- Email 2: Value Focus (30-60 minutes after signup)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='users' AND column_name='onboarding_email_2_sent_at'
    ) THEN
        ALTER TABLE users ADD COLUMN onboarding_email_2_sent_at TIMESTAMP;
        RAISE NOTICE '✅ Added column: onboarding_email_2_sent_at';
    ELSE
        RAISE NOTICE 'ℹ️ Column onboarding_email_2_sent_at already exists';
    END IF;
    
    -- Email 3: Social Proof (24 hours after signup)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='users' AND column_name='onboarding_email_3_sent_at'
    ) THEN
        ALTER TABLE users ADD COLUMN onboarding_email_3_sent_at TIMESTAMP;
        RAISE NOTICE '✅ Added column: onboarding_email_3_sent_at';
    ELSE
        RAISE NOTICE 'ℹ️ Column onboarding_email_3_sent_at already exists';
    END IF;
    
    -- Email 4: Support (3 days after signup)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='users' AND column_name='onboarding_email_4_sent_at'
    ) THEN
        ALTER TABLE users ADD COLUMN onboarding_email_4_sent_at TIMESTAMP;
        RAISE NOTICE '✅ Added column: onboarding_email_4_sent_at';
    ELSE
        RAISE NOTICE 'ℹ️ Column onboarding_email_4_sent_at already exists';
    END IF;
    
    -- Email 5: Last Chance (7 days after signup)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='users' AND column_name='onboarding_email_5_sent_at'
    ) THEN
        ALTER TABLE users ADD COLUMN onboarding_email_5_sent_at TIMESTAMP;
        RAISE NOTICE '✅ Added column: onboarding_email_5_sent_at';
    ELSE
        RAISE NOTICE 'ℹ️ Column onboarding_email_5_sent_at already exists';
    END IF;
END $$;

-- Step 2: Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_users_email_1_sent 
ON users(onboarding_email_1_sent_at) 
WHERE onboarding_email_1_sent_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_email_2_sent 
ON users(onboarding_email_2_sent_at) 
WHERE onboarding_email_2_sent_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_email_3_sent 
ON users(onboarding_email_3_sent_at) 
WHERE onboarding_email_3_sent_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_email_4_sent 
ON users(onboarding_email_4_sent_at) 
WHERE onboarding_email_4_sent_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_email_5_sent 
ON users(onboarding_email_5_sent_at) 
WHERE onboarding_email_5_sent_at IS NOT NULL;

-- Step 3: Verify the columns were added successfully
SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'users' 
AND column_name LIKE 'onboarding_email%'
ORDER BY column_name;

-- =====================================================
-- Migration Complete!
-- =====================================================
-- Expected output: 5 rows showing all email columns
-- =====================================================

