-- SQL Migration: Update existing ad slots with currency based on creator's country
-- This script updates sponsor_drafts.currency based on the creator's country from users table
-- Run this AFTER running add_currency_to_sponsor_drafts.sql

-- Update ad slots for creators in United States
UPDATE sponsor_drafts sd
SET currency = 'USD'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('united states', 'usa', 'united states of america', 'us');

-- Update ad slots for creators in Canada
UPDATE sponsor_drafts sd
SET currency = 'CAD'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) = 'canada';

-- Update ad slots for creators in United Kingdom
UPDATE sponsor_drafts sd
SET currency = 'GBP'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('united kingdom', 'uk', 'britain', 'great britain');

-- Update ad slots for creators in Switzerland
UPDATE sponsor_drafts sd
SET currency = 'CHF'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('switzerland', 'schweiz', 'suisse');

-- Update ad slots for creators in Sweden
UPDATE sponsor_drafts sd
SET currency = 'SEK'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('sweden', 'sverige');

-- Update ad slots for creators in Norway
UPDATE sponsor_drafts sd
SET currency = 'NOK'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('norway', 'norge');

-- Update ad slots for creators in Denmark
UPDATE sponsor_drafts sd
SET currency = 'DKK'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('denmark', 'danmark');

-- Update ad slots for creators in Australia
UPDATE sponsor_drafts sd
SET currency = 'AUD'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) = 'australia';

-- Update ad slots for creators in Japan
UPDATE sponsor_drafts sd
SET currency = 'JPY'
FROM creators c
JOIN users u ON c.user_id = u.id
WHERE sd.creator_id = c.id
  AND (sd.currency IS NULL OR sd.currency = 'EUR')
  AND LOWER(TRIM(u.country)) IN ('japan', '日本');

-- Note: All other countries will remain as EUR (default)
-- European countries (France, Germany, Spain, Italy, etc.) will keep EUR

-- Verify the results
SELECT 
    currency,
    COUNT(*) as count
FROM sponsor_drafts
GROUP BY currency
ORDER BY count DESC;

