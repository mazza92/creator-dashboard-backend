-- Migration: Add stripe_customer_id column to creators table
-- This column stores the Stripe Customer ID for subscription management

ALTER TABLE creators
ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255);

-- Add index for faster lookups
CREATE INDEX IF NOT EXISTS idx_creators_stripe_customer_id ON creators(stripe_customer_id);

-- Add comment
COMMENT ON COLUMN creators.stripe_customer_id IS 'Stripe Customer ID for subscription billing';
