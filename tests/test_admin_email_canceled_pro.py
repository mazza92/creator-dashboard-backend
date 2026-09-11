import unittest

from routes.admin_email import _canceled_pro_sql


class CanceledProSegmentTests(unittest.TestCase):
    def test_sql_only_matches_canceled_stripe_customers(self):
        sql = _canceled_pro_sql()
        self.assertIn("subscription_status", sql)
        self.assertIn("canceled", sql)
        self.assertIn("stripe_subscription_id IS NOT NULL", sql)
        self.assertIn("subscription_tier", sql)
        self.assertNotIn("unsubscribed_at", sql)


if __name__ == "__main__":
    unittest.main()
