-- Brand Discovery System Migration
-- Enables universal brand lookup beyond the curated 431 brands
-- Phase 1: Tier 1 lookup (existing data) + caching

-- ============================================
-- 1. ADD SOURCE TRACKING TO PR_BRANDS
-- ============================================
-- Track whether brand is curated (manual) or discovered (via search)
ALTER TABLE pr_brands
ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'curated',
ADD COLUMN IF NOT EXISTS discovery_tier INT,  -- 1=existing data, 2=web scrape, 3=pattern inference
ADD COLUMN IF NOT EXISTS search_count INT DEFAULT 0,  -- Times searched for this brand
ADD COLUMN IF NOT EXISTS contact_count INT DEFAULT 0,  -- Times contact was used
ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMP,
ADD COLUMN IF NOT EXISTS verified_contact BOOLEAN DEFAULT true,  -- false for Tier 3 inferred contacts
ADD COLUMN IF NOT EXISTS promoted_to_curated_at TIMESTAMP;  -- When manually promoted from discovered

-- Index for filtering by source
CREATE INDEX IF NOT EXISTS idx_pr_brands_source ON pr_brands(source);
CREATE INDEX IF NOT EXISTS idx_pr_brands_search_count ON pr_brands(search_count DESC);

-- ============================================
-- 2. BRAND DISCOVERY LOGS TABLE
-- ============================================
-- Track every discovery attempt for analytics and rate limiting
CREATE TABLE IF NOT EXISTS brand_discovery_logs (
    id SERIAL PRIMARY KEY,

    -- Who searched
    creator_id INT REFERENCES creators(id) ON DELETE SET NULL,

    -- What they searched
    search_query VARCHAR(255) NOT NULL,
    normalized_query VARCHAR(255) NOT NULL,  -- Lowercase, trimmed

    -- Result
    found_brand_id INT REFERENCES pr_brands(id) ON DELETE SET NULL,
    result_tier INT,  -- 1, 2, 3 or NULL (not found)
    result_status VARCHAR(50) NOT NULL,  -- 'found_curated', 'found_discovered', 'discovered_new', 'not_found'

    -- Metadata
    ip_address VARCHAR(45),
    user_agent TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for analytics and rate limiting
CREATE INDEX IF NOT EXISTS idx_discovery_logs_creator ON brand_discovery_logs(creator_id);
CREATE INDEX IF NOT EXISTS idx_discovery_logs_query ON brand_discovery_logs(normalized_query);
CREATE INDEX IF NOT EXISTS idx_discovery_logs_created ON brand_discovery_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_discovery_logs_creator_date ON brand_discovery_logs(creator_id, created_at);

-- ============================================
-- 3. KNOWN BRAND CONTACTS TABLE (Tier 1 seed data)
-- ============================================
-- Pre-seeded from GSC keyword data showing creators searched exact email addresses
CREATE TABLE IF NOT EXISTS known_brand_contacts (
    id SERIAL PRIMARY KEY,

    -- Brand identification
    brand_name VARCHAR(255) NOT NULL,
    normalized_name VARCHAR(255) NOT NULL,  -- Lowercase, no spaces
    domain VARCHAR(255),

    -- Contact info
    contact_email VARCHAR(255) NOT NULL,
    contact_type VARCHAR(50) DEFAULT 'pr',  -- pr, press, partnerships, marketing, general

    -- Source tracking
    source VARCHAR(100) NOT NULL,  -- 'gsc_keyword', 'creator_submitted', 'web_scrape', 'manual'
    source_detail TEXT,  -- Additional context (e.g., which GSC query)

    -- Trust level
    verified BOOLEAN DEFAULT false,
    verified_at TIMESTAMP,
    verified_by VARCHAR(100),  -- 'manual', 'email_response', 'web_scrape'

    -- Usage stats
    usage_count INT DEFAULT 0,
    last_used_at TIMESTAMP,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Prevent duplicates
    UNIQUE(normalized_name, contact_email)
);

CREATE INDEX IF NOT EXISTS idx_known_contacts_name ON known_brand_contacts(normalized_name);
CREATE INDEX IF NOT EXISTS idx_known_contacts_domain ON known_brand_contacts(domain);

-- ============================================
-- 4. SEED KNOWN CONTACTS FROM GSC DATA
-- ============================================
-- These are real PR emails that creators have searched for in GSC
INSERT INTO known_brand_contacts (brand_name, normalized_name, domain, contact_email, contact_type, source, verified)
VALUES
    ('Good Molecules', 'goodmolecules', 'goodmolecules.com', 'pr@goodmolecules.com', 'pr', 'gsc_keyword', true),
    ('Milk Makeup', 'milkmakeup', 'milkmakeup.com', 'pr@milkmakeup.com', 'pr', 'gsc_keyword', true),
    ('Alo Yoga', 'aloyoga', 'aloyoga.com', 'pr@aloyoga.com', 'pr', 'gsc_keyword', true),
    ('Tower 28', 'tower28', 'tower28beauty.com', 'hello@tower28beauty.com', 'pr', 'gsc_keyword', true),
    ('Summer Fridays', 'summerfridays', 'summerfridays.com', 'pr@summerfridays.com', 'pr', 'gsc_keyword', true),
    ('Poppi', 'poppi', 'drinkpoppi.com', 'hello@drinkpoppi.com', 'pr', 'gsc_keyword', true),
    ('Rare Beauty', 'rarebeauty', 'rarebeauty.com', 'pr@rarebeauty.com', 'pr', 'gsc_keyword', true),
    ('Rhode', 'rhode', 'rhodeskin.com', 'pr@rhodeskin.com', 'pr', 'gsc_keyword', true),
    ('Glossier', 'glossier', 'glossier.com', 'press@glossier.com', 'press', 'gsc_keyword', true),
    ('Tatcha', 'tatcha', 'tatcha.com', 'pr@tatcha.com', 'pr', 'gsc_keyword', true),
    ('Drunk Elephant', 'drunkelephant', 'drunkelephant.com', 'pr@drunkelephant.com', 'pr', 'gsc_keyword', true),
    ('Sol de Janeiro', 'soldejaneiro', 'soldejaneiro.com', 'pr@soldejaneiro.com', 'pr', 'gsc_keyword', true),
    ('Supergoop', 'supergoop', 'supergoop.com', 'pr@supergoop.com', 'pr', 'gsc_keyword', true),
    ('Glow Recipe', 'glowrecipe', 'glowrecipe.com', 'pr@glowrecipe.com', 'pr', 'gsc_keyword', true),
    ('Youth to the People', 'youthtothepeople', 'youthtothepeople.com', 'pr@youthtothepeople.com', 'pr', 'gsc_keyword', true),
    ('Kosas', 'kosas', 'kosas.com', 'pr@kosas.com', 'pr', 'gsc_keyword', true),
    ('Patrick Ta', 'patrickta', 'patrickta.com', 'pr@patrickta.com', 'pr', 'gsc_keyword', true),
    ('Saie', 'saie', 'saiehello.com', 'pr@saiehello.com', 'pr', 'gsc_keyword', true),
    ('Merit', 'merit', 'meritbeauty.com', 'pr@meritbeauty.com', 'pr', 'gsc_keyword', true),
    ('Ilia', 'ilia', 'iliabeauty.com', 'pr@iliabeauty.com', 'pr', 'gsc_keyword', true),
    ('Lawless', 'lawless', 'lawlessbeauty.com', 'pr@lawlessbeauty.com', 'pr', 'gsc_keyword', true),
    ('About Face', 'aboutface', 'aboutface.com', 'pr@aboutface.com', 'pr', 'gsc_keyword', true),
    ('E.l.f. Cosmetics', 'elfcosmetics', 'elfcosmetics.com', 'pr@elfcosmetics.com', 'pr', 'gsc_keyword', true),
    ('ColourPop', 'colourpop', 'colourpop.com', 'pr@colourpop.com', 'pr', 'gsc_keyword', true),
    ('Fenty Beauty', 'fentybeauty', 'fentybeauty.com', 'pr@fentybeauty.com', 'pr', 'gsc_keyword', true),
    ('Morphe', 'morphe', 'morphe.com', 'pr@morphe.com', 'pr', 'gsc_keyword', true),
    ('NYX', 'nyx', 'nyxcosmetics.com', 'pr@nyxcosmetics.com', 'pr', 'gsc_keyword', true),
    ('Urban Decay', 'urbandecay', 'urbandecay.com', 'pr@urbandecay.com', 'pr', 'gsc_keyword', true),
    ('Too Faced', 'toofaced', 'toofaced.com', 'pr@toofaced.com', 'pr', 'gsc_keyword', true),
    ('Benefit', 'benefit', 'benefitcosmetics.com', 'pr@benefitcosmetics.com', 'pr', 'gsc_keyword', true),
    ('MAC', 'mac', 'maccosmetics.com', 'pr@maccosmetics.com', 'pr', 'gsc_keyword', true),
    ('Charlotte Tilbury', 'charlottetilbury', 'charlottetilbury.com', 'press@charlottetilbury.com', 'press', 'gsc_keyword', true),
    ('Hourglass', 'hourglass', 'hourglasscosmetics.com', 'pr@hourglasscosmetics.com', 'pr', 'gsc_keyword', true),
    ('Tarte', 'tarte', 'tartecosmetics.com', 'pr@tartecosmetics.com', 'pr', 'gsc_keyword', true),
    ('Anastasia Beverly Hills', 'anastasiabeverlyhills', 'anastasiabeverlyhills.com', 'pr@anastasiabeverlyhills.com', 'pr', 'gsc_keyword', true),
    ('Huda Beauty', 'hudabeauty', 'hudabeauty.com', 'pr@hudabeauty.com', 'pr', 'gsc_keyword', true),
    ('Pat McGrath', 'patmcgrath', 'patmcgrath.com', 'pr@patmcgrath.com', 'pr', 'gsc_keyword', true),
    ('Natasha Denona', 'natashadenona', 'natashadenona.com', 'pr@natashadenona.com', 'pr', 'gsc_keyword', true),
    ('Laura Mercier', 'lauramercier', 'lauramercier.com', 'pr@lauramercier.com', 'pr', 'gsc_keyword', true),
    ('NARS', 'nars', 'narscosmetics.com', 'pr@narscosmetics.com', 'pr', 'gsc_keyword', true),
    ('Bobbi Brown', 'bobbibrown', 'bobbibrowncosmetics.com', 'pr@bobbibrowncosmetics.com', 'pr', 'gsc_keyword', true),
    ('Clinique', 'clinique', 'clinique.com', 'pr@clinique.com', 'pr', 'gsc_keyword', true),
    ('Estee Lauder', 'esteelauder', 'esteelauder.com', 'pr@esteelauder.com', 'pr', 'gsc_keyword', true),
    ('Shiseido', 'shiseido', 'shiseido.com', 'pr@shiseido.com', 'pr', 'gsc_keyword', true),
    ('Origins', 'origins', 'origins.com', 'pr@origins.com', 'pr', 'gsc_keyword', true),
    ('Fresh', 'fresh', 'fresh.com', 'pr@fresh.com', 'pr', 'gsc_keyword', true),
    ('First Aid Beauty', 'firstaidbeauty', 'firstaidbeauty.com', 'pr@firstaidbeauty.com', 'pr', 'gsc_keyword', true),
    ('Paula''s Choice', 'paulaschoice', 'paulaschoice.com', 'pr@paulaschoice.com', 'pr', 'gsc_keyword', true),
    ('The Ordinary', 'theordinary', 'theordinary.com', 'pr@deciem.com', 'pr', 'gsc_keyword', true),
    ('CeraVe', 'cerave', 'cerave.com', 'pr@cerave.com', 'pr', 'gsc_keyword', true),
    ('La Roche-Posay', 'larocheposay', 'laroche-posay.us', 'pr@laroche-posay.us', 'pr', 'gsc_keyword', true),
    ('Laneige', 'laneige', 'laneige.com', 'pr@laneige.com', 'pr', 'gsc_keyword', true),
    ('Innisfree', 'innisfree', 'innisfree.com', 'pr@innisfree.com', 'pr', 'gsc_keyword', true),
    ('Olaplex', 'olaplex', 'olaplex.com', 'pr@olaplex.com', 'pr', 'gsc_keyword', true),
    ('Ouai', 'ouai', 'theouai.com', 'pr@theouai.com', 'pr', 'gsc_keyword', true),
    ('Amika', 'amika', 'loveamika.com', 'pr@loveamika.com', 'pr', 'gsc_keyword', true),
    ('Briogeo', 'briogeo', 'briogeohair.com', 'pr@briogeohair.com', 'pr', 'gsc_keyword', true),
    ('Living Proof', 'livingproof', 'livingproof.com', 'pr@livingproof.com', 'pr', 'gsc_keyword', true),
    ('Drybar', 'drybar', 'thedrybar.com', 'pr@thedrybar.com', 'pr', 'gsc_keyword', true),
    ('Bumble and bumble', 'bumbleandbumble', 'bumbleandbumble.com', 'pr@bumbleandbumble.com', 'pr', 'gsc_keyword', true),
    ('Aveda', 'aveda', 'aveda.com', 'pr@aveda.com', 'pr', 'gsc_keyword', true),
    ('Skims', 'skims', 'skims.com', 'pr@skims.com', 'pr', 'gsc_keyword', true),
    ('Parade', 'parade', 'yourparade.com', 'pr@yourparade.com', 'pr', 'gsc_keyword', true),
    ('Aritzia', 'aritzia', 'aritzia.com', 'pr@aritzia.com', 'pr', 'gsc_keyword', true),
    ('Reformation', 'reformation', 'thereformation.com', 'pr@thereformation.com', 'pr', 'gsc_keyword', true),
    ('Revolve', 'revolve', 'revolve.com', 'pr@revolve.com', 'pr', 'gsc_keyword', true),
    ('Princess Polly', 'princesspolly', 'princesspolly.com', 'pr@princesspolly.com', 'pr', 'gsc_keyword', true),
    ('House of CB', 'houseofcb', 'houseofcb.com', 'pr@houseofcb.com', 'pr', 'gsc_keyword', true),
    ('Meshki', 'meshki', 'meshki.com.au', 'pr@meshki.com.au', 'pr', 'gsc_keyword', true),
    ('Oh Polly', 'ohpolly', 'ohpolly.com', 'pr@ohpolly.com', 'pr', 'gsc_keyword', true),
    ('White Fox', 'whitefox', 'whitefoxboutique.com', 'pr@whitefoxboutique.com', 'pr', 'gsc_keyword', true),
    ('Gymshark', 'gymshark', 'gymshark.com', 'pr@gymshark.com', 'pr', 'gsc_keyword', true),
    ('Lululemon', 'lululemon', 'lululemon.com', 'pr@lululemon.com', 'pr', 'gsc_keyword', true),
    ('Athleta', 'athleta', 'athleta.gap.com', 'pr@athleta.com', 'pr', 'gsc_keyword', true),
    ('Outdoor Voices', 'outdoorvoices', 'outdoorvoices.com', 'pr@outdoorvoices.com', 'pr', 'gsc_keyword', true),
    ('Set Active', 'setactive', 'setactive.co', 'pr@setactive.co', 'pr', 'gsc_keyword', true),
    ('Girlfriend Collective', 'girlfriendcollective', 'girlfriend.com', 'pr@girlfriend.com', 'pr', 'gsc_keyword', true)
ON CONFLICT (normalized_name, contact_email) DO NOTHING;

-- ============================================
-- 5. DISCOVERY RATE LIMITS TABLE
-- ============================================
-- Track daily discovery attempts per creator for rate limiting
CREATE TABLE IF NOT EXISTS creator_discovery_limits (
    id SERIAL PRIMARY KEY,
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    attempts INT DEFAULT 0,
    successful_discoveries INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(creator_id, date)
);

CREATE INDEX IF NOT EXISTS idx_discovery_limits_creator_date ON creator_discovery_limits(creator_id, date);

-- ============================================
-- MIGRATION COMPLETE
-- ============================================
