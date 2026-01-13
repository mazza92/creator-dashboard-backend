-- Brand Scraper Database Schema
-- This schema extends the existing brands table with scraping metadata

-- Add scraping metadata columns to brands table
ALTER TABLE brands ADD COLUMN IF NOT EXISTS data_source VARCHAR(50); -- 'instagram', 'linkedin', 'manual', etc.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS last_verified TIMESTAMP;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending'; -- 'pending', 'verified', 'failed'
ALTER TABLE brands ADD COLUMN IF NOT EXISTS scrape_metadata JSONB; -- Store scraping details
ALTER TABLE brands ADD COLUMN IF NOT EXISTS social_handles JSONB; -- Instagram, Twitter, TikTok handles
ALTER TABLE brands ADD COLUMN IF NOT EXISTS company_size VARCHAR(50);
ALTER TABLE brands ADD COLUMN IF NOT EXISTS industry VARCHAR(100);
ALTER TABLE brands ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS avg_product_value INTEGER;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS collaboration_type VARCHAR(50); -- 'paid', 'gifting', 'affiliate', 'both'
ALTER TABLE brands ADD COLUMN IF NOT EXISTS payment_offered BOOLEAN DEFAULT false;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT false;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS contact_quality_score INTEGER DEFAULT 0; -- 0-100 score

-- Create scraping queue table
CREATE TABLE IF NOT EXISTS brand_scraping_queue (
    id SERIAL PRIMARY KEY,
    brand_name VARCHAR(255) NOT NULL,
    instagram_handle VARCHAR(255),
    website VARCHAR(500),
    priority INTEGER DEFAULT 50, -- 0-100, higher = more priority
    status VARCHAR(20) DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    attempts INTEGER DEFAULT 0,
    last_attempt TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Create email verification log
CREATE TABLE IF NOT EXISTS email_verifications (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER REFERENCES brands(id),
    email VARCHAR(255) NOT NULL,
    verification_method VARCHAR(50), -- 'smtp', 'api', 'manual'
    is_valid BOOLEAN,
    is_disposable BOOLEAN,
    is_role_based BOOLEAN, -- admin@, info@, etc.
    mx_records_valid BOOLEAN,
    verification_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verification_details JSONB
);

-- Create scraping logs table
CREATE TABLE IF NOT EXISTS scraping_logs (
    id SERIAL PRIMARY KEY,
    scrape_type VARCHAR(50), -- 'instagram', 'linkedin', 'email_finder'
    brands_processed INTEGER DEFAULT 0,
    brands_successful INTEGER DEFAULT 0,
    brands_failed INTEGER DEFAULT 0,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error_count INTEGER DEFAULT 0,
    log_details JSONB
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_brands_verification_status ON brands(verification_status);
CREATE INDEX IF NOT EXISTS idx_brands_data_source ON brands(data_source);
CREATE INDEX IF NOT EXISTS idx_brands_email_verified ON brands(email_verified);
CREATE INDEX IF NOT EXISTS idx_scraping_queue_status ON brand_scraping_queue(status);
CREATE INDEX IF NOT EXISTS idx_scraping_queue_priority ON brand_scraping_queue(priority DESC);
