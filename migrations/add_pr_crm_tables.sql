-- PR CRM Database Schema
-- Migration: Add PR brands directory and creator pipeline tables

-- ============================================
-- 1. PR BRANDS DIRECTORY TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS pr_brands (
    id SERIAL PRIMARY KEY,

    -- Basic Info
    brand_name VARCHAR(255) NOT NULL,
    website VARCHAR(500),
    logo_url VARCHAR(500),
    description TEXT,

    -- Contact Information
    contact_email VARCHAR(255),
    application_form_url VARCHAR(500),
    instagram_handle VARCHAR(100),
    tiktok_handle VARCHAR(100),
    youtube_handle VARCHAR(100),

    -- Categorization
    category VARCHAR(100), -- beauty, fashion, tech, gaming, food, lifestyle, etc.
    niches JSONB, -- ["skincare", "makeup", "haircare"]
    product_types JSONB, -- ["serum", "moisturizer", "cleanser"]

    -- Requirements
    min_followers INT DEFAULT 0,
    max_followers INT,
    platforms JSONB, -- ["instagram", "tiktok", "youtube"]
    regions JSONB, -- ["Australia", "US", "UK", "Canada", "Global"]

    -- Metadata
    has_application_form BOOLEAN DEFAULT false,
    response_rate INT, -- Percentage (0-100)
    avg_response_time_days INT, -- Average days to respond
    total_applications INT DEFAULT 0,
    total_responses INT DEFAULT 0,
    last_verified_at TIMESTAMP,

    -- Premium Access
    is_premium BOOLEAN DEFAULT false, -- Premium tier only

    -- Notes
    application_requirements TEXT, -- "Media kit required, min 5% engagement"
    notes TEXT, -- Internal notes
    success_stories TEXT, -- "Sarah got PR in 3 days"

    -- Source tracking
    source_url VARCHAR(500), -- Blog post URL it came from

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for fast searching
CREATE INDEX idx_pr_brands_category ON pr_brands(category);
CREATE INDEX idx_pr_brands_premium ON pr_brands(is_premium);
CREATE INDEX idx_pr_brands_name ON pr_brands(brand_name);
CREATE INDEX idx_pr_brands_niches ON pr_brands USING GIN(niches);
CREATE INDEX idx_pr_brands_regions ON pr_brands USING GIN(regions);
CREATE INDEX idx_pr_brands_platforms ON pr_brands USING GIN(platforms);

-- ============================================
-- 2. CREATOR PIPELINE TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS creator_pipeline (
    id SERIAL PRIMARY KEY,

    -- Relations
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    brand_id INT NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,

    -- Pipeline Stage
    stage VARCHAR(50) NOT NULL DEFAULT 'saved',
    -- Stages: 'saved', 'pitched', 'responded', 'success', 'rejected', 'archived'

    -- Pitch Details
    pitched_at TIMESTAMP,
    pitch_template_used VARCHAR(100), -- Which template they used
    pitch_subject VARCHAR(255),
    pitch_body TEXT,

    -- Email Tracking
    email_opened BOOLEAN DEFAULT false,
    email_opened_at TIMESTAMP,
    email_clicks INT DEFAULT 0,

    -- Response Details
    responded_at TIMESTAMP,
    response_preview TEXT, -- First 100 chars of brand's response
    response_type VARCHAR(50), -- 'positive', 'negative', 'question', 'waiting'

    -- Success Tracking
    accepted_at TIMESTAMP,
    pr_package_value DECIMAL(10, 2), -- Estimated value of PR package
    shipping_tracking_number VARCHAR(100),
    package_received_at TIMESTAMP,

    -- Content Tracking
    content_deadline TIMESTAMP,
    content_posted_at TIMESTAMP,
    content_url VARCHAR(500), -- Link to Instagram/TikTok post
    content_performance JSONB, -- {"likes": 1200, "comments": 45, "views": 5000}

    -- Notes & Reminders
    notes TEXT,
    reminder_sent_at TIMESTAMP,
    follow_up_count INT DEFAULT 0,
    last_follow_up_at TIMESTAMP,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(), -- When saved to pipeline
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Unique constraint: Creator can only have one pipeline entry per brand
    UNIQUE(creator_id, brand_id)
);

-- Indexes for performance
CREATE INDEX idx_creator_pipeline_creator ON creator_pipeline(creator_id);
CREATE INDEX idx_creator_pipeline_brand ON creator_pipeline(brand_id);
CREATE INDEX idx_creator_pipeline_stage ON creator_pipeline(stage);
CREATE INDEX idx_creator_pipeline_creator_stage ON creator_pipeline(creator_id, stage);

-- ============================================
-- 3. EMAIL TEMPLATES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS email_templates (
    id SERIAL PRIMARY KEY,

    -- Template Info
    name VARCHAR(255) NOT NULL, -- "Beauty Brands Cold Pitch"
    category VARCHAR(100), -- "cold_pitch", "follow_up", "thank_you", etc.
    niche VARCHAR(100), -- "beauty", "fashion", "tech", "general"

    -- Template Content
    subject_line VARCHAR(255) NOT NULL,
    body_template TEXT NOT NULL,

    -- Variables used: {creator_name}, {brand_name}, {follower_count}, etc.
    variables JSONB, -- ["creator_name", "brand_name", "follower_count"]

    -- Metadata
    is_premium BOOLEAN DEFAULT false,
    success_rate INT, -- Percentage (based on community data)
    usage_count INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- 4. CREATOR CUSTOM TEMPLATES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS creator_custom_templates (
    id SERIAL PRIMARY KEY,

    -- Relations
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    -- Template Content
    name VARCHAR(255) NOT NULL,
    subject_line VARCHAR(255) NOT NULL,
    body_template TEXT NOT NULL,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_creator_custom_templates_creator ON creator_custom_templates(creator_id);

-- ============================================
-- 5. UPDATE CREATORS TABLE (Add subscription fields)
-- ============================================
-- Add subscription tier tracking
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS subscription_tier VARCHAR(50) DEFAULT 'free',
ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(50) DEFAULT 'inactive',
ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS subscription_started_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS subscription_ends_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;

-- Add usage limits tracking
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS brands_saved_count INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS pitches_sent_this_month INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS pitches_sent_total INT DEFAULT 0;

-- ============================================
-- 6. ANALYTICS TABLE (Track creator activity)
-- ============================================
CREATE TABLE IF NOT EXISTS creator_analytics (
    id SERIAL PRIMARY KEY,

    -- Relations
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,

    -- Monthly Stats
    month_year VARCHAR(7) NOT NULL, -- "2026-01" format

    -- Activity Metrics
    brands_saved INT DEFAULT 0,
    pitches_sent INT DEFAULT 0,
    emails_opened INT DEFAULT 0,
    responses_received INT DEFAULT 0,
    pr_packages_accepted INT DEFAULT 0,

    -- Performance Metrics
    open_rate DECIMAL(5, 2), -- Percentage
    response_rate DECIMAL(5, 2), -- Percentage
    success_rate DECIMAL(5, 2), -- Percentage

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(creator_id, month_year)
);

CREATE INDEX idx_creator_analytics_creator ON creator_analytics(creator_id);
CREATE INDEX idx_creator_analytics_month ON creator_analytics(month_year);

-- ============================================
-- 7. TRIGGER: Update timestamps automatically
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply to all tables
CREATE TRIGGER update_pr_brands_updated_at BEFORE UPDATE ON pr_brands FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_creator_pipeline_updated_at BEFORE UPDATE ON creator_pipeline FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_email_templates_updated_at BEFORE UPDATE ON email_templates FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_creator_custom_templates_updated_at BEFORE UPDATE ON creator_custom_templates FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_creator_analytics_updated_at BEFORE UPDATE ON creator_analytics FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 8. SEED DEFAULT EMAIL TEMPLATES
-- ============================================
INSERT INTO email_templates (name, category, niche, subject_line, body_template, variables, is_premium, success_rate) VALUES

-- Template 1: Beauty Brands Cold Pitch
('Beauty Brands Cold Pitch', 'cold_pitch', 'beauty',
'Partnership Opportunity - {creator_name} x {brand_name}',
'Hi {brand_name} Team,

My name is {creator_name}, and I''m a {niche} creator with {follower_count} engaged followers on {primary_platform}.

I absolutely love your {product_category} products, and I believe my audience would be a perfect fit for {brand_name}. My followers are primarily {age_range} {gender} in {location} who are passionate about {niche}.

My Stats:
• {follower_count} followers on {primary_platform}
• {engagement_rate}% engagement rate
• {total_posts} posts in the last 90 days
• Average {avg_likes} likes per post

I''d love to collaborate through a PR partnership. I create high-quality {content_type} content and typically post within 7-14 days of receiving products.

Here''s my media kit: {media_kit_link}

Instagram: {instagram_handle}
TikTok: {tiktok_handle}

Looking forward to hearing from you!

Best,
{creator_name}
{creator_email}',
'["creator_name", "brand_name", "niche", "follower_count", "primary_platform", "product_category", "age_range", "gender", "location", "engagement_rate", "total_posts", "avg_likes", "content_type", "media_kit_link", "instagram_handle", "tiktok_handle", "creator_email"]',
false, 35),

-- Template 2: Fashion Brands Cold Pitch
('Fashion Brands Cold Pitch', 'cold_pitch', 'fashion',
'Collaboration Request - {creator_name} x {brand_name}',
'Hi {brand_name},

I''m {creator_name}, a fashion and style creator with {follower_count} followers who are obsessed with {niche}.

I''ve been following {brand_name} for a while, and your {specific_product} collection is absolutely stunning. I think it would resonate perfectly with my audience.

What I can offer:
• {follower_count} engaged followers ({engagement_rate}% engagement)
• Professional styled photos and reels
• Stories, feed posts, and reels featuring your pieces
• Authentic content that drives engagement

My audience demographic:
• Age: {age_range}
• Location: {location}
• Interests: Fashion, style, {niche}

I''d love to feature {brand_name} in my content. Here''s my media kit: {media_kit_link}

Instagram: {instagram_handle}
TikTok: {tiktok_handle}

Let me know if you''d be open to sending PR!

Best,
{creator_name}',
'["creator_name", "brand_name", "follower_count", "niche", "specific_product", "engagement_rate", "age_range", "location", "media_kit_link", "instagram_handle", "tiktok_handle"]',
false, 32),

-- Template 3: Tech/Gaming Cold Pitch
('Tech & Gaming Cold Pitch', 'cold_pitch', 'tech',
'{creator_name} - Content Creator Partnership',
'Hi {brand_name} Team,

I''m {creator_name}, a {niche} content creator with {follower_count} followers across {primary_platform}.

I specialize in {content_type} content, and I''ve been impressed by {brand_name}''s {product_category}. I believe my tech-savvy audience would love to see your products in action.

Channel Stats:
• {follower_count} subscribers/followers
• {engagement_rate}% engagement rate
• {avg_views} average views per video
• Audience: {age_range} gamers/tech enthusiasts in {location}

Content I create:
• Unboxing videos
• Product reviews
• Tutorial/how-to content
• Gaming streams featuring sponsor products

Media kit: {media_kit_link}

Social links:
{instagram_handle}
{tiktok_handle}
{youtube_handle}

Would love to discuss a partnership!

Cheers,
{creator_name}
{creator_email}',
'["creator_name", "brand_name", "niche", "follower_count", "primary_platform", "content_type", "product_category", "engagement_rate", "avg_views", "age_range", "location", "media_kit_link", "instagram_handle", "tiktok_handle", "youtube_handle", "creator_email"]',
false, 28),

-- Template 4: Follow-Up Email (7 days)
('Follow-Up Email (7 Days)', 'follow_up', 'general',
'Following up - Partnership with {creator_name}',
'Hi {brand_name},

I wanted to follow up on my email from last week about a potential PR partnership.

I''m still very interested in collaborating with {brand_name} and featuring your products to my {follower_count} followers.

Just to recap:
• {follower_count} engaged followers on {primary_platform}
• {engagement_rate}% engagement rate
• {niche} content creator
• Located in {location}

Let me know if you need any additional information. Happy to send over more details about my audience or content style!

Here''s my media kit again: {media_kit_link}

Best,
{creator_name}
{creator_email}',
'["creator_name", "brand_name", "follower_count", "primary_platform", "engagement_rate", "niche", "location", "media_kit_link", "creator_email"]',
false, 42),

-- Template 5: Thank You Email
('Thank You Email', 'thank_you', 'general',
'Thank you {brand_name}! 💕',
'Hi {brand_name} Team,

Thank you so much for sending the PR package! I just received it today, and I absolutely love everything.

I''m so excited to create content featuring your products. I''ll be posting within the next {posting_timeline}, and I''ll make sure to tag {brand_name} and use your brand hashtags.

I''ll send you links to the posts once they''re live!

Thanks again for this amazing opportunity to work together.

Best,
{creator_name}
{instagram_handle}',
'["brand_name", "posting_timeline", "creator_name", "instagram_handle"]',
false, 85);

-- ============================================
-- MIGRATION COMPLETE
-- ============================================
