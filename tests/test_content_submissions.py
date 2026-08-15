"""Brand Content Hub payload validation (no DB)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_submission_routes import parse_post_url, validate_submission_payload


class TestParsePostUrl(unittest.TestCase):
    def test_tiktok(self):
        parsed, err = parse_post_url('https://www.tiktok.com/@you/video/123')
        self.assertIsNone(err)
        self.assertEqual(parsed['platform'], 'tiktok')
        self.assertTrue(parsed['url'].startswith('https://'))

    def test_instagram(self):
        parsed, err = parse_post_url('https://www.instagram.com/reel/AbC/')
        self.assertIsNone(err)
        self.assertEqual(parsed['platform'], 'instagram')

    def test_youtube_shorts(self):
        parsed, err = parse_post_url('https://www.youtube.com/shorts/abc123')
        self.assertIsNone(err)
        self.assertEqual(parsed['platform'], 'youtube')

    def test_youtu_be(self):
        parsed, err = parse_post_url('https://youtu.be/abc123')
        self.assertIsNone(err)
        self.assertEqual(parsed['platform'], 'youtube')

    def test_rejects_http(self):
        parsed, err = parse_post_url('http://www.tiktok.com/@you/video/123')
        self.assertIsNone(parsed)
        self.assertIn('https://', err)

    def test_rejects_other_domain(self):
        parsed, err = parse_post_url('https://example.com/video/1')
        self.assertIsNone(parsed)
        self.assertIn('TikTok', err)


class TestValidatePayload(unittest.TestCase):
    def _base(self, **overrides):
        data = {
            'post_url': 'https://www.tiktok.com/@you/video/123',
            'brand_id': 10,
            'content_type': 'unboxing',
            'consent_given': True,
        }
        data.update(overrides)
        return data

    def test_ok_directory_brand(self):
        payload, errors = validate_submission_payload(self._base())
        self.assertEqual(errors, [])
        self.assertEqual(payload['brand_id'], 10)
        self.assertEqual(payload['content_type'], 'unboxing')

    def test_ok_freetext_brand(self):
        payload, errors = validate_submission_payload(self._base(
            brand_id=None, brand_name_freetext='Glow Co',
        ))
        self.assertEqual(errors, [])
        self.assertIsNone(payload['brand_id'])
        self.assertEqual(payload['brand_name_freetext'], 'Glow Co')

    def test_requires_brand(self):
        _, errors = validate_submission_payload(self._base(brand_id=None))
        self.assertTrue(any('brand' in e.lower() for e in errors))

    def test_requires_consent(self):
        _, errors = validate_submission_payload(self._base(consent_given=False))
        self.assertTrue(any('consent' in e.lower() for e in errors))

    def test_description_cap(self):
        _, errors = validate_submission_payload(self._base(description='x' * 201))
        self.assertTrue(any('200' in e for e in errors))

    def test_freetext_cap(self):
        _, errors = validate_submission_payload(self._base(
            brand_id=None, brand_name_freetext='b' * 101,
        ))
        self.assertTrue(any('100' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
