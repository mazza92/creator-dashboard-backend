# -*- coding: utf-8 -*-
"""Unit tests for Brand Gifted UGC billing helpers (no Stripe / DB)."""

from unittest.mock import MagicMock

from services.brand_billing import (
    billing_public_summary,
    can_mint_campaign,
    effective_free_used,
)


def _cursor_with_billing(billing_row, shipped=0):
    cursor = MagicMock()

    def execute(sql, params=None):
        cursor._last_sql = " ".join(sql.split())
        cursor._last_params = params

    def fetchone():
        sql = getattr(cursor, "_last_sql", "")
        if "FROM brand_billing" in sql or "RETURNING *" in sql:
            return billing_row
        if "FROM brand_pr_campaigns" in sql and "shipped" in sql:
            return {"n": shipped}
        return billing_row

    cursor.execute.side_effect = execute
    cursor.fetchone.side_effect = fetchone
    return cursor


def test_first_campaign_can_mint_when_no_shipped():
    billing = {
        "brand_id": 1,
        "status": "none",
        "free_campaigns_used": 0,
        "plan": "gifted_ugc_299",
        "current_period_end": None,
        "stripe_customer_id": None,
        "email": None,
    }
    cursor = _cursor_with_billing(billing, shipped=0)
    # get_or_create hits SELECT then maybe INSERT — return billing for both
    allowed, summary, reason = can_mint_campaign(cursor, 1)
    assert allowed is True
    assert summary["can_mint_next"] is True
    assert reason is None


def test_needs_subscribe_after_shipped_without_active_sub():
    billing = {
        "brand_id": 1,
        "status": "trial_used",
        "free_campaigns_used": 1,
        "plan": "gifted_ugc_299",
        "current_period_end": None,
        "stripe_customer_id": None,
        "email": "a@b.com",
    }
    cursor = _cursor_with_billing(billing, shipped=1)
    allowed, summary, reason = can_mint_campaign(cursor, 1)
    assert allowed is False
    assert summary["needs_subscribe"] is True
    assert reason


def test_active_sub_can_mint_even_if_free_used():
    billing = {
        "brand_id": 1,
        "status": "active",
        "free_campaigns_used": 1,
        "plan": "gifted_ugc_299",
        "current_period_end": None,
        "stripe_customer_id": "cus_x",
        "email": "a@b.com",
    }
    cursor = _cursor_with_billing(billing, shipped=2)
    allowed, summary, _ = can_mint_campaign(cursor, 1)
    assert allowed is True
    assert summary["subscribed"] is True
    assert summary["needs_subscribe"] is False


def test_legacy_shipped_counts_as_free_used():
    billing = {
        "brand_id": 1,
        "status": "none",
        "free_campaigns_used": 0,
        "plan": "gifted_ugc_299",
        "current_period_end": None,
        "stripe_customer_id": None,
        "email": None,
    }
    assert effective_free_used(MagicMock(), billing, 1) == 0
    cursor = _cursor_with_billing(billing, shipped=1)
    assert effective_free_used(cursor, billing, 1) == 1
    summary = billing_public_summary(cursor, 1)
    assert summary["needs_subscribe"] is True
