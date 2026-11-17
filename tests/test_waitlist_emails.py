import unittest

from waitlist_emails import waitlist_email_context


class TestWaitlistEmails(unittest.TestCase):
    def test_joined_points_at_waitlist(self):
        ctx = waitlist_email_context('joined', 'cdcparis')
        self.assertIn('waitlist', ctx['subject'].lower())
        self.assertTrue(ctx['action_url'].endswith('/creator/waitlist'))
        self.assertIn('cdcparis', ctx['message'])

    def test_approved_without_picks_points_at_for_you(self):
        ctx = waitlist_email_context('approved', 'Test')
        self.assertTrue(ctx['action_url'].endswith('/creator/dashboard/for-you'))
        self.assertEqual(ctx['template'], 'approved_email.html')
        self.assertEqual(ctx['picks'], [])
        self.assertIn('3 free pitches', ctx['fine_print'])

    def test_approved_with_picks_links_the_top_pick(self):
        picks = {'brands': [{'brand_id': 42, 'brand_name': 'Glow & Co', 'logo': None, 'initials': 'GC',
                             'color': '#1f2937'}], 'niche': 'skincare'}
        ctx = waitlist_email_context('approved', 'Ana', picks=picks, extra=picks)
        # pitch_brand + brand_id: the frontend shows the first-run welcome with this brand leading it
        self.assertIn('/creator/dashboard/for-you?chip=pitch_brand&brand_id=42&brand_name=Glow+%26+Co', ctx['action_url'])
        self.assertIn('skincare', ctx['picks_note'])
        self.assertIn('skincare', ctx['preheader'])

    def test_pro_copy_is_distinct(self):
        ctx = waitlist_email_context('approved', 'Test', as_pro=True)
        self.assertIn('Pro unlocked', ctx['message'])
        self.assertEqual(ctx['badge'], 'Pro unlocked')
        self.assertIn('20 pitches a day', ctx['fine_print'])

    def test_approved_template_renders(self):
        from email_cron_routes import render_email_template
        picks = {'brands': [{'brand_id': 1, 'brand_name': 'Alpha', 'logo': {'url': 'https://x.test/a.png', 'width': 90,
                                                                       'height': 30}, 'initials': 'AL',
                             'color': '#1f2937'},
                            {'brand_id': 2, 'brand_name': 'Beta', 'logo': None, 'initials': 'BE', 'color': '#b4436c'}]}
        ctx = waitlist_email_context('approved', '<b>Ana</b>', picks=picks, extra=picks)
        html, _ = render_email_template('a@example.com', 'approved_email.html', ctx)
        self.assertIn("You're in, &lt;b&gt;Ana&lt;/b&gt;!", html)
        self.assertIn('src="https://x.test/a.png" alt="" width="90" height="30"', html)
        self.assertIn('>BE<', html)
        self.assertIn('Send my first pitch', html)
        bare, _ = render_email_template('a@example.com', 'approved_email.html', waitlist_email_context('approved', 'Ana'))
        self.assertNotIn("first picks", bare)

    def test_rejection_escapes_html(self):
        ctx = waitlist_email_context('rejected', 'x', reason='<script>alert(1)</script>')
        self.assertNotIn('<script>', ctx['message'])
        self.assertIn('&lt;script&gt;', ctx['message'])


class TestEmailLogo(unittest.TestCase):
    @staticmethod
    def _png(w, h):
        import struct
        return b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + struct.pack('>II', w, h) + b'\x08\x06\x00\x00\x00'

    def _resp(self, status=200, ctype='image/png', body=None):
        from unittest.mock import MagicMock
        r = MagicMock(status_code=status)
        r.headers = {'Content-Type': ctype}
        r.iter_content.return_value = [body if body is not None else self._png(400, 120)]
        return r

    def test_logo_fit_never_upscales(self):
        from waitlist_emails import logo_fit
        self.assertEqual(logo_fit((400, 120)), (96, 29))   # wide wordmark: fits the width
        self.assertEqual(logo_fit((300, 300)), (36, 36))   # square: fits the height
        self.assertEqual(logo_fit((60, 30)), (60, 30))     # small: natural size, not stretched
        self.assertIsNone(logo_fit((32, 16)))              # too small to read
        self.assertIsNone(logo_fit(None))

    def test_logo_checks(self):
        from unittest.mock import patch
        from waitlist_emails import email_logo
        self.assertIsNone(email_logo(None))
        self.assertIsNone(email_logo('http://x.test/a.png'))
        self.assertIsNone(email_logo('https://x.test/logo.svg?v=1'))
        with patch('requests.get', return_value=self._resp()):
            self.assertEqual(email_logo('https://x.test/a.png'), {'url': 'https://x.test/a.png', 'width': 96, 'height': 29})
        for bad in (self._resp(status=404), self._resp(ctype='text/html'), self._resp(ctype='image/svg+xml'),
                    self._resp(body=self._png(20, 20)), self._resp(body=b'not an image')):
            with patch('requests.get', return_value=bad):
                self.assertIsNone(email_logo('https://x.test/a.png'))
        with patch('requests.get', side_effect=TimeoutError()):
            self.assertIsNone(email_logo('https://x.test/a.png'))

    def test_no_header_logo_and_initials_fallback(self):
        from email_cron_routes import render_email_template
        picks = {'brands': [{'brand_id': 1, 'brand_name': 'Rare Beauty', 'logo': None, 'initials': 'RB',
                             'color': '#1f2937'}]}
        html, _ = render_email_template('a@example.com', 'approved_email.html',
                                        waitlist_email_context('approved', 'Ana', picks=picks, extra=picks))
        self.assertNotIn('newcollab-logo', html)
        self.assertIn('>RB<', html)
        self.assertNotIn('<img', html)


if __name__ == '__main__':
    unittest.main()
