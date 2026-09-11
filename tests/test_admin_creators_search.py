# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("flask", MagicMock())
sys.modules.setdefault("psycopg2", MagicMock())
sys.modules.setdefault("psycopg2.extras", MagicMock())

from routes.admin_creators import (
    _build_where_clause,
    _region_needles,
    _resolve_sort,
    normalize_search_token,
)


class TestNormalizeSearchToken(unittest.TestCase):
    def test_strips_at_prefix(self):
        self.assertEqual(normalize_search_token('@growglowandflow'), 'growglowandflow')

    def test_keeps_email(self):
        self.assertEqual(
            normalize_search_token('sara@gmail.com'),
            'sara@gmail.com',
        )

    def test_extracts_instagram_url(self):
        self.assertEqual(
            normalize_search_token('https://www.instagram.com/saraskye/'),
            'saraskye',
        )

    def test_extracts_tiktok_url(self):
        self.assertEqual(
            normalize_search_token('https://tiktok.com/@cdcparis'),
            'cdcparis',
        )


class TestCreatorDirectoryFilters(unittest.TestCase):
    def test_handle_search_hits_username_and_social(self):
        sql, params, token = _build_where_clause({'q': '@GrowGlow'})
        self.assertEqual(token, 'GrowGlow')
        self.assertIn('c.social_handle', sql)
        self.assertIn('c.kit_slug', sql)
        self.assertIn("BTRIM(COALESCE(c.username, ''), '@')", sql)
        self.assertNotIn('BOTH', sql)
        self.assertTrue(any(p == 'growglow' for p in params))
        self.assertFalse(any(p == '%@GrowGlow%' for p in params))

    def test_us_region_does_not_substring_match_australia(self):
        sql, params, _ = _build_where_clause({'region': 'US'})
        self.assertTrue(any(p == '%"us"%' for p in params))
        self.assertFalse(any(p == '%US%' for p in params))
        self.assertFalse(any(p == '%us%' for p in params))

    def test_uk_region_includes_gb_alias(self):
        needles = _region_needles('UK')
        self.assertIn('UK', needles)
        self.assertIn('GB', needles)

    def test_wellness_niche_matches_slug_not_only_label(self):
        sql, params, _ = _build_where_clause({'niche': 'Wellness'})
        blob = ' '.join(str(p) for p in params)
        self.assertIn('wellness', blob)
        self.assertIn('c.niche', sql)

    def test_follower_bounds(self):
        sql, params, _ = _build_where_clause({'min_followers': '1000', 'max_followers': '4999'})
        self.assertIn('GREATEST', sql)
        self.assertIn(1000, params)
        self.assertIn(4999, params)

    def test_exact_handle_ranks_first(self):
        order_sql, rank_params = _resolve_sort({'sort': 'signup'}, search_token='saraskye')
        self.assertTrue(order_sql.startswith('CASE'))
        self.assertIn('saraskye', rank_params)


if __name__ == '__main__':
    unittest.main()
