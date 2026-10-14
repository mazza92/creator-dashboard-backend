"""Transactional template emails go through Resend when configured, SMTP otherwise."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_cron_routes
import waitlist_emails


def _send(resend_result=None, configured=True, smtp_password='x', **kwargs):
    with patch.object(email_cron_routes, '_recipient_waitlisted', return_value=False), \
            patch.object(email_cron_routes, 'render_email_template', return_value=('<p>Hi</p>', 'https://u')), \
            patch('services.resend_mail.resend_configured', return_value=configured), \
            patch('services.resend_mail.send_resend_email', return_value=resend_result or {'success': True}) as resend, \
            patch.object(email_cron_routes, '_send_smtp_html') as smtp, \
            patch.dict('os.environ', {'SMTP_PASSWORD': smtp_password}):
        result = email_cron_routes.send_template_email('c@x.com', 'welcome_email.html', 'Hi', {}, **kwargs)
    return result, resend, smtp


class TestSendTemplateEmail(unittest.TestCase):
    def test_uses_resend_with_default_tags(self):
        (ok, err), resend, smtp = _send()
        self.assertTrue(ok)
        smtp.assert_not_called()
        kwargs = resend.call_args.kwargs
        self.assertIn({'name': 'template', 'value': 'welcome_email'}, kwargs['tags'])
        self.assertEqual(kwargs['unsubscribe_url'], 'https://u')
        self.assertEqual(kwargs['reply_to'], 'team@newcollab.co')

    def test_smtp_when_resend_not_configured(self):
        (ok, _), resend, smtp = _send(configured=False)
        self.assertTrue(ok)
        resend.assert_not_called()
        smtp.assert_called_once()

    def test_smtp_fallback_only_on_transient_error(self):
        (ok, _), _, smtp = _send({'success': False, 'error': '429', 'retryable': True})
        self.assertTrue(ok)
        smtp.assert_called_once()
        (ok, err), _, smtp = _send({'success': False, 'error': 'bad to', 'retryable': False})
        self.assertFalse(ok)
        self.assertEqual(err, 'bad to')
        smtp.assert_not_called()

    def test_waitlisted_allowed_when_asked(self):
        with patch.object(email_cron_routes, '_recipient_waitlisted', return_value=True) as waitlisted, \
                patch.object(email_cron_routes, 'render_email_template', return_value=('<p>Hi</p>', None)), \
                patch('services.resend_mail.resend_configured', return_value=True), \
                patch('services.resend_mail.send_resend_email', return_value={'success': True}):
            ok, _ = email_cron_routes.send_template_email('c@x.com', 'x.html', 'Hi', {}, allow_waitlisted=True)
        self.assertTrue(ok)
        waitlisted.assert_not_called()


class TestWaitlistEmails(unittest.TestCase):
    def test_approved_copy_is_polly_first(self):
        ctx = waitlist_emails.waitlist_email_context('approved', 'Ema')
        self.assertIn('Polly', ctx['subject'])
        self.assertIn('chip=line_up', ctx['action_url'])
        self.assertNotIn('browse', ctx['message'])

    def test_status_emails_reach_waitlisted_creators_with_tags(self):
        with patch('email_cron_routes.send_template_email', return_value=(True, None)) as send:
            self.assertTrue(waitlist_emails.send_waitlist_email('joined', 'c@x.com', 'Ema', user_id=1))
        kwargs = send.call_args.kwargs
        self.assertTrue(kwargs['allow_waitlisted'])
        self.assertIn({'name': 'template', 'value': 'waitlist_joined'}, kwargs['tags'])


if __name__ == '__main__':
    unittest.main()
