import unittest

from routes.admin_email import _active_pro_sql, _canceled_pro_sql


class CanceledProSegmentTests(unittest.TestCase):
    def test_sql_only_matches_canceled_stripe_customers(self):
        sql = _canceled_pro_sql()
        self.assertIn("subscription_status", sql)
        self.assertIn("canceled", sql)
        self.assertIn("stripe_subscription_id IS NOT NULL", sql)
        self.assertIn("subscription_tier", sql)
        self.assertNotIn("unsubscribed_at", sql)


class ActiveProSegmentTests(unittest.TestCase):
    def test_sql_only_matches_live_pro_or_elite(self):
        sql = _active_pro_sql()
        self.assertIn("'pro'", sql)
        self.assertIn("'elite'", sql)
        self.assertIn("canceled", sql)
        self.assertNotIn("stripe_subscription_id", sql)
        self.assertNotIn("= 'free'", sql)


if __name__ == "__main__":
    unittest.main()
