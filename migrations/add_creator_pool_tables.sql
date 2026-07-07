-- Migration: Creator Pool Tables
-- Adds pool_supports and pool_credits tables for the "give to get" follow exchange feature

-- Pool Supports: tracks who supported (followed) whom
CREATE TABLE IF NOT EXISTS pool_supports (
    id SERIAL PRIMARY KEY,
    supporter_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL DEFAULT 'instagram',
    created_at TIMESTAMP DEFAULT NOW(),
    confirmed_at TIMESTAMP,
    -- Prevent duplicate supports (same supporter following same target on same platform)
    CONSTRAINT unique_pool_support UNIQUE (supporter_id, target_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_pool_supports_supporter ON pool_supports(supporter_id);
CREATE INDEX IF NOT EXISTS idx_pool_supports_target ON pool_supports(target_id);
CREATE INDEX IF NOT EXISTS idx_pool_supports_confirmed ON pool_supports(confirmed_at);

-- Pool Credits: tracks each creator's credit balance for visibility in the pool
CREATE TABLE IF NOT EXISTS pool_credits (
    creator_id INTEGER PRIMARY KEY REFERENCES creators(id) ON DELETE CASCADE,
    balance INTEGER DEFAULT 0,
    lifetime_earned INTEGER DEFAULT 0,
    lifetime_spent INTEGER DEFAULT 0,
    week_start DATE,
    last_support_at TIMESTAMP,
    streak_days INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pool_credits_balance ON pool_credits(balance);
CREATE INDEX IF NOT EXISTS idx_pool_credits_last_support ON pool_credits(last_support_at);

-- Add pool_last_visit to creators table for badge calculation
ALTER TABLE creators ADD COLUMN IF NOT EXISTS pool_last_visit TIMESTAMP;

-- Function to update pool_credits.updated_at automatically
CREATE OR REPLACE FUNCTION update_pool_credits_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for auto-updating timestamp
DROP TRIGGER IF EXISTS pool_credits_updated_at ON pool_credits;
CREATE TRIGGER pool_credits_updated_at
    BEFORE UPDATE ON pool_credits
    FOR EACH ROW
    EXECUTE FUNCTION update_pool_credits_timestamp();
