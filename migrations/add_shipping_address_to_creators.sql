-- =====================================================
-- Add Shipping Address to Creators Table
-- =====================================================
-- This migration adds shipping address and size preferences
-- to the creators table for PR package collaborations
-- =====================================================

-- Add shipping_address column (JSONB to store structured address data)
ALTER TABLE creators 
ADD COLUMN IF NOT EXISTS shipping_address JSONB;

-- Add size preferences (for clothing/fashion brands)
ALTER TABLE creators 
ADD COLUMN IF NOT EXISTS size_preferences JSONB;

-- Add phone number for shipping (optional but useful)
ALTER TABLE creators 
ADD COLUMN IF NOT EXISTS shipping_phone VARCHAR(50);

-- Add notes for shipping (e.g., delivery instructions)
ALTER TABLE creators 
ADD COLUMN IF NOT EXISTS shipping_notes TEXT;

-- =====================================================
-- Migration Complete!
-- =====================================================
-- The shipping_address JSONB column will store:
-- {
--   "full_name": "John Doe",
--   "address_line1": "123 Main St",
--   "address_line2": "Apt 4B",
--   "city": "New York",
--   "state": "NY",
--   "zip": "10001",
--   "country": "United States"
-- }
--
-- The size_preferences JSONB column will store:
-- {
--   "clothing": {
--     "shirt": "M",
--     "pants": "32",
--     "shoes": "10"
--   },
--   "skincare": "sensitive",
--   "other": "notes"
-- }
-- =====================================================

