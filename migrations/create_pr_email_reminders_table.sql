-- Create table for PR package email reminders
CREATE TABLE IF NOT EXISTS pr_email_reminders (
    id SERIAL PRIMARY KEY,
    offer_id VARCHAR(255), -- NULL for introduction emails, actual offer_id for PR reminders
    reminder_type VARCHAR(50) NOT NULL, -- 'product_received_check', 'start_content_reminder', or 'pr_packages_introduction'
    scheduled_for TIMESTAMP NOT NULL,
    sent_at TIMESTAMP,
    user_id INTEGER NOT NULL,
    user_role VARCHAR(20) NOT NULL,
    email VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Create index for efficient querying
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_scheduled ON pr_email_reminders(scheduled_for, sent_at);
CREATE INDEX IF NOT EXISTS idx_pr_email_reminders_offer ON pr_email_reminders(offer_id);

