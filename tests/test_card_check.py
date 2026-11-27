"""Free-week $1 card check (services.card_check)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from services import card_check as cc


class CardError(Exception):
    def __init__(self, decline_code="insufficient_funds"):
        super().__init__("declined")
        self.code = "card_declined"
        self.decline_code = decline_code


def fake_stripe(sub=None, intent=None, raises=None):
    stripe = MagicMock()
    stripe.error = SimpleNamespace(CardError=CardError)
    stripe.Subscription.retrieve.return_value = sub or {
        "id": "sub_1", "status": "trialing", "customer": "cus_1", "default_payment_method": "pm_1",
        "currency": "usd", "metadata": {}}
    if raises:
        stripe.PaymentIntent.create.side_effect = raises
    else:
        stripe.PaymentIntent.create.return_value = intent or {"id": "pi_1", "status": "requires_capture"}
    return stripe


class CardCheck(unittest.TestCase):
    def test_funds_ok_hold_released_trial_goes_on(self):
        stripe = fake_stripe()
        out = cc.verify_trial_card(stripe, "sub_1", 7)
        self.assertTrue(out["ok"])
        kw = stripe.PaymentIntent.create.call_args.kwargs
        self.assertEqual((kw["amount"], kw["capture_method"], kw["off_session"]), (100, "manual", True))
        stripe.PaymentIntent.cancel.assert_called_once_with("pi_1")
        stripe.Subscription.cancel.assert_not_called()
        stripe.Subscription.modify.assert_called_with("sub_1", metadata={"card_check": "ok"})

    def test_empty_card_ends_the_trial(self):
        stripe = fake_stripe(raises=CardError("insufficient_funds"))
        out = cc.verify_trial_card(stripe, "sub_1", 7)
        self.assertEqual(out, {"ok": False, "reason": "insufficient_funds"})
        stripe.Subscription.cancel.assert_called_once_with("sub_1")
        stripe.Subscription.modify.assert_called_with("sub_1", metadata={"card_check": "declined"})

    def test_runs_once(self):
        stripe = fake_stripe(sub={"id": "sub_1", "status": "trialing", "metadata": {"card_check": "declined"}})
        self.assertFalse(cc.verify_trial_card(stripe, "sub_1")["ok"])
        stripe.PaymentIntent.create.assert_not_called()

    def test_3ds_or_api_trouble_never_blocks_the_trial(self):
        stripe = fake_stripe(intent={"id": "pi_2", "status": "requires_action"})
        self.assertTrue(cc.verify_trial_card(stripe, "sub_1")["ok"])
        stripe.PaymentIntent.cancel.assert_called_once_with("pi_2")
        stripe.Subscription.cancel.assert_not_called()
        stripe = fake_stripe(raises=RuntimeError("api down"))
        self.assertTrue(cc.verify_trial_card(stripe, "sub_1")["ok"])
        stripe.Subscription.cancel.assert_not_called()

    def test_not_a_trial_is_left_alone(self):
        stripe = fake_stripe(sub={"id": "sub_1", "status": "active", "metadata": {}})
        self.assertTrue(cc.verify_trial_card(stripe, "sub_1")["ok"])
        stripe.PaymentIntent.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
