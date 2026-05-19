-- Migration: Add PR Pipeline columns to creator_pipeline table
-- Run this migration to enable the new pipeline features

-- Add new columns to creator_pipeline (existing table uses 'stage' column)
ALTER TABLE creator_pipeline
  ADD COLUMN IF NOT EXISTS send_confirmed    BOOLEAN      NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS followup_count    INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS followup_sent_at  TIMESTAMP,
  ADD COLUMN IF NOT EXISTS replied_at        TIMESTAMP,
  ADD COLUMN IF NOT EXISTS reply_type        VARCHAR(20),
  ADD COLUMN IF NOT EXISTS package_confirmed_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS package_value     INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS expected_delivery DATE,
  ADD COLUMN IF NOT EXISTS received_at       TIMESTAMP;

-- Note: 'stage' column already exists, we'll use these stage values:
-- 'saved'     -> bookmarked, not yet contacted
-- 'waiting'   -> pitch sent and confirmed by user (replaces 'pitched')
-- 'followup'  -> follow-up sent
-- 'replied'   -> creator marked as replied (pending reply_type)
-- 'won'       -> package confirmed coming (replaces 'success')
-- 'received'  -> package physically received
-- 'archived'  -> no response after 2 follow-ups, or "not a fit"

-- reply_type enum values:
-- 'package_coming' | 'need_info' | 'not_fit' | 'unsure'

-- Create indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_creator_pipeline_stage
  ON creator_pipeline(creator_id, stage);

CREATE INDEX IF NOT EXISTS idx_creator_pipeline_followup
  ON creator_pipeline(pitched_at)
  WHERE stage IN ('waiting', 'followup') AND send_confirmed = TRUE;

-- Add columns to creators table for pitch tracking if not exists
ALTER TABLE creators
  ADD COLUMN IF NOT EXISTS pitches_sent_this_month INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS first_pitch_sent_at TIMESTAMP;

-- Add response tracking columns to pr_brands if not exists
ALTER TABLE pr_brands
  ADD COLUMN IF NOT EXISTS responses_received INT NOT NULL DEFAULT 0;
