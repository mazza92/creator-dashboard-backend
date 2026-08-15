"""Creator campaigns send through Resend, not Gmail SMTP."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.resend_mail import (
    campaign_from_header,
    extract_unsubscribe_url,
    send_resend_email,
)


class TestResendMail(unittest.TestCase):
    def test_from_header_defaults_to_team(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(campaign_from_header(), 'Newcollab <team@newcollab.co>')

    def test_extracts_unsubscribe_link(self):
        html = '<a href="https://api.newcollab.co/api/public/unsubscribe?uid=1&token=abc">Unsubscribe</a>'
        self.assertEqual(
            extract_unsubscribe_url(html),
            'https://api.newcollab.co/api/public/unsubscribe?uid=1&token=abc',
        )

    def test_missing_api_key_is_not_retryable(self):
        with patch.dict('os.environ', {}, clear=True):
            result = send_resend_email('a@b.com', 'Hi', '<p>Hi</p>')
        self.assertFalse(result['success'])
        self.assertFalse(result['retryable'])
        self.assertIn('RESEND_API_KEY', result['error'])

    @patch('services.resend_mail.requests.post')
    def test_sends_payload_with_unsubscribe_headers(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text='{"id":"re_1"}')
        mock_post.return_value.json.return_value = {'id': 're_1'}
        html = '<a href="https://api.newcollab.co/api/public/unsubscribe?uid=9&token=z">Unsub</a>'

        with patch.dict('os.environ', {'RESEND_API_KEY': 're_test'}, clear=True):
            result = send_resend_email('creator@example.com', 'Weekly roundup', html)

        self.assertTrue(result['success'])
        self.assertEqual(result['message_id'], 're_1')
        kwargs = mock_post.call_args.kwargs
        payload = kwargs['json']
        self.assertEqual(payload['to'], ['creator@example.com'])
        self.assertIn('List-Unsubscribe', payload['headers'])
        self.assertIn('unsubscribe?uid=9', payload['headers']['List-Unsubscribe'])

    @patch('services.resend_mail.requests.post')
    def test_rate_limit_is_retryable(self, mock_post):
        mock_post.return_value = MagicMock(status_code=429, text='Too many')
        mock_post.return_value.json.return_value = {'message': 'Rate limit exceeded'}
        with patch.dict('os.environ', {'RESEND_API_KEY': 're_test'}, clear=True):
            result = send_resend_email('creator@example.com', 'Hi', '<p>Hi</p>')
        self.assertFalse(result['success'])
        self.assertTrue(result['retryable'])


if __name__ == '__main__':
    unittest.main()
