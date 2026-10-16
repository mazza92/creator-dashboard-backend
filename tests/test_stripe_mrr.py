import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from utils.stripe_mrr import fetch_stripe_mrr


def _sub(price_id, amount, interval):
    return {"items": {"data": [{"price": {"id": price_id, "unit_amount": amount,
                                          "recurring": {"interval": interval}}, "quantity": 1}]}}


class TestStripeMrr(unittest.TestCase):
    def _run(self, subs):
        fake = types.ModuleType("stripe")
        fake.Subscription = MagicMock()
        fake.Subscription.list.return_value.auto_paging_iter.return_value = subs
        env = {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_PRICE_ID_PRO": "price_month",
               "STRIPE_PRICE_ID_PRO_ANNUAL": "price_year"}
        with patch.dict(sys.modules, {"stripe": fake}), patch.dict("os.environ", env):
            return fetch_stripe_mrr()

    def test_yearly_counts_as_a_twelfth(self):
        out = self._run([_sub("price_month", 1900, "month"), _sub("price_year", 15200, "year")])
        self.assertEqual(out["active_subscriptions"], 2)
        self.assertEqual(out["mrr_dollars"], 31.66)

    def test_other_products_are_ignored(self):
        out = self._run([_sub("price_month", 1900, "month"), _sub("price_brand", 29900, "month")])
        self.assertEqual(out["active_subscriptions"], 1)
        self.assertEqual(out["mrr_dollars"], 19.0)
