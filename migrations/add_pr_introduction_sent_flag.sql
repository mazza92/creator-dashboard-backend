-- Add flag to track if PR Packages introduction email has been sent to existing creators
-- This prevents sending duplicate introduction emails

ALTER TABLE users 
ADD COLUMN IF NOT EXISTS pr_packages_intro_sent_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_users_pr_intro_sent ON users(pr_packages_intro_sent_at) WHERE pr_packages_intro_sent_at IS NULL;

