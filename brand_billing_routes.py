# -*- coding: utf-8 -*-
"""
Brand Gifted UGC billing — Stripe Checkout / portal / webhooks ($299/mo).

Public routes are roster-token auth (same magic link as the brand PR portal).
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

import stripe
from flask import Blueprint, jsonify, request
from psycopg2.extras import RealDictCursor

from pr_crm_routes import get_db_connection
from services.brand_billing import (
    PRODUCT_META,
    PLAN_GIFTED_UGC_299,
    apply_subscription_active,
    apply_subscription_status_by_stripe_id,
    billing_public_summary,
    ensure_brand_billing_schema,
    get_or_create_brand_billing,
)

brand_billing_bp = Blueprint("brand_billing", __name__, url_prefix="/api/brand-billing")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

_TOKEN_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{16,64}$")


def _frontend_base() -> str:
    return (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/")


def _load_campaign_by_token(cursor, token: str):
    if not token or not _TOKEN_RE.match(token):
        return None
    cursor.execute(
        """
        SELECT c.*, b.brand_name, b.contact_email, b.slug AS brand_slug, b.logo_url
        FROM brand_pr_campaigns c
        JOIN pr_brands b ON b.id = c.brand_id
        WHERE c.token = %s
        """,
        (token,),
    )
    return cursor.fetchone()


def _period_end_from_subscription(sub) -> datetime | None:
    raw = None
    if isinstance(sub, dict):
        raw = sub.get("current_period_end")
    else:
        raw = getattr(sub, "current_period_end", None)
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@brand_billing_bp.route("/r/<token>/status", methods=["GET"])
def roster_billing_status(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        ensure_brand_billing_schema(cursor, conn)
        campaign = _load_campaign_by_token(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404
        summary = billing_public_summary(cursor, campaign["brand_id"])
        conn.commit()
        conn.close()
        return jsonify({"success": True, "billing": summary}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_billing_bp.route("/r/<token>/checkout", methods=["POST"])
def roster_create_checkout(token):
    """Create Stripe Checkout for Brand Gifted UGC ($299/mo)."""
    try:
        price_id = os.getenv("STRIPE_PRICE_ID_BRAND_GIFTED_MONTHLY")
        if not price_id:
            return jsonify({
                "success": False,
                "error": "Brand plan price not configured. Set STRIPE_PRICE_ID_BRAND_GIFTED_MONTHLY.",
                "code": "price_not_configured",
            }), 500
        if not stripe.api_key:
            return jsonify({"success": False, "error": "Stripe is not configured"}), 500

        data = request.get_json(silent=True) or {}
        override_email = (data.get("email") or "").strip() or None

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        ensure_brand_billing_schema(cursor, conn)
        campaign = _load_campaign_by_token(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404

        brand_id = campaign["brand_id"]
        email = override_email or (campaign.get("contact_email") or "").strip() or None
        billing = get_or_create_brand_billing(cursor, brand_id, email=email)
        conn.commit()

        frontend = _frontend_base()
        success_url = (
            f"{frontend}/r/{token}?billing=success&session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = f"{frontend}/r/{token}?billing=cancel"

        session_kwargs = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "allow_promotion_codes": True,
            "metadata": {
                "product": PRODUCT_META,
                "plan": PLAN_GIFTED_UGC_299,
                "brand_id": str(brand_id),
                "campaign_id": str(campaign["id"]),
                "campaign_token": token,
                "brand_name": campaign.get("brand_name") or "",
            },
            "subscription_data": {
                "metadata": {
                    "product": PRODUCT_META,
                    "plan": PLAN_GIFTED_UGC_299,
                    "brand_id": str(brand_id),
                }
            },
        }
        if billing.get("stripe_customer_id"):
            session_kwargs["customer"] = billing["stripe_customer_id"]
        elif email:
            session_kwargs["customer_email"] = email

        checkout_session = stripe.checkout.Session.create(**session_kwargs)
        conn.close()
        return jsonify({
            "success": True,
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
        }), 200
    except stripe.error.InvalidRequestError as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_billing_bp.route("/r/<token>/portal", methods=["POST"])
def roster_billing_portal(token):
    try:
        if not stripe.api_key:
            return jsonify({"success": False, "error": "Stripe is not configured"}), 500

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        ensure_brand_billing_schema(cursor, conn)
        campaign = _load_campaign_by_token(cursor, token)
        if not campaign:
            conn.close()
            return jsonify({"success": False, "error": "Roster link not found or expired"}), 404

        billing = get_or_create_brand_billing(cursor, campaign["brand_id"])
        customer_id = billing.get("stripe_customer_id")
        if not customer_id:
            conn.close()
            return jsonify({
                "success": False,
                "error": "No billing account yet — subscribe first.",
                "code": "no_customer",
            }), 404

        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{_frontend_base()}/r/{token}",
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "portal_url": portal.url}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@brand_billing_bp.route("/webhook", methods=["POST"])
def brand_billing_webhook():
    """Stripe webhook for Brand Gifted UGC subscriptions."""
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        webhook_secret = (
            os.getenv("STRIPE_WEBHOOK_SECRET_BRAND")
            or os.getenv("STRIPE_WEBHOOK_SECRET_SUBSCRIPTION")
        )
        if not webhook_secret:
            print("⚠️  Brand billing webhook secret not set — constructing event without verify")
            event = stripe.Event.construct_from(request.json, stripe.api_key)
        else:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        print(f"❌ Brand billing webhook signature failed: {e}")
        return jsonify({"error": str(e)}), 400

    print(f"📨 Brand billing webhook: {event['type']}")

    try:
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            meta = session.get("metadata") or {}
            if meta.get("product") != PRODUCT_META:
                print("Skipping non-brand checkout in brand billing webhook")
                return jsonify({"success": True}), 200

            brand_id = int(meta.get("brand_id") or 0)
            if not brand_id:
                return jsonify({"error": "missing brand_id"}), 400

            subscription_id = session.get("subscription")
            customer_id = session.get("customer")
            email = session.get("customer_details", {}).get("email") if isinstance(
                session.get("customer_details"), dict
            ) else None
            if not email:
                email = session.get("customer_email")

            period_end = None
            if subscription_id and stripe.api_key:
                try:
                    sub = stripe.Subscription.retrieve(subscription_id)
                    period_end = _period_end_from_subscription(sub)
                except Exception as sub_err:
                    print(f"⚠️  Could not load brand subscription: {sub_err}")

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            ensure_brand_billing_schema(cursor, conn)
            apply_subscription_active(
                cursor,
                brand_id=brand_id,
                customer_id=customer_id,
                subscription_id=subscription_id,
                email=email,
                current_period_end=period_end,
                status="active",
            )
            conn.commit()
            conn.close()
            print(f"✅ Brand {brand_id} Gifted UGC subscription active")

        elif event["type"] in (
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            subscription = event["data"]["object"]
            meta = subscription.get("metadata") or {}
            # Only touch brand_billing rows (by subscription id). Creator Pro uses creators table.
            if meta.get("product") and meta.get("product") != PRODUCT_META:
                return jsonify({"success": True}), 200

            subscription_id = subscription.get("id")
            status = subscription.get("status") or "canceled"
            if event["type"] == "customer.subscription.deleted":
                status = "canceled"
            period_end = _period_end_from_subscription(subscription)
            customer_id = subscription.get("customer")

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            ensure_brand_billing_schema(cursor, conn)
            updated = apply_subscription_status_by_stripe_id(
                cursor,
                subscription_id,
                status,
                current_period_end=period_end,
                customer_id=customer_id,
            )
            # Fallback: brand_id in metadata if row not linked yet
            if not updated and meta.get("brand_id"):
                try:
                    brand_id = int(meta["brand_id"])
                except (TypeError, ValueError):
                    brand_id = 0
                if brand_id:
                    mapped = "active" if status in ("active", "trialing") else (
                        "past_due" if status == "past_due" else "canceled"
                    )
                    apply_subscription_active(
                        cursor,
                        brand_id=brand_id,
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        current_period_end=period_end,
                        status=mapped,
                    )
            conn.commit()
            conn.close()

        elif event["type"] == "invoice.payment_failed":
            invoice = event["data"]["object"]
            subscription_id = invoice.get("subscription")
            if subscription_id:
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                ensure_brand_billing_schema(cursor, conn)
                apply_subscription_status_by_stripe_id(
                    cursor, subscription_id, "past_due"
                )
                conn.commit()
                conn.close()

        elif event["type"] == "invoice.paid":
            invoice = event["data"]["object"]
            subscription_id = invoice.get("subscription")
            if subscription_id:
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                ensure_brand_billing_schema(cursor, conn)
                apply_subscription_status_by_stripe_id(
                    cursor, subscription_id, "active"
                )
                conn.commit()
                conn.close()

        return jsonify({"success": True}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
