-- Creator onboarding personalization survey (segment, intent, pain)
ALTER TABLE creators
  ADD COLUMN IF NOT EXISTS onboarding_survey JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_creators_onboarding_survey_gin
  ON creators USING gin (onboarding_survey);

COMMENT ON COLUMN creators.onboarding_survey IS
  'Onboarding survey: segment, intent[], pain[], other text, skipped/completed timestamps';
