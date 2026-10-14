-- Lifecycle Phase 1, Polly-first: Polly owns brand-level emails (unsent pitches,
-- check-ins, kit views, replies). Lifecycle keeps account-level ones: welcome,
-- Polly activation, quota -> Pro, weekly digest (free), one win-back per dormancy.

UPDATE lifecycle_email_templates
SET active = false, updated_at = NOW()
WHERE slug IN (
    'edu_5reasons', 'edu_60sec', 'edu_pitch', 'edu_followup', 'edu_edge',
    'doubter_sarah', 'doubter_5pitch', 'doubter_story',
    'max_3things', 'max_30day',
    'reengagement_soft',
    'first_move',
    'pitch_brand', 'brand_waiting', 'time_followup', 'did_reply',
    'celebration_reply', 'celebration_first_box'
);

UPDATE lifecycle_email_templates
SET trigger_conditions = '{"condition": "polly_never_opened", "days_after_signup": 4, "one_time": true}'::jsonb,
    required_user_state = NULL,
    subject_template = '{{ first_name }}, have you met Polly yet?',
    preheader_template = 'She finds the brands and writes the pitches. Your first 3 are free.',
    updated_at = NOW()
WHERE slug = 'meet_manager';

UPDATE lifecycle_email_templates
SET trigger_conditions = trigger_conditions || '{"one_time_per_dormancy": true}'::jsonb,
    subject_template = 'new brands that fit you, {{ first_name }}',
    preheader_template = 'Three picks for you, and Polly can write the pitches.',
    updated_at = NOW()
WHERE slug = 'reengagement_new';

UPDATE lifecycle_email_templates
SET excluded_tiers = ARRAY['pro', 'elite'],
    subject_template = 'your monday picks from Polly',
    preheader_template = 'This week''s brands, open campaigns, and your free pitches.',
    updated_at = NOW()
WHERE slug = 'weekly_digest';

UPDATE lifecycle_email_templates
SET preheader_template = 'Meet your matches. Polly writes the pitches, you hit send.',
    updated_at = NOW()
WHERE slug = 'welcome_manager';

CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_provider_msg
    ON lifecycle_email_sends (provider_message_id);
