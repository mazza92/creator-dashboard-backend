import unittest
from unittest.mock import MagicMock, patch


class TestCancelFlag(unittest.TestCase):
    def test_stripe_flag(self):
        from services.billing_access import stripe_cancel_scheduled
        self.assertFalse(stripe_cancel_scheduled({'cancel_at_period_end': False, 'cancel_at': None}))
        self.assertTrue(stripe_cancel_scheduled({'cancel_at_period_end': True}))
        self.assertTrue(stripe_cancel_scheduled({'cancel_at_period_end': False, 'cancel_at': 1797006587}))

    def _run(self, stored, stripe_sub=None):
        import subscription_routes as sr
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {'cancel_at_period_end': stored}
        creator = {'subscription_tier': 'pro', 'stripe_subscription_id': 'sub_1'}
        with patch('services.billing_access.ensure_columns'), \
                patch('services.billing_access.set_cancel_flag') as save, \
                patch.object(sr.stripe.Subscription, 'retrieve', return_value=stripe_sub or {}) as retrieve:
            flag = sr._cancel_flag(conn, 7, creator)
        return flag, save, retrieve

    def test_renewing_sub_is_not_cancelled(self):
        flag, save, retrieve = self._run(None, {'cancel_at_period_end': False, 'cancel_at': None})
        self.assertFalse(flag)
        retrieve.assert_called_once_with('sub_1')
        save.assert_called_once()

    def test_stored_flag_skips_stripe(self):
        flag, _save, retrieve = self._run(True)
        self.assertTrue(flag)
        retrieve.assert_not_called()

    def test_free_creator(self):
        import subscription_routes as sr
        self.assertFalse(sr._cancel_flag(MagicMock(), 7, {'subscription_tier': 'free'}))


if __name__ == '__main__':
    unittest.main()


class TestLocationSwap(unittest.TestCase):
    def test_location_starting_with_digits(self):
        from services.irresistible_pitch import apply_location_to_body
        body = "Hi team,\n\nNo fee. Just product + shipping to London."
        self.assertIn("shipping to 8000 Aarhus, Denmark.", apply_location_to_body(body, "8000 Aarhus, Denmark"))


class TestUnpaidCancel(unittest.TestCase):
    def test_cancels_now_and_voids_open_invoices(self):
        import subscription_routes as sr
        with patch.object(sr.stripe.Subscription, 'cancel') as cancel, \
                patch.object(sr.stripe.Invoice, 'list', side_effect=[{'data': [{'id': 'in_1'}]}, {'data': []}]), \
                patch.object(sr.stripe.Invoice, 'void_invoice') as void:
            self.assertEqual(sr.cancel_unpaid_now('sub_9'), 1)
        cancel.assert_called_once_with('sub_9')
        void.assert_called_once_with('in_1')

    def test_unpaid_statuses(self):
        import subscription_routes as sr
        self.assertIn('past_due', sr.UNPAID_STATUSES)
        self.assertNotIn('active', sr.UNPAID_STATUSES)
