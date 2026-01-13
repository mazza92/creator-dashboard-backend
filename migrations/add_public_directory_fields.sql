-- Migration: Add Public Directory Fields to pr_brands
-- Date: 2026-01-13
-- Purpose: Enable SEO-optimized public brand landing pages

-- Add new columns for public directory
ALTER TABLE pr_brands
ADD COLUMN IF NOT EXISTS slug VARCHAR(255) UNIQUE,
ADD COLUMN IF NOT EXISTS seo_title VARCHAR(255),
ADD COLUMN IF NOT EXISTS seo_description TEXT,
ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS application_method VARCHAR(50) DEFAULT 'DIRECT_LINK';

-- Create index on slug for fast lookups
CREATE INDEX IF NOT EXISTS idx_pr_brands_slug ON pr_brands(slug);

-- Create index on is_featured for homepage queries
CREATE INDEX IF NOT EXISTS idx_pr_brands_featured ON pr_brands(is_featured) WHERE is_featured = TRUE;

-- Generate slugs from existing brand names
UPDATE pr_brands
SET slug = LOWER(
    REGEXP_REPLACE(
        REGEXP_REPLACE(brand_name, '[^a-zA-Z0-9\s-]', '', 'g'),
        '\s+', '-', 'g'
    )
)
WHERE slug IS NULL;

-- Handle duplicate slugs by appending a number
WITH ranked_brands AS (
    SELECT
        id,
        slug,
        ROW_NUMBER() OVER (PARTITION BY slug ORDER BY id) as rn
    FROM pr_brands
    WHERE slug IS NOT NULL
)
UPDATE pr_brands
SET slug = ranked_brands.slug || '-' || ranked_brands.rn
FROM ranked_brands
WHERE pr_brands.id = ranked_brands.id
AND ranked_brands.rn > 1;

-- Generate default SEO titles from brand name
UPDATE pr_brands
SET seo_title = brand_name || ' PR Application | Contact ' || brand_name || ' for Brand Partnerships'
WHERE seo_title IS NULL;

-- Generate default SEO descriptions
UPDATE pr_brands
SET seo_description =
    'Apply to ' || brand_name || ' PR program. ' ||
    CASE
        WHEN min_followers > 0 THEN 'Minimum ' || min_followers || ' followers required. '
        ELSE 'Open to all creator sizes. '
    END ||
    'Find ' || brand_name || ' contact information, application requirements, and response rates on Newcollab.'
WHERE seo_description IS NULL;

-- Set application method based on existing data
UPDATE pr_brands
SET application_method = CASE
    WHEN application_form_url IS NOT NULL AND application_form_url != '' THEN 'DIRECT_LINK'
    WHEN contact_email IS NOT NULL AND contact_email != '' THEN 'EMAIL_PITCH'
    ELSE 'DIRECT_LINK'
END
WHERE application_method = 'DIRECT_LINK';

-- Mark popular brands as featured (example: brands with good response rates)
UPDATE pr_brands
SET is_featured = TRUE
WHERE response_rate >= 70
AND avg_response_time_days <= 7
LIMIT 20;

-- Add comment for documentation
COMMENT ON COLUMN pr_brands.slug IS 'URL-friendly identifier for public brand pages (e.g., rare-beauty)';
COMMENT ON COLUMN pr_brands.seo_title IS 'Custom title tag for Google search results';
COMMENT ON COLUMN pr_brands.seo_description IS 'Custom meta description for Google search results';
COMMENT ON COLUMN pr_brands.is_featured IS 'Featured brands appear at top of public directory';
COMMENT ON COLUMN pr_brands.application_method IS 'How creators apply: DIRECT_LINK or EMAIL_PITCH';
