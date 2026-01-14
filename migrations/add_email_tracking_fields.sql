-- Add email tracking fields to creators table
-- These fields help track email campaigns and prevent spam

-- Add last_new_brands_email_sent column
ALTER TABLE creators
ADD COLUMN IF NOT EXISTS last_new_brands_email_sent TIMESTAMP DEFAULT NULL;

-- Create index for efficient querying
CREATE INDEX IF NOT EXISTS idx_creators_last_new_brands_email ON creators(last_new_brands_email_sent);

-- Comment for documentation
COMMENT ON COLUMN creators.last_new_brands_email_sent IS 'Timestamp of last "new brands added" notification email sent to this creator';

-- Create pr_email_reminders table for tracking PR package reminders
CREATE TABLE IF NOT EXISTS pr_email_reminders (
    id SERIAL PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES pr_packages(id) ON DELETE CASCADE,
    reminder_type VARCHAR(50) NOT NULL, -- 'product_received_check' or 'start_content'
    sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_package ON pr_email_reminders(package_id);
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_type ON pr_email_reminders(reminder_type);
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_sent ON pr_email_reminders(sent_at);

-- Comment for documentation
COMMENT ON TABLE pr_email_reminders IS 'Tracks PR package reminder emails sent to creators';
COMMENT ON COLUMN pr_email_reminders.reminder_type IS 'Type of reminder: product_received_check (7 days after shipping) or start_content (48 hours after receipt)';
