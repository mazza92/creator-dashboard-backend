"""On-site Pro checkout (Stripe embedded Checkout): config and the reassurance line under the pay button.

Nov 2026: Polly's direct-to-Stripe buttons sent 11 creators to the hosted page in 3 days and none paid. The hosted
page said nothing beyond the price; the on-site page carries the context (what Pro does, cancel anytime, $0 today
on a free week) next to the card form.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional


def publishable_key() -> str:
    return (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()


def embedded_ready() -> bool:
    """Embedded Checkout needs the publishable key in the browser; without it the hosted page is used."""
    key = publishable_key()
    secret = (os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or "").strip()
    if not key.startswith("pk_"):
        return False
    # A test key with a live secret (or the reverse) would mount a checkout that can't load the session.
    return not secret or (key.startswith("pk_live_") == secret.startswith(("sk_live_", "rk_live_")))


def trial_end_date(trial_days: int, now: Optional[datetime] = None) -> Optional[str]:
    if not trial_days:
        return None
    end = (now or datetime.now(timezone.utc)) + timedelta(days=int(trial_days))
    return end.date().isoformat()


def checkout_submit_text(trial_days: int, now: Optional[datetime] = None) -> str:
    """Shown under Stripe's pay button, on the hosted page too (Stripe caps it at 1200 characters)."""
    if trial_days:
        end = (now or datetime.now(timezone.utc)) + timedelta(days=int(trial_days))
        day = f"{end.strftime('%B')} {end.day}"
        return (f"$0 today. Your free week ends {day}; cancel before then in one tap from Settings and you pay "
                "nothing. Polly starts on your first pitches right away.")
    return "Cancel anytime in one tap from Settings. Polly starts on your first pitches right away."
