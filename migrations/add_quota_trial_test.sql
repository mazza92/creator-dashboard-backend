-- Free-limit email experiment: control ($19) vs 7-day trial vs no-email holdout.
-- Turn off with: UPDATE email_feature_flags SET enabled = false WHERE flag_name = 'quota_trial_test_v1';

INSERT INTO email_feature_flags (flag_name, enabled, description)
SELECT 'quota_trial_test_v1', true, 'max_quota_hit: 45% control, 45% 7-day trial, 10% holdout'
WHERE NOT EXISTS (SELECT 1 FROM email_feature_flags WHERE flag_name = 'quota_trial_test_v1');

CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_creator_campaign
    ON lifecycle_email_sends (creator_id, utm_campaign, sent_at DESC);
