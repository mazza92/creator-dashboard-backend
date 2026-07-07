-- Migration: Add "Complete Your Portfolio" email template
-- For the kit_unpublished segment - nudge creators to publish their portfolio

INSERT INTO campaign_templates (name, type, subject, preview_text, html_content, variables, is_active)
VALUES (
    'Complete Your Portfolio',
    'engagement',
    'Your portfolio is almost ready',
    'Brands are actively reviewing creator portfolios on Newcollab',
    '<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Complete Your Portfolio - Newcollab</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, ''Segoe UI'', Roboto, ''Helvetica Neue'', Arial, sans-serif; background-color: #f5f5f7; color: #1d1d1f;">
  <div style="display: none; font-size: 1px; color: #f5f5f7; line-height: 1px; max-height: 0; max-width: 0; opacity: 0; overflow: hidden;">
    Brands are actively reviewing creator portfolios on Newcollab
  </div>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f5f5f7;">
    <tr>
      <td style="padding: 40px 20px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 520px; margin: 0 auto; background-color: #ffffff; border-radius: 20px; overflow: hidden;">

          <!-- Logo -->
          <tr>
            <td style="padding: 32px 32px 24px; text-align: center;">
              <img src="https://newcollab.co/newcollab-logo-dark.png" alt="Newcollab" width="120" style="max-width: 120px;">
            </td>
          </tr>

          <!-- Main Content -->
          <tr>
            <td style="padding: 0 32px 32px;">
              <h1 style="font-size: 24px; font-weight: 600; margin: 0 0 20px; color: #1d1d1f; text-align: center;">
                Complete your portfolio
              </h1>

              <p style="font-size: 16px; line-height: 1.6; color: #1d1d1f; margin: 0 0 20px;">
                Hi {{first_name}},
              </p>

              <p style="font-size: 16px; line-height: 1.6; color: #1d1d1f; margin: 0 0 20px;">
                Your creator portfolio on Newcollab is almost ready - but brands can only see and review creators with a <strong>published portfolio</strong>.
              </p>

              <p style="font-size: 16px; line-height: 1.6; color: #1d1d1f; margin: 0 0 20px;">
                A complete portfolio helps brands understand your content style, audience, and past collaborations at a glance. It''s the first thing they check before responding to pitches.
              </p>

              <!-- Feature highlight box -->
              <div style="background-color: #f0f9ff; border-radius: 12px; padding: 20px; margin: 24px 0;">
                <p style="font-size: 14px; line-height: 1.5; color: #0369a1; margin: 0;">
                  <strong>Pro tip:</strong> With a published portfolio, you can also track when brands view your page - so you know exactly who''s checking you out.
                </p>
              </div>

              <!-- CTA Button -->
              <div style="text-align: center; margin: 28px 0 8px;">
                <a href="https://app.newcollab.co/creator/dashboard/kit" style="display: inline-block; padding: 14px 28px; background-color: #1d1d1f; color: #ffffff; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 15px;">
                  Complete & Publish My Portfolio
                </a>
              </div>

              <p style="font-size: 14px; line-height: 1.5; color: #86868b; margin: 24px 0 0; text-align: center;">
                Takes less than 2 minutes
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 24px 32px; background-color: #f5f5f7; border-top: 1px solid #e8e8ed;">
              <p style="font-size: 13px; color: #86868b; margin: 0 0 8px; text-align: center;">
                Newcollab helps creators land PR packages from brands they love.
              </p>
              <p style="font-size: 12px; color: #86868b; margin: 0 0 10px; text-align: center;">
                <a href="mailto:team@newcollab.co" style="color: #86868b; text-decoration: underline;">Help</a>
                &nbsp;&middot;&nbsp;
                <a href="{{unsubscribe_url}}" style="color: #86868b; text-decoration: underline;">Unsubscribe</a>
              </p>
              <p style="font-size: 11px; color: #aeaeb2; margin: 0; text-align: center;">
                &copy; 2026 Newcollab. All rights reserved.<br>
                Newcollab Ltd &middot; <a href="https://newcollab.co/privacy-policy" style="color: #aeaeb2; text-decoration: underline;">Privacy Policy</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>',
    '["first_name", "unsubscribe_url"]',
    true
);
