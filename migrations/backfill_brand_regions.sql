-- Migration: Backfill regions data for pr_brands
-- Purpose: Enable country-specific brand filtering for US, AU, CA market targeting
-- Per geoshiftstrategy.md: Grow US, Australia, Canada markets

-- Step 1: Set default regions to ['US', 'Worldwide'] for brands with no regions set
-- Most brands in the directory ship to US
UPDATE pr_brands
SET regions = '["US", "Worldwide"]'::jsonb
WHERE regions IS NULL OR regions = '[]'::jsonb OR regions = 'null'::jsonb;

-- Step 2: Add 'Australia' to brands with .au domains
UPDATE pr_brands
SET regions = regions || '["Australia"]'::jsonb
WHERE website LIKE '%.au' OR website LIKE '%.au/%' OR website LIKE '%.com.au%'
  AND NOT (regions ? 'Australia');

-- Step 3: Add 'Canada' to brands with .ca domains
UPDATE pr_brands
SET regions = regions || '["Canada"]'::jsonb
WHERE website LIKE '%.ca' OR website LIKE '%.ca/%'
  AND NOT (regions ? 'Canada');

-- Step 4: Add 'UK' to brands with .uk or .co.uk domains
UPDATE pr_brands
SET regions = regions || '["UK"]'::jsonb
WHERE (website LIKE '%.uk' OR website LIKE '%.uk/%' OR website LIKE '%.co.uk%')
  AND NOT (regions ? 'UK');

-- Step 5: Add 'US' to known US beauty brands by name pattern
-- These are confirmed US-based brands
UPDATE pr_brands
SET regions = regions || '["US"]'::jsonb
WHERE (
    LOWER(brand_name) LIKE '%rhode%' OR
    LOWER(brand_name) LIKE '%rare beauty%' OR
    LOWER(brand_name) LIKE '%e.l.f%' OR
    LOWER(brand_name) LIKE '%elf cosmetics%' OR
    LOWER(brand_name) LIKE '%glossier%' OR
    LOWER(brand_name) LIKE '%fenty%' OR
    LOWER(brand_name) LIKE '%milk makeup%' OR
    LOWER(brand_name) LIKE '%colourpop%' OR
    LOWER(brand_name) LIKE '%morphe%' OR
    LOWER(brand_name) LIKE '%tarte%' OR
    LOWER(brand_name) LIKE '%too faced%' OR
    LOWER(brand_name) LIKE '%urban decay%' OR
    LOWER(brand_name) LIKE '%nyx%' OR
    LOWER(brand_name) LIKE '%revlon%' OR
    LOWER(brand_name) LIKE '%maybelline%' OR
    LOWER(brand_name) LIKE '%covergirl%'
)
AND NOT (regions ? 'US');

-- Step 6: Add 'Australia' to known Australian brands
UPDATE pr_brands
SET regions = regions || '["Australia"]'::jsonb
WHERE (
    LOWER(brand_name) LIKE '%frank body%' OR
    LOWER(brand_name) LIKE '%bondi sands%' OR
    LOWER(brand_name) LIKE '%mecca%' OR
    LOWER(brand_name) LIKE '%aesop%' OR
    LOWER(brand_name) LIKE '%sand & sky%' OR
    LOWER(brand_name) LIKE '%go-to%' OR
    LOWER(brand_name) LIKE '%sukin%'
)
AND NOT (regions ? 'Australia');

-- Step 7: Add 'Canada' to known Canadian brands
UPDATE pr_brands
SET regions = regions || '["Canada"]'::jsonb
WHERE (
    LOWER(brand_name) LIKE '%nudestix%' OR
    LOWER(brand_name) LIKE '%deciem%' OR
    LOWER(brand_name) LIKE '%the ordinary%' OR
    LOWER(brand_name) LIKE '%mac%' OR
    LOWER(brand_name) LIKE '%lise watier%'
)
AND NOT (regions ? 'Canada');

-- Deduplicate region arrays (remove duplicates)
UPDATE pr_brands
SET regions = (
    SELECT jsonb_agg(DISTINCT value)
    FROM jsonb_array_elements_text(regions)
)
WHERE jsonb_array_length(regions) > 0;

-- Verify the migration - show region distribution
SELECT
    'US' as region, COUNT(*) as count
FROM pr_brands WHERE regions ? 'US'
UNION ALL
SELECT 'Australia', COUNT(*) FROM pr_brands WHERE regions ? 'Australia'
UNION ALL
SELECT 'Canada', COUNT(*) FROM pr_brands WHERE regions ? 'Canada'
UNION ALL
SELECT 'UK', COUNT(*) FROM pr_brands WHERE regions ? 'UK'
UNION ALL
SELECT 'Worldwide', COUNT(*) FROM pr_brands WHERE regions ? 'Worldwide'
UNION ALL
SELECT 'Total brands', COUNT(*) FROM pr_brands
ORDER BY count DESC;
