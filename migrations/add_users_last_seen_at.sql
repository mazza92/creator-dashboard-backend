-- When a signed-in user last used the app (stamped at most hourly by services/last_seen.py).
-- users.last_login only moves on an explicit login, so it misses creators who return while still signed in.
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
UPDATE users SET last_seen_at = last_login WHERE last_seen_at IS NULL AND last_login IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_last_seen_at ON users (last_seen_at);
