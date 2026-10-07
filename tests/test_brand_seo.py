import unittest
from datetime import datetime

from services.brand_seo import (
    example_posts,
    is_indexable,
    is_low_quality_slug,
    public_seo_fields,
    social_profile,
)


def _brand(**overrides):
    row = {
        'slug': 'olipop',
        'brand_name': 'OLIPOP',
        'status': 'published',
        'logo_url': 'https://cdn.example/logo.png',
        'description': 'Prebiotic soda brand offering gut-healthy, low-sugar sparkling beverages.',
        'hero_product': 'OLIPOP Prebiotic Soda',
        'target_audience': 'health-conscious consumers',
        'price_point': 20,
        'avg_product_value': 50,
        'collaboration_type': 'gifted',
        'regions': ['US'],
        'instagram_handle': 'drinkolipop',
        'application_method': 'EMAIL_PITCH',
        'min_followers': 500,
    }
    row.update(overrides)
    return row


class BrandSeoTests(unittest.TestCase):
    def test_rich_brand_is_indexable(self):
        self.assertTrue(is_indexable(_brand()))

    def test_unpublished_or_logo_less_brand_is_not_indexable(self):
        self.assertFalse(is_indexable(_brand(status='draft')))
        self.assertFalse(is_indexable(_brand(logo_url=None)))

    def test_short_description_is_not_indexable(self):
        self.assertFalse(is_indexable(_brand(description='Soda.')))

    def test_brand_with_few_facts_is_not_indexable(self):
        bare = _brand(hero_product=None, target_audience=None, price_point=None,
                      avg_product_value=None, collaboration_type=None, regions=None,
                      instagram_handle=None, application_method=None, min_followers=0)
        self.assertFalse(is_indexable(bare))

    def test_scraped_slugs_are_low_quality(self):
        self.assertTrue(is_low_quality_slug('maje-site-officiel', 'Maje'))
        self.assertTrue(is_low_quality_slug('maje-site-officiel', 'Maje Site Officiel'))
        self.assertTrue(is_low_quality_slug('shop-best-serums', 'Serum Co'))
        self.assertFalse(is_low_quality_slug('olipop', 'OLIPOP'))
        self.assertFalse(is_low_quality_slug('the-body-shop', 'The Body Shop'))

    def test_example_posts_parse_python_repr_and_skip_bad_urls(self):
        raw = ("[{'url': 'https://www.tiktok.com/@a/video/1', 'title': 'Hello  world …', "
               "'platform': 'TikTok', 'handle': '@a'}, {'url': 'javascript:alert(1)'}]")
        self.assertEqual(example_posts(raw), [{
            'url': 'https://www.tiktok.com/@a/video/1',
            'title': 'Hello world',
            'platform': 'tiktok',
            'handle': '@a',
        }])

    def test_social_profile_needs_a_real_audience(self):
        self.assertIsNone(social_profile({'handle': 'x', 'followers': 1}))
        profile = social_profile('{"handle": "drinkolipop", "followers": 588600, "platform": "tiktok", "verified": true}')
        self.assertEqual(profile['followers'], 588600)
        self.assertTrue(profile['verified'])

    def test_public_fields_fall_back_to_estimated_value(self):
        fields = public_seo_fields(_brand(avg_product_value=None, updated_at=datetime(2026, 9, 1)),
                                   estimated_value=75)
        self.assertEqual(fields['estimatedValue'], 75)
        self.assertEqual(fields['updatedAt'], '2026-09-01T00:00:00')
        self.assertTrue(fields['indexable'])
        self.assertNotIn('contact_email', fields)


if __name__ == '__main__':
    unittest.main()
