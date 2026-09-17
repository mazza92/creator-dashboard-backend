"""Post-apply transparency: applicant count + brand reply rate.

Uses live application rows and stored brand response_rate.
Does not invent a rate when there is not enough history.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


MIN_RATE_SAMPLE = 5


def resolve_apply_transparency(
    counts: Optional[Dict[str, Any]] = None,
    brand: Optional[Dict[str, Any]] = None,
    *,
    min_sample: int = MIN_RATE_SAMPLE,
) -> Dict[str, Any]:
    counts = counts or {}
    brand = brand or {}
    applicants = _as_int(counts.get("applicants"))
    decided = _as_int(counts.get("decided"))
    selected = _as_int(counts.get("selected"))
    in_review = _as_int(counts.get("in_review"))
    stored = _as_float(brand.get("response_rate"))
    rate = None
    source = None
    if stored is not None and stored > 0:
        rate = round(stored, 1)
        source = "brand"
    elif decided >= min_sample and applicants > 0:
        rate = round(decided * 100.0 / applicants, 1)
        source = "campaign"
    days = _as_int(brand.get("avg_response_time_days") or brand.get("avg_response_days"))
    return {
        "applicants": applicants,
        "selected": selected,
        "in_review": in_review,
        "response_rate": rate,
        "avg_response_days": days if days and days > 0 else None,
        "response_rate_source": source,
    }


def fetch_apply_transparency(cursor, brand_id: int) -> Dict[str, Any]:
    if not brand_id:
        return resolve_apply_transparency()
    cursor.execute(
        """
        SELECT
            COUNT(*)::int AS applicants,
            COUNT(*) FILTER (WHERE status IN ('ships', 'posted'))::int AS selected,
            COUNT(*) FILTER (
                WHERE status IN ('ships', 'posted', 'declined', 'skipped')
            )::int AS decided,
            COUNT(*) FILTER (WHERE COALESCE(status, 'review') = 'review')::int AS in_review
        FROM brand_pr_applications
        WHERE brand_id = %s
          AND COALESCE(status, 'review') <> 'hidden'
        """,
        (brand_id,),
    )
    counts = cursor.fetchone() or {}
    cursor.execute(
        """
        SELECT response_rate, avg_response_time_days
        FROM pr_brands
        WHERE id = %s
        """,
        (brand_id,),
    )
    brand = cursor.fetchone() or {}
    return resolve_apply_transparency(counts, brand)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
