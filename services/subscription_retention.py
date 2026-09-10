"""Creator Pro retention helpers: dunning, cancel flow, activation."""

import os
import threading
from datetime import datetime, timezone

REPLY_STAGES = (
    "replied",
    "won",
    "received",
    "success",
    "responded",
    "accepted",
    "shipped",
)

# First failure + one reminder after Stripe has already retried.
DUNNING_ATTEMPTS = (1, 3)

_PORTAL_CONFIG_CACHE = None
_RETENTION_TABLE_READY = False
_RETENTION_TABLE_LOCK = threading.Lock()

CANCEL_REASONS = ("no_replies", "too_expensive", "unused", "other")
RETENTION_COUPON_ID = "pro_retention_12_3mo"
PRO_MONTHLY_CENTS = 1900
HOLD_MONTHLY_CENTS = 1200
HOLD_MONTHS = 3
LEGACY_PRO_PRICE_ID = "price_1TN7n9EYev1UAuLgQcLAr73g"


def invoice_subscription_id(invoice):
    """Stripe 2024+ invoices nest subscription under parent.subscription_details."""
    if not invoice:
        return None
    sub = invoice.get("subscription")
    if isinstance(sub, str) and sub:
        return sub
    if isinstance(sub, dict) and sub.get("id"):
        return sub.get("id")
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    nested = details.get("subscription")
    if isinstance(nested, str) and nested:
        return nested
    if isinstance(nested, dict):
        return nested.get("id")
    return None


def invoice_customer_id(invoice):
    if not invoice:
        return None
    customer = invoice.get("customer")
    if isinstance(customer, str) and customer:
        return customer
    if isinstance(customer, dict):
        return customer.get("id")
    return None


def should_send_dunning(attempt_count):
    try:
        n = int(attempt_count or 0)
    except (TypeError, ValueError):
        return False
    return n in DUNNING_ATTEMPTS


def dunning_already_sent(invoice, attempt_count):
    meta = (invoice or {}).get("metadata") or {}
    return meta.get(f"dunning_attempt_{attempt_count}") == "1"


def dunning_metadata_key(attempt_count):
    return f"dunning_attempt_{attempt_count}"


def subscription_period_end_ts(subscription):
    if not subscription:
        return None
    ts = subscription.get("current_period_end")
    if ts:
        return int(ts)
    items = (subscription.get("items") or {}).get("data") or []
    if items:
        nested = items[0].get("current_period_end")
        if nested:
            return int(nested)
    return None


def period_end_datetime(subscription):
    ts = subscription_period_end_ts(subscription)
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def should_downgrade_on_status(status, cancel_at_period_end=False):
    """Keep Pro access through the paid period. Drop only when Stripe has ended it."""
    if status in ("canceled", "unpaid", "incomplete_expired"):
        return True
    return False


def increment_replies_received(cursor, creator_id, previous_stage, new_stage):
    """Bump creators.total_replies_received once when a pitch first becomes a reply."""
    if new_stage not in REPLY_STAGES:
        return False
    if previous_stage in REPLY_STAGES:
        return False
    cursor.execute(
        """
        UPDATE creators
        SET total_replies_received = COALESCE(total_replies_received, 0) + 1
        WHERE id = %s
        """,
        (creator_id,),
    )
    return cursor.rowcount > 0


def settings_url():
    frontend = (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/")
    return f"{frontend}/creator/dashboard/settings"


def discover_url():
    frontend = (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/")
    return f"{frontend}/creator/dashboard/for-you"


def dunning_email_html(name, amount_label=None):
    who = name or "there"
    amount = f" ({amount_label})" if amount_label else ""
    update_url = settings_url()
    return f"""
    <p style="margin:0 0 16px;">Hey {who},</p>
    <p style="margin:0 0 16px;">We could not charge your Newcollab Pro card{amount}. Update your payment method so your unlimited credits and pipeline stay on.</p>
    <p style="margin:0 0 16px;"><a href="{update_url}">Update payment method</a></p>
    <p style="margin:0;">If this was a bank decline, retrying from Settings usually clears it in a minute.</p>
    """


def activation_email_html(name):
    who = name or "there"
    url = discover_url()
    return f"""
    <p style="margin:0 0 16px;">Hey {who},</p>
    <p style="margin:0 0 16px;">Pro is on. Brands usually take <strong>2–4 weeks</strong> to reply — silence in week one is normal, not a sign it failed.</p>
    <p style="margin:0 0 16px;">This week: send <strong>5 applications</strong> to brands that gift your size. We write the pitch. You tap send. We follow up.</p>
    <p style="margin:0 0 16px;"><a href="{url}">Open brands that fit you</a></p>
    <p style="margin:0;">If a card fails later, we will email you before Pro turns off.</p>
    """


def _feature_get(obj, key):
    if obj is None:
        return None
    if hasattr(obj, "get"):
        return obj.get(key)
    return getattr(obj, key, None)


def _portal_cancel_disabled(cfg):
    features = _feature_get(cfg, "features")
    cancel = _feature_get(features, "subscription_cancel")
    enabled = _feature_get(cancel, "enabled")
    return enabled is False


def get_retention_portal_configuration_id(stripe_module=None):
    """
    Billing portal for cards + invoices only. Cancel stays in-app so we can
    offer a talent manager or the $12/3-month hold before they leave.
    """
    global _PORTAL_CONFIG_CACHE
    env_id = (os.getenv("STRIPE_PORTAL_CONFIGURATION_ID") or "").strip()
    if env_id:
        return env_id
    if _PORTAL_CONFIG_CACHE:
        return _PORTAL_CONFIG_CACHE

    stripe_client = stripe_module
    if stripe_client is None:
        import stripe as stripe_client  # noqa: PLC0415

    try:
        for cfg in stripe_client.billing_portal.Configuration.list(limit=20).auto_paging_iter():
            if _portal_cancel_disabled(cfg):
                _PORTAL_CONFIG_CACHE = cfg.get("id") if hasattr(cfg, "get") else cfg.id
                return _PORTAL_CONFIG_CACHE
        created = stripe_client.billing_portal.Configuration.create(
            business_profile={"headline": "Manage your Newcollab Pro billing"},
            features={
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "customer_update": {
                    "enabled": True,
                    "allowed_updates": ["email", "name"],
                },
                "subscription_cancel": {"enabled": False},
            },
        )
        _PORTAL_CONFIG_CACHE = created.get("id") if isinstance(created, dict) else created.id
        print(f"[retention] Created Stripe portal config {_PORTAL_CONFIG_CACHE} (cancel disabled)")
        return _PORTAL_CONFIG_CACHE
    except Exception as exc:
        print(f"[retention] Portal configuration unavailable, using Stripe default: {exc}")
        return None


def offer_for_reason(reason, current_amount_cents=None, interval=None):
    """Which save offer to show for a cancel reason. None = no offer, just confirm cancel."""
    if reason == "no_replies":
        return "talent_manager"
    if reason == "too_expensive":
        if interval and interval not in ("month", "monthly"):
            return None
        if current_amount_cents is not None and int(current_amount_cents) != PRO_MONTHLY_CENTS:
            return None
        return "price_hold"
    return None


def subscription_price_snapshot(subscription):
    """unit_amount cents + recurring interval from the first subscription item."""
    if not subscription:
        return None, None
    items = (subscription.get("items") or {}).get("data") or []
    if not items:
        return None, None
    price = items[0].get("price") or {}
    if isinstance(price, str):
        return None, None
    amount = price.get("unit_amount")
    rec = price.get("recurring") or {}
    return amount, rec.get("interval")


def ensure_cancel_retention_schema(conn):
    """Log table only — no ALTER on creators (that deadlocks list endpoints)."""
    global _RETENTION_TABLE_READY
    if _RETENTION_TABLE_READY:
        return
    with _RETENTION_TABLE_LOCK:
        if _RETENTION_TABLE_READY:
            return
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_retention_events (
                    id SERIAL PRIMARY KEY,
                    creator_id INTEGER NOT NULL,
                    reason TEXT,
                    offer TEXT,
                    outcome TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_creator_retention_events_creator
                ON creator_retention_events (creator_id, created_at DESC)
                """
            )
            conn.commit()
            _RETENTION_TABLE_READY = True
        finally:
            cursor.close()


def log_retention_event(cursor, creator_id, reason, offer, outcome):
    cursor.execute(
        """
        INSERT INTO creator_retention_events (creator_id, reason, offer, outcome)
        VALUES (%s, %s, %s, %s)
        """,
        (creator_id, reason, offer, outcome),
    )


def get_or_create_retention_coupon(stripe_client):
    coupon_id = (os.getenv("STRIPE_COUPON_RETENTION_12") or RETENTION_COUPON_ID).strip()
    try:
        stripe_client.Coupon.retrieve(coupon_id)
        return coupon_id
    except Exception:
        pass
    created = stripe_client.Coupon.create(
        id=coupon_id,
        amount_off=PRO_MONTHLY_CENTS - HOLD_MONTHLY_CENTS,
        currency="usd",
        duration="repeating",
        duration_in_months=HOLD_MONTHS,
        name="$12/mo for 3 months",
    )
    return created.id if hasattr(created, "id") else coupon_id


def talent_manager_creator_email_html(name):
    who = name or "there"
    url = discover_url()
    return f"""
    <p style="margin:0 0 16px;">Hey {who},</p>
    <p style="margin:0 0 16px;">A Newcollab talent manager is on your account. This week we will review your kit, pick brands that gift your size, and push those applications until you get a yes or a clear no.</p>
    <p style="margin:0 0 16px;">Keep Pro on. First collabs usually take 2–4 weeks — we will not leave you in silence.</p>
    <p style="margin:0;"><a href="{url}">Open your brand matches</a></p>
    """


def price_hold_creator_email_html(name):
    who = name or "there"
    return f"""
    <p style="margin:0 0 16px;">Hey {who},</p>
    <p style="margin:0 0 16px;">Your next <strong>3 months of Pro are $12</strong> instead of $19. After that it returns to $19 unless you change it in Settings.</p>
    <p style="margin:0;">Same unlimited credits. Same pipeline. Use the extra time to land the first collab.</p>
    """


def talent_manager_team_email_html(creator):
    username = creator.get("username") or "unknown"
    email = creator.get("email") or "n/a"
    cid = creator.get("id") or "?"
    return f"""
    <p>Retention save: talent manager requested.</p>
    <p><strong>@{username}</strong> ({email}) · creator #{cid}</p>
    <p>They were about to cancel because brands have not replied. Review their kit this week, pick 5 brands that gift their size, and follow the applications until a yes or a clear no.</p>
    """
