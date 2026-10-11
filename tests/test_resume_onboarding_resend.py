import unittest
from unittest.mock import patch

from routes.admin_creators import _resume_onboarding_email_context


class ResumeOnboardingEmailTests(unittest.TestCase):
    def test_google_copy_has_no_password_reset(self):
        ctx = _resume_onboarding_email_context({
            'user_id': 5,
            'first_name': 'Alex',
            'is_google': True,
        })
        self.assertEqual(ctx['action_text'], 'Continue with Google')
        self.assertIn('/login', ctx['action_url'])
        self.assertNotIn('secondary_action_url', ctx)
        self.assertIn('Instagram or TikTok', ctx['message'])
        self.assertNotIn('password', ctx['message'].lower())

    def test_email_signup_still_offers_password_reset(self):
        ctx = _resume_onboarding_email_context({
            'user_id': 5,
            'first_name': 'Alex',
            'is_google': False,
        })
        self.assertIn('forgot-password', ctx['secondary_action_url'])

    def test_resume_send_uses_resend_and_includes_pending(self):
        import email_cron_routes

        with patch.object(email_cron_routes, 'render_email_template', return_value=('<p>Hi</p>', None)), \
             patch('services.resend_mail.send_resend_email', return_value={'success': True, 'error': None}) as send, \
             patch.object(email_cron_routes, '_recipient_waitlisted', return_value=True) as waitlisted, \
             patch.object(email_cron_routes, '_send_smtp_html') as smtp:
            ok, err = email_cron_routes.send_template_email_resend(
                'creator@example.com',
                'resume_onboarding.html',
                'Finish',
                {'user_id': 5},
                allow_waitlisted=True,
                tags=[{'name': 'type', 'value': 'resume_onboarding'}],
            )
        self.assertTrue(ok)
        self.assertIsNone(err)
        send.assert_called_once()
        smtp.assert_not_called()
        waitlisted.assert_not_called()


if __name__ == '__main__':
    unittest.main()
