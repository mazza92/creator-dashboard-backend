-- Link brand-submitted opportunities to Gifted PR brands + rosters.

ALTER TABLE opportunities
ADD COLUMN IF NOT EXISTS pr_brand_id INTEGER;

ALTER TABLE pr_brands
ADD COLUMN IF NOT EXISTS source_opportunity_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_opportunities_pr_brand_id
ON opportunities(pr_brand_id);

CREATE INDEX IF NOT EXISTS idx_pr_brands_source_opportunity
ON pr_brands(source_opportunity_id);

COMMENT ON COLUMN opportunities.pr_brand_id IS
  'When set, this brand submission is live as Gifted PR (creators apply in-app, not Open gigs).';
COMMENT ON COLUMN pr_brands.source_opportunity_id IS
  'Inbound brand submission that created or attached this Gifted PR brand.';
