-- Audit trail for GDPR / privacy account deletion.
-- Stores no plaintext email or other personal data.
CREATE TABLE IF NOT EXISTS account_deletion_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    creator_id INTEGER,
    email_hash TEXT NOT NULL,
    role TEXT,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_account_deletion_log_email_hash
    ON account_deletion_log(email_hash);

CREATE INDEX IF NOT EXISTS idx_account_deletion_log_deleted_at
    ON account_deletion_log(deleted_at DESC);
