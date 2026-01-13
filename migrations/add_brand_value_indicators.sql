-- Add value indicator columns to pr_brands table
-- These help creators understand what to expect from each brand

ALTER TABLE pr_brands
ADD COLUMN IF NOT EXISTS avg_product_value INTEGER DEFAULT 50,
ADD COLUMN IF NOT EXISTS collaboration_type VARCHAR(50) DEFAULT 'gifting',
ADD COLUMN IF NOT EXISTS payment_offered BOOLEAN DEFAULT false;

-- Add comments for clarity
COMMENT ON COLUMN pr_brands.avg_product_value IS 'Estimated average product/service value in USD';
COMMENT ON COLUMN pr_brands.collaboration_type IS 'Type of collaboration: gifting, paid, affiliate, etc.';
COMMENT ON COLUMN pr_brands.payment_offered IS 'Whether brand offers paid collaborations';

-- Create index for filtering by collaboration type
CREATE INDEX IF NOT EXISTS idx_pr_brands_collaboration_type ON pr_brands(collaboration_type);
CREATE INDEX IF NOT EXISTS idx_pr_brands_payment_offered ON pr_brands(payment_offered);
