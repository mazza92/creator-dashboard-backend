-- Migration: Add PR Packages table for the PR Package pivot
-- This table stores the complete PR Package generated for each creator-brand pair
-- Generated packages are cached and served instantly on revisits

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- PR Packages table
CREATE TABLE IF NOT EXISTS pr_packages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  brand_id INT NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,

  -- Section 2: Pitches (3 tones)
  -- SHORT tone: 60-90 words, direct, minimal
  pitch_short_subject TEXT,
  pitch_short_body_html TEXT,
  pitch_short_body_plain TEXT,

  -- GROWING tone: 100-140 words, balanced, includes stats
  pitch_growing_subject TEXT,
  pitch_growing_body_html TEXT,
  pitch_growing_body_plain TEXT,

  -- FOUNDER tone: 130-180 words, warmer, more personal
  pitch_founder_subject TEXT,
  pitch_founder_body_html TEXT,
  pitch_founder_body_plain TEXT,

  -- Section 3: Optimal send timing (deterministic, no Gemini)
  optimal_send_day VARCHAR(20),           -- e.g. 'Tuesday'
  optimal_send_time_range VARCHAR(20),    -- e.g. '2-5pm ET'
  timing_sample_size INT,                 -- e.g. 47 (brand's past replies used)
  timing_uplift_multiplier NUMERIC(3,1),  -- e.g. 3.1

  -- Section 4: Content Playbook (5 content ideas, Pro feature)
  -- JSONB array: [{title, format, why_this_brand}, ...]
  content_ideas JSONB,

  -- Section 5: Follow-up sequence (3 messages, Pro feature)
  followup_day3_subject TEXT,
  followup_day3_body TEXT,
  followup_day8_subject TEXT,
  followup_day8_body TEXT,
  followup_day14_subject TEXT,
  followup_day14_body TEXT,

  -- Section 6: Reply prediction (deterministic + brand-level)
  reply_rate_brand_avg NUMERIC(4,2),       -- e.g. 35.00 (free tier visible)
  reply_rate_personalized NUMERIC(4,2),    -- e.g. 42.00 (Pro-only surfaced)
  reply_rate_confidence VARCHAR(10),       -- 'low' | 'medium' | 'high'

  -- Meta
  generated_by VARCHAR(20) DEFAULT 'gemini', -- 'gemini' | 'fallback_template'
  generation_reasoning TEXT,                  -- Gemini's own explanation (internal only)
  scrub_failures_count INT DEFAULT 0,         -- How many times scrubber caught issues before success
  generated_at TIMESTAMP DEFAULT NOW(),
  regenerated_count INT DEFAULT 0,            -- Manual regeneration count

  -- Unique constraint: one package per creator-brand pair
  UNIQUE(creator_id, brand_id)
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_pr_packages_creator ON pr_packages(creator_id);
CREATE INDEX IF NOT EXISTS idx_pr_packages_brand ON pr_packages(brand_id);
CREATE INDEX IF NOT EXISTS idx_pr_packages_generated_at ON pr_packages(generated_at DESC);

-- Add column to track package status in creator_pipeline
ALTER TABLE creator_pipeline
ADD COLUMN IF NOT EXISTS has_pr_package BOOLEAN DEFAULT FALSE;

-- Comment for documentation
COMMENT ON TABLE pr_packages IS 'Stores complete PR Packages (pitch variants, content ideas, follow-ups, timing, predictions) for each creator-brand unlock. Generated via Gemini with AI-tell scrubbing.';
COMMENT ON COLUMN pr_packages.content_ideas IS 'JSON array of 5 content ideas: [{title: string, format: string, why_this_brand: string}]';
COMMENT ON COLUMN pr_packages.generated_by IS 'gemini = AI-generated, fallback_template = template used due to Gemini failure';
COMMENT ON COLUMN pr_packages.generation_reasoning IS 'Internal-only: Gemini explains its creative strategy. Never shown to users.';
