import unittest

from waitlist_emails import waitlist_email_context


class TestWaitlistEmails(unittest.TestCase):
    def test_joined_points_at_waitlist(self):
        ctx = waitlist_email_context('joined', 'cdcparis')
        self.assertIn('waitlist', ctx['subject'].lower())
        self.assertTrue(ctx['action_url'].endswith('/creator/waitlist'))
        self.assertIn('cdcparis', ctx['message'])

    def test_approved_points_at_for_you(self):
        ctx = waitlist_email_context('approved', 'Test')
        self.assertTrue(ctx['action_url'].endswith('/creator/dashboard/for-you'))
        self.assertIn('approved', ctx['message'].lower())

    def test_pro_copy_is_distinct(self):
        ctx = waitlist_email_context('approved', 'Test', as_pro=True)
        self.assertIn('Pro unlocked', ctx['message'])

    def test_rejection_escapes_html(self):
        ctx = waitlist_email_context('rejected', 'x', reason='<script>alert(1)</script>')
        self.assertNotIn('<script>', ctx['message'])
        self.assertIn('&lt;script&gt;', ctx['message'])


if __name__ == '__main__':
    unittest.main()
