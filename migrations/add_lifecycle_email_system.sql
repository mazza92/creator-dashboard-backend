-- ============================================
-- LIFECYCLE EMAIL SYSTEM MIGRATION
-- Supports 23-email strategy with state-based segmentation
-- ============================================

-- ============================================
-- 1. LIFECYCLE EMAIL TEMPLATES REGISTRY
-- ============================================
-- Defines all 23 emails with metadata for the engine
CREATE TABLE IF NOT EXISTS lifecycle_email_templates (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(50) UNIQUE NOT NULL,           -- e.g., 'welcome_manager', 'edu_5reasons'
    email_number INT NOT NULL,                   -- 1-23 per strategy doc
    name VARCHAR(100) NOT NULL,                  -- Human-readable name
    category VARCHAR(50) NOT NULL,               -- 'onboarding', 'education', 'behavioral', 'doubter', 'maximizer', 'reengagement', 'celebration', 'digest'
    subject_template TEXT NOT NULL,              -- Jinja2 subject with {{ variables }}
    preheader_template TEXT,                     -- Preheader text
    body_template_file VARCHAR(100) NOT NULL,    -- Template filename in /templates/lifecycle/

    -- Trigger conditions (stored as JSON for flexibility)
    trigger_type VARCHAR(50) NOT NULL,           -- 'immediate', 'day_based', 'state_based', 'action_based', 'weekly'
    trigger_conditions JSONB NOT NULL DEFAULT '{}',

    -- Throttling rules
    exempt_from_weekly_cap BOOLEAN DEFAULT false,
    exempt_from_daily_cap BOOLEAN DEFAULT false,
    priority INT DEFAULT 50,                     -- Higher = sent first when conflicts (1-100)

    -- State requirements
    required_user_state VARCHAR(50)[],           -- ['new', 'explorer', 'engaged', 'doubter', 'maximizer', 'winner', 'dormant']
    excluded_tiers VARCHAR(20)[],                -- e.g., ['pro', 'elite'] to skip Pro users

    -- Feature flag for rollback
    feature_flag VARCHAR(50),                    -- e.g., 'email_onboarding_v2'

    -- Metadata
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_templates_slug ON lifecycle_email_templates(slug);
CREATE INDEX IF NOT EXISTS idx_lifecycle_templates_category ON lifecycle_email_templates(category);
CREATE INDEX IF NOT EXISTS idx_lifecycle_templates_active ON lifecycle_email_templates(active);

-- ============================================
-- 2. LIFECYCLE EMAIL SENDS TRACKING
-- ============================================
-- Track every lifecycle email sent for deduplication and analytics
CREATE TABLE IF NOT EXISTS lifecycle_email_sends (
    id SERIAL PRIMARY KEY,
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    template_slug VARCHAR(50) NOT NULL,

    -- Context (for brand-specific emails)
    brand_id INT REFERENCES pr_brands(id) ON DELETE SET NULL,
    brand_slug VARCHAR(100),

    -- Send metadata
    sent_at TIMESTAMP DEFAULT NOW(),
    email_address VARCHAR(255) NOT NULL,
    subject_rendered TEXT,

    -- Delivery tracking
    provider_message_id TEXT,
    status VARCHAR(20) DEFAULT 'sent',          -- 'sent', 'failed', 'bounced'
    error_message TEXT,

    -- Engagement tracking
    opened_at TIMESTAMP,
    clicked_at TIMESTAMP,
    clicked_url TEXT,

    -- UTM tracking
    utm_campaign VARCHAR(100),

    -- Deduplication key (one-time sends)
    dedup_key VARCHAR(100),                     -- e.g., 'sarah_story_v1', 'first_pr_box'

    UNIQUE(creator_id, dedup_key)               -- Prevent duplicate one-time sends
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_creator ON lifecycle_email_sends(creator_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_template ON lifecycle_email_sends(template_slug);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_sent_at ON lifecycle_email_sends(sent_at);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_status ON lifecycle_email_sends(status);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_dedup ON lifecycle_email_sends(creator_id, dedup_key);

-- ============================================
-- 3. CREATOR LIFECYCLE STATE TRACKING
-- ============================================
-- Add columns to creators table for state determination
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS lifecycle_state VARCHAR(50) DEFAULT 'new',  -- 'new', 'explorer', 'engaged', 'doubter', 'maximizer', 'winner', 'dormant'
ADD COLUMN IF NOT EXISTS lifecycle_state_updated_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS first_reply_received_at TIMESTAMP,          -- For 'winner' state
ADD COLUMN IF NOT EXISTS first_pr_box_received_at TIMESTAMP,         -- For celebration email
ADD COLUMN IF NOT EXISTS total_pitches_sent INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_replies_received INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS education_series_position INT DEFAULT 0,    -- 0-6, which edu email they're on
ADD COLUMN IF NOT EXISTS last_education_email_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS last_weekly_digest_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS weekly_digest_week_number INT DEFAULT 0,    -- For theme rotation (1-12)
ADD COLUMN IF NOT EXISTS doubter_series_started_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS maximizer_series_started_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS reengagement_series_started_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_creators_lifecycle_state ON creators(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_creators_lifecycle_state_updated ON creators(lifecycle_state_updated_at);

-- ============================================
-- 4. EMAIL THROTTLING COUNTERS
-- ============================================
-- More granular tracking than existing fields
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS lifecycle_emails_sent_today INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS lifecycle_emails_sent_this_week INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS lifecycle_last_email_date DATE,
ADD COLUMN IF NOT EXISTS lifecycle_week_start_date DATE;

-- ============================================
-- 5. EMAIL FEATURE FLAGS
-- ============================================
-- For rollback capability per strategy section
CREATE TABLE IF NOT EXISTS email_feature_flags (
    id SERIAL PRIMARY KEY,
    flag_name VARCHAR(50) UNIQUE NOT NULL,
    enabled BOOLEAN DEFAULT false,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default flags (all disabled initially for safe rollout)
INSERT INTO email_feature_flags (flag_name, enabled, description) VALUES
    ('email_onboarding_v2', false, 'New onboarding sequence (Emails 2, 3, 4)'),
    ('email_education_series_v2', false, 'Creator PR Fundamentals series (Emails 5-9)'),
    ('email_behavioral_triggers_v2', false, 'Rewritten behavioral emails (Emails 10-13)'),
    ('email_doubter_series_v2', false, 'Doubter conversion series (Emails 14-15)'),
    ('email_maximizer_pro_v2', false, 'Maximizer Pro conversion (Emails 16-18)'),
    ('email_reengagement_v2', false, 'Re-engagement series (Emails 19-20)'),
    ('email_weekly_digest_v2', false, 'Weekly Manager Digest (Email 21)'),
    ('email_celebration_v2', false, 'Celebration triggers (Emails 22-23)')
ON CONFLICT (flag_name) DO NOTHING;

-- ============================================
-- 6. WEEKLY DIGEST THEMES
-- ============================================
-- Store rotating themes for the weekly digest
CREATE TABLE IF NOT EXISTS weekly_digest_themes (
    id SERIAL PRIMARY KEY,
    week_number INT UNIQUE NOT NULL,            -- 1-12, cycles
    theme_title VARCHAR(100) NOT NULL,
    theme_body TEXT NOT NULL,                   -- 2-3 sentence explanation
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Insert the 12 themes from strategy doc
INSERT INTO weekly_digest_themes (week_number, theme_title, theme_body) VALUES
    (1, 'Bio polish', 'Brands scan bios in 3 seconds. This week, audit yours. Remove fluff, add your niche, and make sure your contact info is visible.'),
    (2, 'Portfolio refresh', 'Add 2 posts to your portfolio that show product in real use. Brands want to see you demonstrate, not just display.'),
    (3, 'Follow-up rhythm', 'You have pitches due for follow-up. 67% of replies come after a follow-up. Do them today.'),
    (4, 'New pitch angle', 'Try leading with a specific product name in your subject line. Generic subjects get archived.'),
    (5, 'Content pattern', 'Post one "for the brand" style Reel this week. Give potential partners something fresh to see.'),
    (6, 'Portfolio recency', 'Refresh anything older than 30 days on your public kit. Brands check recency before replying.'),
    (7, 'Rate card visibility', 'Add your gifting plus paid rates to your kit. Brands who know your rates respond faster.'),
    (8, 'Whitelisting mention', 'Add "open to whitelisting" to your bio. 60% of brands running ads prioritize creators who offer this.'),
    (9, 'Trending sound match', 'Use one trending sound in this weeks content for reach. More eyes means more brand interest.'),
    (10, 'First-touch personalization', 'Every pitch this week: mention 1 real product they sell. Specific beats generic every time.'),
    (11, 'Application forms', 'Some brands opened application forms this week. Apply to at least one. Forms have higher acceptance rates.'),
    (12, 'Testimonial add', 'Add a 1-line quote from any brand youve worked with. Social proof lifts reply rates.')
ON CONFLICT (week_number) DO NOTHING;

-- ============================================
-- 7. SEED THE 23 EMAIL TEMPLATES
-- ============================================
INSERT INTO lifecycle_email_templates (
    slug, email_number, name, category, subject_template, preheader_template,
    body_template_file, trigger_type, trigger_conditions, exempt_from_weekly_cap,
    exempt_from_daily_cap, priority, required_user_state, excluded_tiers, feature_flag
) VALUES
    -- ONBOARDING (Emails 1-4)
    ('email_verification', 1, 'Email Verification', 'onboarding',
     'Verify your Newcollab account', 'One click to activate your account and meet your manager.',
     'lifecycle/01_verification.html', 'immediate', '{}', true, true, 100, NULL, NULL, NULL),

    ('welcome_manager', 2, 'Welcome from your manager', 'onboarding',
     'hi from your manager, {{ first_name }}', 'Let''s get you your first PR reply.',
     'lifecycle/02_welcome_manager.html', 'immediate', '{"after": "email_verified"}', true, true, 99, NULL, NULL, 'email_onboarding_v2'),

    ('first_move', 3, 'Your first move', 'onboarding',
     '{{ first_name }} — your first move', 'The one thing most creators skip.',
     'lifecycle/03_first_move.html', 'day_based', '{"days_after_signup": 1, "condition": "no_unlocks"}', false, false, 90, ARRAY['new'], NULL, 'email_onboarding_v2'),

    ('meet_manager', 4, 'Meet the manager', 'onboarding',
     '{{ first_name }}, i think i can help', 'A quick note from your manager.',
     'lifecycle/04_meet_manager.html', 'day_based', '{"days_after_signup": 3, "condition": "incomplete_profile"}', false, false, 85, ARRAY['new'], NULL, 'email_onboarding_v2'),

    -- EDUCATION SERIES (Emails 5-9)
    ('edu_5reasons', 5, 'Why brands say no', 'education',
     'the 5 real reasons brands don''t reply', 'It''s rarely about your follower count.',
     'lifecycle/05_edu_5reasons.html', 'day_based', '{"days_after_signup": 5, "condition": "has_activity"}', false, false, 60, ARRAY['explorer', 'engaged'], ARRAY['pro', 'elite'], 'email_education_series_v2'),

    ('edu_60sec', 6, 'The 60-second brand audit', 'education',
     'the 60-second brand audit', 'How PR coordinators actually decide.',
     'lifecycle/06_edu_60sec.html', 'day_based', '{"days_after_signup": 8, "requires_previous": "edu_5reasons"}', false, false, 60, ARRAY['explorer', 'engaged'], ARRAY['pro', 'elite'], 'email_education_series_v2'),

    ('edu_pitch', 7, 'The pitch that gets replied to', 'education',
     'the pitch that gets replied to', 'Anatomy of a working cold pitch.',
     'lifecycle/07_edu_pitch.html', 'day_based', '{"days_after_signup": 12, "requires_previous": "edu_60sec"}', false, false, 60, ARRAY['explorer', 'engaged'], ARRAY['pro', 'elite'], 'email_education_series_v2'),

    ('edu_followup', 8, 'How follow-ups actually work', 'education',
     'follow-ups are where deals actually happen', 'The 3-touch cadence that lifts reply rate 4x.',
     'lifecycle/08_edu_followup.html', 'day_based', '{"days_after_signup": 16, "requires_previous": "edu_pitch"}', false, false, 60, ARRAY['explorer', 'engaged'], ARRAY['pro', 'elite'], 'email_education_series_v2'),

    ('edu_edge', 9, 'Your micro-creator edge', 'education',
     'your micro-creator edge (macros don''t have this)', 'Why small creators land deals macros can''t.',
     'lifecycle/09_edu_edge.html', 'day_based', '{"days_after_signup": 20, "requires_previous": "edu_followup"}', false, false, 60, ARRAY['explorer', 'engaged'], ARRAY['pro', 'elite'], 'email_education_series_v2'),

    -- BEHAVIORAL TRIGGERS (Emails 10-13)
    ('pitch_brand', 10, 'Time to pitch brand', 'behavioral',
     'ready to pitch {{ brand_name }}?', 'Two days ago you saved them. Now''s the moment.',
     'lifecycle/10_pitch_brand.html', 'action_based', '{"days_after_save": 2, "condition": "brand_not_pitched"}', false, false, 70, NULL, NULL, 'email_behavioral_triggers_v2'),

    ('brand_waiting', 11, 'Brand is still waiting', 'behavioral',
     '{{ brand_name }} is still on your list', 'Quick honest note about timing.',
     'lifecycle/11_brand_waiting.html', 'action_based', '{"days_after_save": 6, "condition": "brand_not_pitched"}', false, false, 70, NULL, NULL, 'email_behavioral_triggers_v2'),

    ('did_reply', 12, 'Did brand reply?', 'behavioral',
     'did {{ brand_name }} reply?', 'Two weeks in. Quick check-in.',
     'lifecycle/12_did_reply.html', 'action_based', '{"days_after_pitch": 14, "condition": "no_reply_logged"}', false, false, 70, NULL, NULL, 'email_behavioral_triggers_v2'),

    ('time_followup', 13, 'Time to follow up', 'behavioral',
     'time to follow up with {{ brand_name }}', '67% of replies come after a follow-up.',
     'lifecycle/13_time_followup.html', 'action_based', '{"days_after_pitch": 7, "condition": "no_followup_sent"}', false, false, 70, NULL, NULL, 'email_behavioral_triggers_v2'),

    -- DOUBTER SERIES (Emails 14-15)
    ('doubter_sarah', 14, 'Sarah''s Story', 'doubter',
     'a story you''ll want to read', 'Sarah has 340 followers.',
     'lifecycle/14_doubter_sarah.html', 'state_based', '{"state": "doubter", "one_time": true}', false, false, 50, ARRAY['doubter'], ARRAY['pro', 'elite'], 'email_doubter_series_v2'),

    ('doubter_5pitch', 15, 'The 5-pitch rule', 'doubter',
     'about the 5-pitch rule', 'Honest data on how long this actually takes.',
     'lifecycle/15_doubter_5pitch.html', 'state_based', '{"days_after_previous": 4, "requires_previous": "doubter_sarah"}', false, false, 50, ARRAY['doubter'], ARRAY['pro', 'elite'], 'email_doubter_series_v2'),

    -- MAXIMIZER PRO CONVERSION (Emails 16-18)
    ('max_quota_hit', 16, 'Quota resets in N days', 'maximizer',
     '{{ first_name }}, you''re maxed out for {{ month }}', 'Here''s what to do until your quota resets.',
     'lifecycle/16_max_quota_hit.html', 'action_based', '{"trigger": "quota_3_3", "one_time_per_month": true}', false, false, 75, ARRAY['maximizer'], ARRAY['pro', 'elite'], 'email_maximizer_pro_v2'),

    ('max_3things', 17, 'What Pro creators do differently', 'maximizer',
     'the 3 things pro creators do that free creators don''t', 'And why it changes their reply rate.',
     'lifecycle/17_max_3things.html', 'state_based', '{"days_after_previous": 2, "requires_previous": "max_quota_hit"}', false, false, 75, ARRAY['maximizer'], ARRAY['pro', 'elite'], 'email_maximizer_pro_v2'),

    ('max_30day', 18, 'Your manager''s Pro plan', 'maximizer',
     'here''s your personalized pro plan', 'What I''d do with you in your first 30 days of Pro.',
     'lifecycle/18_max_30day.html', 'state_based', '{"days_after_previous": 5, "requires_previous": "max_3things"}', false, false, 75, ARRAY['maximizer'], ARRAY['pro', 'elite'], 'email_maximizer_pro_v2'),

    -- RE-ENGAGEMENT (Emails 19-20)
    ('reengagement_new', 19, 'Brands you missed this week', 'reengagement',
     '{{ N_new_brands }} new matches while you were away', 'Quick update on what''s new in your niche.',
     'lifecycle/19_reengagement_new.html', 'state_based', '{"state": "dormant", "days_dormant": 14}', false, false, 40, ARRAY['dormant'], NULL, 'email_reengagement_v2'),

    ('reengagement_soft', 20, 'Your manager wants you back', 'reengagement',
     'hey — everything ok?', 'Your manager noticed you''ve been away.',
     'lifecycle/20_reengagement_soft.html', 'state_based', '{"state": "dormant", "days_dormant": 21, "one_time_per_dormancy": true}', false, false, 40, ARRAY['dormant'], NULL, 'email_reengagement_v2'),

    -- WEEKLY DIGEST (Email 21)
    ('weekly_digest', 21, 'Weekly Manager Digest', 'digest',
     'your monday brief from your manager', 'Your progress + this week''s focus.',
     'lifecycle/21_weekly_digest.html', 'weekly', '{"day": "monday", "hour": 8}', false, false, 55, NULL, NULL, 'email_weekly_digest_v2'),

    -- CELEBRATION (Emails 22-23)
    ('celebration_reply', 22, 'Reply received', 'celebration',
     '{{ brand_name }} replied. here''s what to do next.', 'The next 24 hours matter more than the pitch did.',
     'lifecycle/22_celebration_reply.html', 'action_based', '{"trigger": "reply_marked", "realtime": true}', true, true, 95, ARRAY['winner'], NULL, 'email_celebration_v2'),

    ('celebration_first_box', 23, 'First PR box received', 'celebration',
     'your first pr box. congrats.', 'Real talk about what happens next.',
     'lifecycle/23_celebration_first_box.html', 'action_based', '{"trigger": "first_pr_box", "one_time": true}', true, true, 95, ARRAY['winner'], NULL, 'email_celebration_v2')
ON CONFLICT (slug) DO UPDATE SET
    subject_template = EXCLUDED.subject_template,
    preheader_template = EXCLUDED.preheader_template,
    trigger_conditions = EXCLUDED.trigger_conditions,
    updated_at = NOW();

-- ============================================
-- 8. EMAIL PREFERENCES TABLE
-- ============================================
-- Allow users to manage email preferences
CREATE TABLE IF NOT EXISTS email_preferences (
    id SERIAL PRIMARY KEY,
    creator_id INT UNIQUE NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    -- Category preferences (default all true)
    receive_onboarding BOOLEAN DEFAULT true,
    receive_education BOOLEAN DEFAULT true,
    receive_behavioral BOOLEAN DEFAULT true,
    receive_weekly_digest BOOLEAN DEFAULT true,
    receive_celebration BOOLEAN DEFAULT true,
    receive_reengagement BOOLEAN DEFAULT true,
    receive_pro_nudges BOOLEAN DEFAULT true,

    -- Global unsubscribe
    unsubscribed_all BOOLEAN DEFAULT false,
    unsubscribed_at TIMESTAMP,

    -- Timezone for send timing
    timezone VARCHAR(50) DEFAULT 'America/New_York',

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_preferences_creator ON email_preferences(creator_id);
CREATE INDEX IF NOT EXISTS idx_email_preferences_unsubscribed ON email_preferences(unsubscribed_all);

-- ============================================
-- 9. UPDATE FUNCTION FOR LIFECYCLE STATE
-- ============================================
-- Function to determine and update creator lifecycle state
CREATE OR REPLACE FUNCTION update_creator_lifecycle_state(p_creator_id INT)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_state VARCHAR(50);
    v_days_since_signup INT;
    v_days_since_login INT;
    v_unlocks_used INT;
    v_replies_received INT;
    v_has_pr_box BOOLEAN;
    v_subscription_tier VARCHAR(50);
BEGIN
    -- Get creator stats
    SELECT
        EXTRACT(DAY FROM NOW() - c.created_at)::INT,
        EXTRACT(DAY FROM NOW() - COALESCE(c.last_login, c.created_at))::INT,
        COALESCE(c.contact_unlocks_this_month, 0),
        COALESCE(c.total_replies_received, 0),
        c.first_pr_box_received_at IS NOT NULL,
        COALESCE(c.subscription_tier, 'free')
    INTO v_days_since_signup, v_days_since_login, v_unlocks_used, v_replies_received, v_has_pr_box, v_subscription_tier
    FROM creators c
    WHERE c.id = p_creator_id;

    -- Determine state (priority order)
    IF v_has_pr_box OR v_replies_received > 0 THEN
        v_state := 'winner';
    ELSIF v_days_since_login >= 14 THEN
        v_state := 'dormant';
    ELSIF v_unlocks_used >= 3 AND v_subscription_tier = 'free' THEN
        v_state := 'maximizer';
    ELSIF v_unlocks_used >= 2 AND v_days_since_signup >= 14 AND v_replies_received = 0 THEN
        v_state := 'doubter';
    ELSIF v_unlocks_used >= 2 THEN
        v_state := 'engaged';
    ELSIF v_unlocks_used >= 1 THEN
        v_state := 'explorer';
    ELSIF v_days_since_signup <= 3 THEN
        v_state := 'new';
    ELSE
        v_state := 'explorer';
    END IF;

    -- Update creator
    UPDATE creators
    SET lifecycle_state = v_state,
        lifecycle_state_updated_at = NOW()
    WHERE id = p_creator_id
    AND (lifecycle_state IS DISTINCT FROM v_state);

    RETURN v_state;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- MIGRATION COMPLETE
-- ============================================
