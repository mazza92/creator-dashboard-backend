-- Maxed-out lifecycle email: credit cap + 1 gifting campaign / month, not unlocks/follow-ups.
UPDATE lifecycle_email_templates
SET
    name = 'Your 3 are out',
    preheader_template = 'Your 3 are out. Pro guarantees 1 gifting campaign a month.',
    updated_at = NOW()
WHERE slug = 'max_quota_hit';
