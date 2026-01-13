-- Add niche and avg_engagement_rate fields to creators table for marketplace
-- These are critical for the new "Creator-First" marketplace gallery

ALTER TABLE creators 
ADD COLUMN IF NOT EXISTS niche VARCHAR(100),
ADD COLUMN IF NOT EXISTS avg_engagement_rate DECIMAL(5,2);

-- Create index for niche filtering
CREATE INDEX IF NOT EXISTS idx_creators_niche ON creators(niche) WHERE niche IS NOT NULL;

-- Create index for engagement rate sorting
CREATE INDEX IF NOT EXISTS idx_creators_engagement_rate ON creators(avg_engagement_rate) WHERE avg_engagement_rate IS NOT NULL;

-- Add comment for documentation
COMMENT ON COLUMN creators.niche IS 'Creator niche/category (e.g., Skincare, Tech, Fashion) for marketplace filtering';
COMMENT ON COLUMN creators.avg_engagement_rate IS 'Average engagement rate percentage (e.g., 7.5) for marketplace sorting';

