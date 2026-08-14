-- $9 pack bundle: extra unlocks that sit on top of the 3 free monthly packs.
ALTER TABLE creators ADD COLUMN IF NOT EXISTS pack_credits INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS pack_purchases (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL,
    stripe_session_id TEXT NOT NULL UNIQUE,
    packs INTEGER NOT NULL DEFAULT 3,
    amount_cents INTEGER NOT NULL DEFAULT 900,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
