-- Brand Outreach Tracking Tables
-- For the brand-acquisition agent to track outreach efforts

-- Main tracking table for brand outreach status
CREATE TABLE IF NOT EXISTS brand_outreach_tracking (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    outreach_count INTEGER DEFAULT 0,
    last_contacted_at TIMESTAMP,
    last_subject TEXT,
    last_response_status VARCHAR(50), -- 'replied', 'interested', 'not_interested', 'signed_up'
    last_response_at TIMESTAMP,
    response_notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(brand_id)
);

-- Detailed log of all outreach emails sent
CREATE TABLE IF NOT EXISTS brand_outreach_log (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES pr_brands(id) ON DELETE CASCADE,
    email_sent_to VARCHAR(255) NOT NULL,
    subject TEXT,
    html_content TEXT,
    status VARCHAR(50) DEFAULT 'sent', -- 'sent', 'opened', 'clicked', 'bounced'
    sent_at TIMESTAMP DEFAULT NOW(),
    opened_at TIMESTAMP,
    clicked_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_brand_outreach_tracking_brand_id ON brand_outreach_tracking(brand_id);
CREATE INDEX IF NOT EXISTS idx_brand_outreach_tracking_status ON brand_outreach_tracking(last_response_status);
CREATE INDEX IF NOT EXISTS idx_brand_outreach_log_brand_id ON brand_outreach_log(brand_id);
CREATE INDEX IF NOT EXISTS idx_brand_outreach_log_sent_at ON brand_outreach_log(sent_at);

-- Add trigger to update updated_at
CREATE OR REPLACE FUNCTION update_brand_outreach_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS brand_outreach_tracking_updated_at ON brand_outreach_tracking;
CREATE TRIGGER brand_outreach_tracking_updated_at
    BEFORE UPDATE ON brand_outreach_tracking
    FOR EACH ROW
    EXECUTE FUNCTION update_brand_outreach_timestamp();

-- Insert a brand_outreach template
INSERT INTO campaign_templates (name, type, subject, preview_text, html_content, variables, is_active, created_at)
VALUES (
    'Brand Acquisition - Initial Outreach',
    'brand_outreach',
    'Partner with 800+ creators on Newcollab - Free for {{brand_name}}',
    'Get authentic UGC content from vetted creators',
    '<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #f5f5f5;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f5f5f5; padding: 40px 20px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 40px 30px; text-align: center;">
                            <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 700;">Newcollab</h1>
                            <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0; font-size: 16px;">Creator Partnership Platform</p>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="padding: 40px;">
                            <h2 style="color: #333; margin: 0 0 20px; font-size: 22px;">Hi {{brand_name}} Team,</h2>

                            <p style="color: #555; line-height: 1.6; margin: 0 0 20px; font-size: 16px;">
                                I came across {{brand_name}} and thought you''d be a great fit for our creator community at <strong>Newcollab</strong>.
                            </p>

                            <p style="color: #555; line-height: 1.6; margin: 0 0 20px; font-size: 16px;">
                                We have <strong>800+ vetted creators</strong> actively looking to partner with brands like yours through product gifting and PR collaborations.
                            </p>

                            <!-- Benefits Box -->
                            <div style="background-color: #f8f9fa; border-radius: 8px; padding: 25px; margin: 25px 0;">
                                <h3 style="color: #333; margin: 0 0 15px; font-size: 18px;">What you get (100% free):</h3>
                                <ul style="color: #555; margin: 0; padding-left: 20px; line-height: 1.8;">
                                    <li>Access to 800+ creators across beauty, fashion, lifestyle & more</li>
                                    <li>Authentic UGC content for your social channels</li>
                                    <li>Easy product gifting - just send products, creators post</li>
                                    <li>No agency fees or contracts</li>
                                </ul>
                            </div>

                            <p style="color: #555; line-height: 1.6; margin: 0 0 25px; font-size: 16px;">
                                Creating a Brand Account takes 2 minutes and is completely free. Our creators are ready to create content for {{brand_name}}!
                            </p>

                            <!-- CTA Button -->
                            <div style="text-align: center; margin: 30px 0;">
                                <a href="https://app.newcollab.co/register" style="display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #ffffff; text-decoration: none; padding: 16px 40px; border-radius: 8px; font-weight: 600; font-size: 16px;">Create Free Brand Account</a>
                            </div>

                            <p style="color: #555; line-height: 1.6; margin: 25px 0 0; font-size: 16px;">
                                Happy to answer any questions!
                            </p>

                            <p style="color: #333; margin: 20px 0 0; font-size: 16px;">
                                Best,<br>
                                <strong>The Newcollab Team</strong>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 25px 40px; text-align: center; border-top: 1px solid #eee;">
                            <p style="color: #888; margin: 0; font-size: 13px;">
                                Newcollab - Connecting Brands with Creators<br>
                                <a href="https://newcollab.co" style="color: #667eea;">newcollab.co</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>',
    '["brand_name", "website", "category"]',
    true,
    NOW()
)
ON CONFLICT DO NOTHING;
