"""Brand-aware For You scoring: differentiated scores, intent vs scrape, skips."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.fit_score_calculator import (
    score_brand_for_creator,
    scrape_agrees_with_intent,
    check_brand_context_mismatch,
)
from services.mentor_matchmaker import diversify_matches, _prefilter_candidates


def _yasia_profile(**overrides):
    profile = {
        'primary_niche': 'beauty',
        'secondary_niches': ['wellness', 'lifestyle', 'fashion'],
        'content_themes': ['product showcasing', 'fashion styling', 'fragrance reviews'],
        'raw_bio': 'UGC creator sharing wellness, lifestyle and self-care',
        'engagement_rate': 1.3,
        'posting_cadence_per_week': 3,
        'follower_count': 7001,
        'has_collab_email': True,
        'match_intent_lanes': ['beauty', 'wellness', 'lifestyle'],
        'match_proof_lanes': ['fashion'],
        'aesthetic_descriptors': ['clean', 'minimal'],
    }
    profile.update(overrides)
    return profile


def _brand(brand_id, name, category, description, **extra):
    row = {
        'id': brand_id,
        'name': name,
        'brand_name': name,
        'category': category,
        'description': description,
        'hero_product': extra.pop('hero_product', ''),
        'min_followers': extra.pop('min_followers', 1000),
        'micro_friendly': extra.pop('micro_friendly', True),
        'has_email_contact': extra.pop('has_email_contact', True),
        'contact_email': extra.pop('contact_email', 'pr@brand.com'),
    }
    row.update(extra)
    return row


class TestScrapeIntentAgreement(unittest.TestCase):
    def test_fashion_scrape_conflicts_with_beauty_wellness(self):
        self.assertFalse(
            scrape_agrees_with_intent('fashion', ['beauty', 'wellness', 'lifestyle'])
        )

    def test_beauty_scrape_agrees_with_beauty_parenting(self):
        self.assertTrue(
            scrape_agrees_with_intent('beauty', ['beauty', 'parenting'])
        )

    def test_skincare_agrees_with_beauty(self):
        self.assertTrue(scrape_agrees_with_intent('skincare', ['beauty']))


class TestBrandAwareScores(unittest.TestCase):
    def setUp(self):
        self.profile = _yasia_profile()
        self.interests = ['beauty', 'wellness', 'lifestyle']
        self.tarte = _brand(
            104,
            'Tarte Cosmetics',
            'beauty',
            'Clean makeup and Amazonian clay foundation with Shape Tape concealer.',
            hero_product='Shape Tape Concealer',
        )
        self.eydology = _brand(
            201,
            'Eydology',
            'skincare',
            'Premium eye care and prescription glasses / optical frames.',
            hero_product='Prescription Glasses Collection',
        )
        self.silk = _brand(
            202,
            'Silked',
            'haircare',
            'Silk pillowcases and hair wraps for overnight hair care.',
            hero_product='Silk Pillowcase',
        )
        self.wellness = _brand(
            203,
            'Norse Organics',
            'wellness',
            'Adaptogen wellness rituals and self-care supplements for daily balance.',
            hero_product='Adaptogen Blend',
        )

    def _score(self, brand):
        return score_brand_for_creator(self.profile, brand, interest_niches=self.interests)

    def test_optical_brand_is_not_a_good_match(self):
        fit = self._score(self.eydology)
        self.assertLess(fit['overall_score'], 35)
        mismatch, _ = check_brand_context_mismatch(self.profile, self.eydology)
        self.assertTrue(mismatch)

    def test_makeup_brand_outscores_optical(self):
        tarte = self._score(self.tarte)
        eye = self._score(self.eydology)
        self.assertGreater(tarte['overall_score'], eye['overall_score'])
        self.assertGreaterEqual(tarte['overall_score'], 55)

    def test_same_category_brands_are_not_identical(self):
        tarte = self._score(self.tarte)
        silk = self._score(self.silk)
        self.assertNotEqual(tarte['overall_score'], silk['overall_score'])

    def test_scores_spread_across_a_shortlist(self):
        scores = [
            self._score(b)['overall_score']
            for b in (self.tarte, self.silk, self.wellness, self.eydology)
        ]
        unique = {s for s in scores}
        self.assertGreaterEqual(len(unique), 3)


class TestDiversifyAndPrefilter(unittest.TestCase):
    def test_diversify_caps_one_category(self):
        brands = [
            {'id': i, 'name': f'Skincare {i}', 'category': 'skincare', 'match_score': 80 - i}
            for i in range(10)
        ]
        kept = diversify_matches(brands, limit=8, max_per_raw=2, max_per_family=4)
        self.assertLessEqual(len(kept), 4)

    def test_prefilter_drops_optical_and_spreads_scores(self):
        profile = _yasia_profile()
        brands = [
            _brand(1, 'Tarte Cosmetics', 'beauty', 'Shape Tape concealer and Amazonian clay foundation.', hero_product='Shape Tape Concealer'),
            _brand(2, 'Eydology', 'skincare', 'Premium eye care and prescription glasses.', hero_product='Prescription Glasses Collection'),
            _brand(3, 'Glow Serum Co', 'skincare', 'Vitamin C serum and daily glow moisturizer for skin.', hero_product='Vitamin C Serum'),
            _brand(4, 'Calm Rituals', 'wellness', 'Self-care wellness rituals and adaptogen blends.', hero_product='Adaptogen Blend'),
            _brand(5, 'Kitsch Hair', 'haircare', 'Satin scrunchies and heatless hair curlers.', hero_product='Satin Scrunchie'),
            _brand(6, 'Deweffect', 'skincare', 'Dewy skin mist and hydrating facial spray.', hero_product='Dew Mist'),
        ]
        kept = _prefilter_candidates(profile, brands, ['beauty', 'wellness', 'lifestyle'])
        names = {b.get('name') for b in kept}
        self.assertNotIn('Eydology', names)
        scores = [b['match_score'] for b in kept]
        self.assertTrue(all(s >= 35 for s in scores))
        if len(scores) >= 2:
            self.assertGreater(max(scores) - min(scores), 0)


class TestPrepareForYouProfile(unittest.TestCase):
    def test_fashion_scrape_yields_to_beauty_interests(self):
        from pr_crm_routes import (
            _prepare_for_you_profile,
            _build_for_you_category_pool,
            _for_you_should_skip_brand,
        )

        scrape = {
            'primary_niche': 'fashion',
            'primary_niche_confidence': 80,
            'secondary_niches': ['beauty', 'ugc_creator', 'wellness', 'lifestyle'],
            'content_themes': ['product showcasing', 'fashion styling', 'fragrance reviews'],
            'raw_bio': 'wellness lifestyle self-care UGC',
            'follower_count': 7001,
            'engagement_rate': 1.3,
        }
        interests = ['Beauty', 'Wellness', 'Lifestyle']
        prep = _prepare_for_you_profile(scrape, interests, followers=7001)
        self.assertEqual(prep['primary_niche'], 'beauty')
        self.assertIn('fashion', [str(s).lower() for s in prep['secondary_niches']])
        self.assertIn('beauty', prep.get('match_intent_lanes') or [])

        pool = {c.lower() for c in _build_for_you_category_pool(scrape, interests)}
        self.assertIn('beauty', pool)
        self.assertIn('skincare', pool)
        self.assertNotIn('food', pool)
        self.assertNotIn('beverages', pool)

        self.assertTrue(_for_you_should_skip_brand(
            {'name': 'Eydology', 'description': 'prescription glasses', 'category': 'skincare'},
            interests,
            prep,
        ))
        self.assertFalse(_for_you_should_skip_brand(
            {'name': 'Tarte Cosmetics', 'description': 'Shape Tape concealer', 'category': 'beauty'},
            interests,
            prep,
        ))


class TestBeautyBabyParentingFeed(unittest.TestCase):
    def test_lifestyle_scrape_does_not_become_skincare(self):
        from pr_crm_routes import _prepare_for_you_profile

        scrape = {
            'primary_niche': 'lifestyle',
            'primary_niche_confidence': 85,
            'secondary_niches': ['home decor', 'organization', 'family', 'cooking'],
            'content_themes': ['skincare routine', 'mom finds', 'product showcasing'],
            'raw_bio': 'Busy mom sharing baby and beauty finds',
            'follower_count': 12000,
            'engagement_rate': 2.1,
        }
        prep = _prepare_for_you_profile(scrape, ['beauty', 'baby'], followers=12000)
        self.assertEqual(prep['primary_niche'], 'beauty')
        self.assertIn('baby', [str(s).lower() for s in prep['secondary_niches']])
        self.assertNotIn('cooking', [str(s).lower() for s in prep['secondary_niches']])
        self.assertNotIn('organization', [str(s).lower() for s in prep['secondary_niches']])

    def test_dual_intent_reserves_parenting_seats(self):
        brands = []
        for i in range(6):
            brands.append({
                'id': i,
                'name': f'Skincare {i}',
                'category': 'skincare',
                'match_score': 90 - i,
            })
        brands.append({
            'id': 50, 'name': 'Duradry', 'category': 'wellness', 'match_score': 88,
        })
        brands.append({
            'id': 51, 'name': 'Shield Your Body', 'category': 'wellness', 'match_score': 86,
        })
        for i in range(3):
            brands.append({
                'id': 80 + i,
                'name': f'Baby Brand {i}',
                'category': 'baby',
                'match_score': 62 - i,
            })
        kept = diversify_matches(
            brands, limit=8, max_per_raw=2, max_per_family=5,
            interest_niches=['beauty', 'baby'],
        )
        cats = [b['category'] for b in kept]
        self.assertGreaterEqual(cats.count('baby'), 2)
        names = {b['name'] for b in kept}
        self.assertIn('Baby Brand 0', names)

    def test_deodorant_loses_to_baby_brand(self):
        profile = {
            'primary_niche': 'beauty',
            'secondary_niches': ['baby'],
            'content_themes': ['mom finds', 'skincare routine', 'baby products'],
            'raw_bio': 'Busy mom sharing baby and beauty finds',
            'engagement_rate': 2.1,
            'posting_cadence_per_week': 3,
            'follower_count': 12000,
            'has_collab_email': True,
            'match_intent_lanes': ['beauty', 'baby'],
        }
        interests = ['beauty', 'baby']
        baby = _brand(
            1, 'Ergobaby', 'baby',
            'Baby carriers and newborn essentials for everyday parenting.',
            hero_product='Omni Baby Carrier',
        )
        deodorant = _brand(
            2, 'Duradry', 'wellness',
            'Clinical deodorant and antiperspirant for sweat control.',
            hero_product='Duradry AM Deodorant',
        )
        baby_fit = score_brand_for_creator(profile, baby, interest_niches=interests)
        deo_fit = score_brand_for_creator(profile, deodorant, interest_niches=interests)
        self.assertGreater(baby_fit['overall_score'], deo_fit['overall_score'])
        self.assertGreaterEqual(baby_fit['overall_score'], 35)

    def test_beauty_scores_are_not_capped_identical(self):
        profile = {
            'primary_niche': 'beauty',
            'secondary_niches': ['baby'],
            'content_themes': ['mom finds', 'skincare routine'],
            'raw_bio': 'Busy mom sharing baby and beauty finds',
            'engagement_rate': 2.1,
            'posting_cadence_per_week': 3,
            'follower_count': 12000,
            'match_intent_lanes': ['beauty', 'baby'],
        }
        interests = ['beauty', 'baby']
        brands = [
            _brand(1, 'Starface', 'skincare', 'Hydrocolloid pimple patches for breakouts.', hero_product='Hydrocolloid Patches'),
            _brand(2, 'Fancii', 'beauty', 'LED makeup mirrors and beauty tools.', hero_product='LED Makeup Mirror'),
            _brand(3, 'Briogeo', 'haircare', 'Clean haircare for scalp and curls.', hero_product='Scalp Revival Shampoo'),
            _brand(4, 'BEB Organic', 'Baby & Parenting', 'Organic baby skincare for newborns and postpartum.', hero_product='Baby Nourishing Cream'),
        ]
        scored = [
            (b['name'], score_brand_for_creator(profile, b, interest_niches=interests)['overall_score'])
            for b in brands
        ]
        scores = [s for _, s in scored]
        self.assertLess(max(scores), 90)
        self.assertGreaterEqual(max(scores) - min(scores), 6)
        by_name = dict(scored)
        self.assertGreaterEqual(by_name['BEB Organic'], 50)
        self.assertNotEqual(by_name['Starface'], by_name['Fancii'])


if __name__ == '__main__':
    unittest.main()
