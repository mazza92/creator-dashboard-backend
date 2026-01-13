-- Migration: Add brand cover images
-- This adds a cover_image_url field to pr_brands for better visual display in discovery cards

ALTER TABLE pr_brands
ADD COLUMN IF NOT EXISTS cover_image_url VARCHAR(500);

-- Add comment to explain the difference
COMMENT ON COLUMN pr_brands.logo_url IS 'Brand logo (small, for icons and lists)';
COMMENT ON COLUMN pr_brands.cover_image_url IS 'Brand cover/hero image (large, for discovery cards)';

-- Note: After running this migration, you can populate cover images by:
-- 1. Manually adding high-quality brand images
-- 2. Using Instagram API to fetch brand's latest post image
-- 3. Using OpenGraph tags from brand's website
-- 4. Using placeholder service like Unsplash based on category
