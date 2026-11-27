"""Free-week card check: a $1 hold placed and released right after a trial checkout.

Nov 2026: of the last 100 charges, 57 failed, 30 for insufficient funds; 19 of 30 failing customers never paid
once. Stripe Checkout only verifies a trial card with a $0 test, so an empty debit or prepaid card is found out on
day 7. A $1 authorization (capture_method=manual, released at once, never charged) catches cards with nothing on
them, prepaid cards that refuse merchants and cards blocked for online payments, while the creator is still on
the page. A declined card ends the trial subscription before it is ever stored on the creator, so they can try
another card (and keep the free-week offer).

verify_trial_card() is idempotent: the result is saved on the subscription's metadata (card_check), so the
success page (confirm-checkout) and the webhook, which race, run the hold once.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

HOLD_CENTS = 100
DECLINED_MESSAGE = ("Your card was declined for the $1 check, so your free week didn't start. Nothing was charged. "
                    "Try another card, or Apple Pay / Google Pay.")


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _payment_method(stripe, sub) -> Optional[str]:
    pm = _get(sub, "default_payment_method")
    if pm:
        return pm if isinstance(pm, str) else _get(pm, "id")
    customer = _get(sub, "customer")
    if not customer:
        return None
    cust = stripe.Customer.retrieve(customer if isinstance(customer, str) else _get(customer, "id"))
    pm = _get(_get(cust, "invoice_settings") or {}, "default_payment_method")
    return pm if isinstance(pm, str) or pm is None else _get(pm, "id")


def verify_trial_card(stripe, subscription_id: str, creator_id: Any = None) -> Dict[str, Any]:
    """{'ok': bool, 'reason': str}. Never raises: when the check itself can't run, the trial goes ahead (ok)."""
    if not subscription_id:
        return {"ok": True, "reason": "no_subscription"}
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
    except Exception as err:  # noqa: BLE001
        print(f"[card_check] subscription lookup failed {subscription_id}: {err}")
        return {"ok": True, "reason": "lookup_failed"}
    done = _get(_get(sub, "metadata") or {}, "card_check")
    if done in ("ok", "declined", "skipped"):
        return {"ok": done != "declined", "reason": f"already_{done}"}
    if _get(sub, "status") != "trialing":
        return {"ok": True, "reason": "not_trialing"}

    def mark(value: str) -> None:
        try:
            stripe.Subscription.modify(subscription_id, metadata={"card_check": value})
        except Exception as err:  # noqa: BLE001
            print(f"[card_check] could not save result on {subscription_id}: {err}")

    try:
        pm = _payment_method(stripe, sub)
    except Exception as err:  # noqa: BLE001
        print(f"[card_check] payment method lookup failed {subscription_id}: {err}")
        pm = None
    if not pm:
        mark("skipped")
        return {"ok": True, "reason": "no_payment_method"}

    customer = _get(sub, "customer")
    customer = customer if isinstance(customer, str) else _get(customer, "id")
    try:
        intent = stripe.PaymentIntent.create(
            amount=HOLD_CENTS,
            currency=str(_get(sub, "currency") or "usd"),
            customer=customer,
            payment_method=pm,
            capture_method="manual",
            confirm=True,
            off_session=True,
            description="Newcollab free week: $1 card check, released immediately",
            metadata={"purpose": "trial_card_check", "subscription": subscription_id,
                      "creator_id": str(creator_id or "")},
        )
    except Exception as err:  # noqa: BLE001
        card_error = getattr(stripe, "error", None) and isinstance(err, getattr(stripe.error, "CardError", ()))
        if not card_error:
            print(f"[card_check] hold could not run on {subscription_id}: {err}")
            mark("skipped")
            return {"ok": True, "reason": "check_error"}
        code = getattr(err, "code", None) or "card_declined"
        decline = getattr(err, "decline_code", None) or ""
        print(f"[card_check] declined {subscription_id} creator={creator_id}: {code} {decline}")
        mark("declined")
        try:
            stripe.Subscription.cancel(subscription_id)
        except Exception as cancel_err:  # noqa: BLE001
            print(f"[card_check] could not end declined trial {subscription_id}: {cancel_err}")
        return {"ok": False, "reason": decline or code}

    status = _get(intent, "status")
    try:
        if status in ("requires_capture", "requires_action", "requires_confirmation", "requires_payment_method"):
            stripe.PaymentIntent.cancel(_get(intent, "id"))  # release the hold: never captured
    except Exception as err:  # noqa: BLE001
        print(f"[card_check] release failed {_get(intent, 'id')}: {err}")
    if status == "requires_payment_method":
        mark("declined")
        try:
            stripe.Subscription.cancel(subscription_id)
        except Exception as cancel_err:  # noqa: BLE001
            print(f"[card_check] could not end declined trial {subscription_id}: {cancel_err}")
        return {"ok": False, "reason": "declined"}
    # requires_capture: the card holds at least $1. requires_action (3D Secure off-session): can't be checked
    # without the creator, so the trial goes ahead.
    mark("ok" if status == "requires_capture" else "skipped")
    return {"ok": True, "reason": status or "unknown"}
