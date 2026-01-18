-- Migration: Add application form fields to brand_candidates
-- Purpose: Store PR application form URLs found by Form Scout module
-- Created: 2026-01-14

-- Add application form fields to brand_candidates
ALTER TABLE brand_candidates
ADD COLUMN IF NOT EXISTS application_url VARCHAR(500),
ADD COLUMN IF NOT EXISTS application_method VARCHAR(20) DEFAULT 'EMAIL_ONLY',
ADD COLUMN IF NOT EXISTS form_platform VARCHAR(50);

-- Add index for application_method
CREATE INDEX IF NOT EXISTS idx_brand_candidates_application_method
ON brand_candidates(application_method);

-- Comments for documentation
COMMENT ON COLUMN brand_candidates.application_url IS 'URL to PR/Ambassador application form (Typeform, Google Forms, etc.)';
COMMENT ON COLUMN brand_candidates.application_method IS 'DIRECT_FORM (has form), EMAIL_ONLY (email required), PLATFORM (Grin, Dovetale, etc.)';
COMMENT ON COLUMN brand_candidates.form_platform IS 'Platform hosting the form: Typeform, Google Forms, GRIN, Dovetale, etc.';

-- Also add to brands table for approved candidates
ALTER TABLE brands
ADD COLUMN IF NOT EXISTS application_url VARCHAR(500),
ADD COLUMN IF NOT EXISTS application_method VARCHAR(20) DEFAULT 'EMAIL_ONLY',
ADD COLUMN IF NOT EXISTS form_platform VARCHAR(50);

-- Add index for application_method
CREATE INDEX IF NOT EXISTS idx_brands_application_method
ON brands(application_method);

-- Comments for documentation
COMMENT ON COLUMN brands.application_url IS 'URL to PR/Ambassador application form (Typeform, Google Forms, etc.)';
COMMENT ON COLUMN brands.application_method IS 'DIRECT_FORM (has form), EMAIL_ONLY (email required), PLATFORM (Grin, Dovetale, etc.)';
COMMENT ON COLUMN brands.form_platform IS 'Platform hosting the form: Typeform, Google Forms, GRIN, Dovetale, etc.';
