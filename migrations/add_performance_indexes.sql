-- ============================================
-- PERFORMANCE INDEXES MIGRATION
-- Add indexes for frequently queried columns
-- ============================================

-- Collaboration Requests indexes
CREATE INDEX IF NOT EXISTS idx_collab_requests_creator_id ON collaboration_requests(creator_id);
CREATE INDEX IF NOT EXISTS idx_collab_requests_brand_id ON collaboration_requests(brand_id);
CREATE INDEX IF NOT EXISTS idx_collab_requests_created_at ON collaboration_requests(created_at DESC);
-- NOTE: status column doesn't exist on collaboration_requests

-- Messages indexes
-- NOTE: messages table doesn't have request_id column - skipping those indexes

-- Bookings indexes (dashboard stats queries)
CREATE INDEX IF NOT EXISTS idx_bookings_creator_id ON bookings(creator_id);
CREATE INDEX IF NOT EXISTS idx_bookings_brand_id ON bookings(brand_id);
CREATE INDEX IF NOT EXISTS idx_bookings_payment_status ON bookings(payment_status);
CREATE INDEX IF NOT EXISTS idx_bookings_content_status ON bookings(content_status);
CREATE INDEX IF NOT EXISTS idx_bookings_type ON bookings(type);
CREATE INDEX IF NOT EXISTS idx_bookings_creator_type ON bookings(creator_id, type);
CREATE INDEX IF NOT EXISTS idx_bookings_created_at ON bookings(created_at DESC);

-- Creators indexes
CREATE INDEX IF NOT EXISTS idx_creators_user_id ON creators(user_id);
CREATE INDEX IF NOT EXISTS idx_creators_username ON creators(username);
CREATE INDEX IF NOT EXISTS idx_creators_subscription_tier ON creators(subscription_tier);
CREATE INDEX IF NOT EXISTS idx_creators_created_at ON creators(created_at DESC);

-- Brands indexes
CREATE INDEX IF NOT EXISTS idx_brands_spotlight ON brands(spotlight) WHERE spotlight = TRUE;
CREATE INDEX IF NOT EXISTS idx_brands_status ON brands(status);
CREATE INDEX IF NOT EXISTS idx_brands_category ON brands(category);
CREATE INDEX IF NOT EXISTS idx_brands_name ON brands(name);

-- Campaigns indexes
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_brand_id ON campaigns(brand_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_created_at ON campaigns(created_at DESC);

-- Brand unlocks indexes (lifecycle email queries)
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_creator_id ON brand_unlocks(creator_id);
CREATE INDEX IF NOT EXISTS idx_brand_unlocks_created_at ON brand_unlocks(created_at DESC);

-- Users indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_is_verified ON users(is_verified);
CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login DESC);

-- Lifecycle email sends indexes
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_creator_template ON lifecycle_email_sends(creator_id, template_slug);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sends_sent_at ON lifecycle_email_sends(sent_at DESC);

-- Email preferences index
CREATE INDEX IF NOT EXISTS idx_email_prefs_unsubscribed ON email_preferences(creator_id) WHERE unsubscribed_at IS NOT NULL;

-- ============================================
-- MIGRATION COMPLETE
-- Run: psql -d your_database -f add_performance_indexes.sql
-- ============================================
