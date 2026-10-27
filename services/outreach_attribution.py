"""Credit a cold email (its brand_outreach_tracking.ref_code, carried as ?oref=) with what the brand did after:
opened the free scan, signed up, started its first campaign, paid for Pro. Logged once per step in brand_activity."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services import brand_pipeline as bp

EVENTS = ("scan_open", "signed_up", "campaign", "paid")


def _val(row, key):
    return row[key] if isinstance(row, dict) else row[0]


def mark(cursor, ref: str, event: str, meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Record a funnel step for the prospect behind a ref code, once per event. Returns the brand_id."""
    ref = str(ref or "").strip()
    if event not in EVENTS or not ref:
        return None
    cursor.execute("SELECT brand_id FROM brand_outreach_tracking WHERE ref_code = %s", (ref,))
    hit = cursor.fetchone()
    if not hit:
        return None
    brand_id = int(_val(hit, "brand_id"))
    kind = f"funnel_{event}"
    cursor.execute("SELECT 1 FROM brand_activity WHERE brand_id = %s AND kind = %s LIMIT 1", (brand_id, kind))
    if cursor.fetchone():
        return brand_id
    bp.log_activity(cursor, brand_id, kind, meta=meta or {})
    if event == "paid":
        cursor.execute("UPDATE brand_outreach_tracking SET stage = 'paid' WHERE brand_id = %s", (brand_id,))
    elif event in ("signed_up", "campaign"):
        row = bp.load_row(cursor, brand_id)
        if row:
            bp._set_stage(cursor, row, "interested" if event == "signed_up" else "won", f"after email: {event}")
    return brand_id


def mark_paid(cursor, pr_brand_id: int) -> Optional[int]:
    """A brand subscribed: credit the cold email behind it (its workspace account's ref, or the prospect row)."""
    refs: List[str] = []
    cursor.execute("SELECT outreach_ref FROM brands WHERE pr_brand_id = %s AND outreach_ref IS NOT NULL", (pr_brand_id,))
    refs += [_val(r, "outreach_ref") for r in cursor.fetchall()]
    cursor.execute("SELECT ref_code FROM brand_outreach_tracking WHERE brand_id = %s AND ref_code IS NOT NULL",
                   (pr_brand_id,))
    refs += [_val(r, "ref_code") for r in cursor.fetchall()]
    for ref in dict.fromkeys(refs):
        hit = mark(cursor, ref, "paid", {"pr_brand_id": pr_brand_id})
        if hit:
            return hit
    return None


def funnel(cursor, hours: int) -> Dict[str, int]:
    cursor.execute("""SELECT kind, COUNT(DISTINCT brand_id) AS n FROM brand_activity
                      WHERE kind LIKE 'funnel_%%' AND created_at > NOW() - (%s * INTERVAL '1 hour') GROUP BY kind""",
                   (hours,))
    got = {(_val(r, "kind") if isinstance(r, dict) else r[0]): int(r["n"] if isinstance(r, dict) else r[1])
           for r in cursor.fetchall()}
    return {e: got.get(f"funnel_{e}", 0) for e in EVENTS}
