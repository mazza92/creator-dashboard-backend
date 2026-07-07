-- Email Quality Tracking for pr_brands
-- Track email verification status, bounce history, and quality scores

-- Add email quality columns to pr_brands
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_status VARCHAR(50) DEFAULT 'unverified';
-- Statuses: 'unverified', 'valid', 'invalid', 'catch-all', 'bounced', 'risky'

ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP;
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_quality_score INT DEFAULT 0;
-- Score: 0-100 (100 = verified valid, 50 = catch-all, 0 = invalid/bounced)

ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_bounce_count INT DEFAULT 0;
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_last_bounced_at TIMESTAMP;
ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS email_verification_source VARCHAR(50);
-- Source: 'neverbounce', 'hunter', 'manual', 'smtp_check'

-- Index for filtering by email quality
CREATE INDEX IF NOT EXISTS idx_pr_brands_email_status ON pr_brands(email_status);
CREATE INDEX IF NOT EXISTS idx_pr_brands_email_quality ON pr_brands(email_quality_score);

-- Add comment
COMMENT ON COLUMN pr_brands.email_status IS 'Email verification status: unverified, valid, invalid, catch-all, bounced, risky';
COMMENT ON COLUMN pr_brands.email_quality_score IS 'Email quality score 0-100. 100=verified, 50=catch-all, 0=invalid';
