import unittest

from services.subscription_retention import (
    dunning_already_sent,
    increment_replies_received,
    invoice_subscription_id,
    offer_for_reason,
    qualifies_for_winback_coupon,
    should_downgrade_on_status,
    should_send_dunning,
    stripe_checkout_customer_kwargs,
    stripe_checkout_promo_kwargs,
    subscription_period_end_ts,
    subscription_price_snapshot,
)


class SubscriptionRetentionTests(unittest.TestCase):
    def test_dunning_on_first_and_third_attempt_only(self):
        self.assertTrue(should_send_dunning(1))
        self.assertTrue(should_send_dunning(3))
        self.assertFalse(should_send_dunning(2))
        self.assertFalse(should_send_dunning(4))
        self.assertFalse(should_send_dunning(None))

    def test_dunning_already_sent_reads_invoice_metadata(self):
        invoice = {"metadata": {"dunning_attempt_1": "1"}}
        self.assertTrue(dunning_already_sent(invoice, 1))
        self.assertFalse(dunning_already_sent(invoice, 3))
        self.assertFalse(dunning_already_sent({}, 1))

    def test_invoice_subscription_id_legacy_and_nested(self):
        self.assertEqual(invoice_subscription_id({"subscription": "sub_abc"}), "sub_abc")
        self.assertEqual(
            invoice_subscription_id(
                {"parent": {"subscription_details": {"subscription": "sub_nested"}}}
            ),
            "sub_nested",
        )
        self.assertEqual(
            invoice_subscription_id({"subscription": {"id": "sub_obj"}}),
            "sub_obj",
        )
        self.assertIsNone(invoice_subscription_id({}))

    def test_keep_pro_while_past_due_or_cancel_scheduled(self):
        self.assertFalse(should_downgrade_on_status("past_due"))
        self.assertFalse(should_downgrade_on_status("active", cancel_at_period_end=True))
        self.assertFalse(should_downgrade_on_status("trialing"))
        self.assertTrue(should_downgrade_on_status("canceled"))
        self.assertTrue(should_downgrade_on_status("unpaid"))
        self.assertTrue(should_downgrade_on_status("incomplete_expired"))

    def test_period_end_from_item_when_top_level_missing(self):
        sub = {"items": {"data": [{"current_period_end": 1780000000}]}}
        self.assertEqual(subscription_period_end_ts(sub), 1780000000)
        self.assertEqual(subscription_period_end_ts({"current_period_end": 10}), 10)

    def test_increment_replies_only_on_first_reply_stage(self):
        calls = []

        class Cursor:
            rowcount = 1

            def execute(self, sql, params=None):
                calls.append((sql, params))

        cursor = Cursor()
        self.assertTrue(increment_replies_received(cursor, 7, "pitched", "replied"))
        self.assertFalse(increment_replies_received(cursor, 7, "replied", "won"))
        self.assertFalse(increment_replies_received(cursor, 7, "pitched", "archived"))
        self.assertTrue(increment_replies_received(cursor, 7, "waiting", "won"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], (7,))

    def test_offer_for_no_replies_is_talent_manager(self):
        self.assertEqual(offer_for_reason("no_replies"), "talent_manager")

    def test_offer_for_too_expensive_is_price_hold_on_19(self):
        self.assertEqual(offer_for_reason("too_expensive", 1900, "month"), "price_hold")
        self.assertEqual(offer_for_reason("too_expensive", None, "month"), "price_hold")
        self.assertIsNone(offer_for_reason("too_expensive", 1200, "month"))
        self.assertIsNone(offer_for_reason("too_expensive", 4900, "month"))
        self.assertIsNone(offer_for_reason("too_expensive", 1900, "year"))
        self.assertIsNone(offer_for_reason("unused"))
        self.assertIsNone(offer_for_reason("other"))

    def test_price_snapshot_from_first_item(self):
        amount, interval = subscription_price_snapshot(
            {
                "items": {
                    "data": [
                        {
                            "price": {
                                "unit_amount": 1900,
                                "recurring": {"interval": "month"},
                            }
                        }
                    ]
                }
            }
        )
        self.assertEqual(amount, 1900)
        self.assertEqual(interval, "month")

    def test_winback_coupon_only_for_canceled_pro(self):
        canceled = {
            "subscription_tier": "free",
            "subscription_status": "canceled",
            "stripe_subscription_id": "sub_123",
            "stripe_customer_id": "cus_123",
            "email": "a@b.com",
        }
        self.assertTrue(qualifies_for_winback_coupon(canceled))
        self.assertFalse(
            qualifies_for_winback_coupon({**canceled, "subscription_tier": "pro", "subscription_status": "active"})
        )
        self.assertFalse(
            qualifies_for_winback_coupon({**canceled, "stripe_subscription_id": None})
        )
        self.assertFalse(
            qualifies_for_winback_coupon(
                {
                    "subscription_tier": "free",
                    "subscription_status": "free",
                    "stripe_subscription_id": None,
                    "email": "never@paid.com",
                }
            )
        )
        self.assertEqual(
            stripe_checkout_promo_kwargs("winback", canceled, "pro_retention_12_3mo"),
            {"discounts": [{"coupon": "pro_retention_12_3mo"}]},
        )
        self.assertEqual(
            stripe_checkout_promo_kwargs("winback", {**canceled, "subscription_tier": "pro"}, "pro_retention_12_3mo"),
            {"allow_promotion_codes": True},
        )
        self.assertEqual(
            stripe_checkout_promo_kwargs(None, canceled, "pro_retention_12_3mo"),
            {"allow_promotion_codes": True},
        )
        self.assertEqual(
            stripe_checkout_customer_kwargs("winback", canceled),
            {"customer": "cus_123"},
        )
        self.assertEqual(
            stripe_checkout_customer_kwargs(None, canceled),
            {"customer_email": "a@b.com"},
        )


if __name__ == "__main__":
    unittest.main()
