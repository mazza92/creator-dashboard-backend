"""The Pro welcome email: name, first brands, Gmail state, escaping."""
import unittest
from unittest.mock import patch

from services import subscription_retention as sr


class ProWelcome(unittest.TestCase):
    def test_context_by_gmail_state(self):
        off = sr.activation_email_context('Ana', [], gmail_connected=False)
        self.assertIn('One step', off['subject'])
        self.assertIn('Connect Gmail', off['action_text'])
        on = sr.activation_email_context('Ana', [], gmail_connected=True)
        self.assertIn('starts pitching today', on['subject'])
        self.assertTrue(on['action_url'].endswith('/creator/dashboard/for-you'))

    def test_render_escapes_and_shows_picks(self):
        picks = {'first_name': '<b>Ana</b>', 'brands': [
            {'brand_id': 1, 'brand_name': 'Glow & Co', 'logo': None, 'initials': 'GC', 'color': '#1f2937'}]}
        with patch('waitlist_emails.approved_picks', return_value=picks):
            subject, html = sr.activation_email(creator_id=None, name='fallback')
        self.assertIn("You're Pro", subject)
        self.assertIn('fallback', html)  # no creator id: no lookup, the given name
        with patch('waitlist_emails.approved_picks', return_value=picks), \
                patch('pr_crm_routes.get_db_connection', side_effect=RuntimeError('no db')):
            _, html = sr.activation_email(creator_id=5, name='x')
        self.assertIn("You're Pro, &lt;b&gt;Ana&lt;/b&gt;", html)
        self.assertIn('Glow &amp; Co', html)
        self.assertIn('First up this week', html)
        self.assertIn('Connect Gmail', html)
        self.assertIn('Manage billing', html)

    def test_legacy_helper_still_renders(self):
        self.assertIn('Polly', sr.activation_email_html('Ana'))


if __name__ == '__main__':
    unittest.main()
