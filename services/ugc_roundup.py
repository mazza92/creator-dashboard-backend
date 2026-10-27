"""Every 48h, Pro creators get the paid UGC gigs the scanner found since their last roundup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

ROUNDUP_KIND = "ugc_roundup"
ROUNDUP_EVERY = timedelta(hours=47)
ROUNDUP_WINDOW = timedelta(hours=48)
SEND_HOURS_UTC = range(14, 18)  # 10am New York, 3pm London
MAX_GIGS = 6
# Monday weekly report day: the roundup waits until the next day's window.
SAME_DAY_KINDS = ("monday_board", "autopilot_week", "renewal_note")

_GIG_COLUMNS = """
    id, brand_name, brand_category, brand_logo_url, brand_website,
    product_name, campaign_description,
    pr_value_usd, creator_count_range, shipping_regions, follower_ranges,
    content_types, creator_niches, additional_notes,
    spots_total, spots_filled, closes_at,
    created_at, published_at, listing_brief
"""


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _aware(value: datetime) -> datetime:
    """opportunities timestamps are naive UTC; the email log is timestamptz."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def load_fresh_gigs(conn, since: datetime) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    cur.execute(
        f"""
        SELECT {_GIG_COLUMNS}
        FROM opportunities
        WHERE status = 'live'
          AND additional_notes ILIKE %s
          AND published_at >= %s
          AND (closes_at IS NULL OR closes_at > NOW())
        ORDER BY published_at DESC
        LIMIT 200
        """,
        ("%[scanner:%", since),
    )
    return [dict(r) for r in cur.fetchall()]


def _creator_context(conn, creator_id: int):
    from opportunities_routes import _tokenize_niche_blob

    cur = _cursor(conn)
    cur.execute("SELECT niche, creator_niches FROM creators WHERE id = %s", (creator_id,))
    row = cur.fetchone() or {}
    tokens = _tokenize_niche_blob(row.get("creator_niches")) | _tokenize_niche_blob(row.get("niche"))
    cur.execute("SELECT opportunity_id FROM opportunity_applications WHERE creator_id = %s", (creator_id,))
    applied = {r["opportunity_id"] for r in cur.fetchall()}
    return tokens, applied


def pick_gigs(rows: Iterable[Dict[str, Any]], tokens: Set[str], applied: Set[int],
              limit: int = MAX_GIGS) -> List[Dict[str, Any]]:
    """Niche matches first, then other fresh paid UGC gigs, newest first, one per brief."""
    from opportunities_routes import _opportunity_card
    from services.polly_gigs import gig_card_from_opp, gig_dedupe_key, is_paid_listing, newest_published
    from services.ugc_gig_filter import is_ugc_gig

    matched, others = [], []
    for opp in rows:
        if not is_ugc_gig(opp):
            continue
        card, is_match = _opportunity_card(opp, tokens, applied)
        if card.get("already_applied") or not is_paid_listing(card):
            continue
        card["published_at"] = opp.get("published_at")
        (matched if is_match else others).append(card)
    out, seen = [], set()
    for card in newest_published(matched) + newest_published(others):
        gig = gig_card_from_opp(card)
        key = gig_dedupe_key(gig)
        if key in seen:
            continue
        seen.add(key)
        gig["is_matched"] = bool(card.get("is_matched"))
        for field in ("raw_listing", "campaign_description", "listing_src", "fit_score"):
            gig.pop(field, None)
        out.append(gig)
        if len(out) >= limit:
            break
    return out


def polly_href(kind: str = ROUNDUP_KIND) -> str:
    from services.polly_alerts import POLLY_PATH, frontend_url

    params = {"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": kind}
    return f"{frontend_url()}{POLLY_PATH}?{urlencode(params)}"


def apply_href(gig: Dict[str, Any]) -> str:
    url = str(gig.get("external_apply_url") or "").strip()
    if gig.get("apply_mode") == "url" and url.lower().startswith(("http://", "https://")):
        return url
    return polly_href()


def gig_meta(gig: Dict[str, Any]) -> List[str]:
    brand = str(gig.get("brand_name") or "").strip()
    if brand.lower() == str(gig.get("headline") or "").strip().lower():
        brand = ""
    bits = [brand, gig.get("pay_label"), gig.get("location")]
    if gig.get("source_label"):
        bits.append(f"via {gig['source_label']}")
    return [str(b).strip() for b in bits if b and str(b).strip()]


def roundup_subject(gigs: List[Dict[str, Any]]) -> str:
    if len(gigs) == 1:
        return f"New paid UGC gig: {gigs[0].get('headline') or 'fresh brief'}"
    return f"{len(gigs)} new paid UGC gigs for you"


def roundup_email(first_name: str, gigs: List[Dict[str, Any]], unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = roundup_subject(gigs)
    matched = sum(1 for g in gigs if g.get("is_matched"))
    intro = (
        "I checked every UGC board for you. Here are the paid gigs posted in the last 48 hours"
        + (f", {matched} in your niche." if matched else ".")
    )
    lines = [escape(intro)]
    for gig in gigs:
        title = escape(gig.get("headline") or "Paid UGC gig")
        meta = " &middot; ".join(escape(bit) for bit in gig_meta(gig))
        href = escape(apply_href(gig), quote=True)
        lines.append(
            f'<a href="{href}" style="color:#111827;font-weight:700;text-decoration:none;">{title}</a>'
            + (f'<br><span style="font-size:13px;color:#6B7280;">{meta}</span>' if meta else "")
            + f'<br><a href="{href}" style="font-size:13px;font-weight:600;color:#C2410C;">Apply &rarr;</a>'
        )
    lines.append(escape("Early applicants get picked first. Most of these close within a week."))
    buttons = [{"label": "Open in Polly", "href": polly_href()}]
    return {"subject": subject, "html": render_email(first_name, "Fresh paid UGC gigs", lines, buttons, unsubscribe_url)}


def roundup_note(gigs: List[Dict[str, Any]]) -> str:
    return (
        f"🎬 **{len(gigs)} new paid UGC {'gig' if len(gigs) == 1 else 'gigs'}** from the last 48 hours. "
        "Tap one to apply."
    )


def post_roundup_note(conn, creator_id: int, gigs: List[Dict[str, Any]]) -> None:
    from services.polly_gigs import mark_shown_gigs
    from services.polly_memory import load_thread, save_thread

    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or [])
    messages.append({"role": "assistant", "kind": ROUNDUP_KIND, "content": roundup_note(gigs), "gigs": gigs})
    notes = mark_shown_gigs(thread.get("notes") or {}, gigs)
    save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=notes)


def _last_roundup_at(conn, creator_id: int) -> Optional[datetime]:
    cur = _cursor(conn)
    cur.execute(
        "SELECT MAX(sent_at) AS at FROM polly_email_log WHERE creator_id = %s AND kind = %s AND ok",
        (creator_id, ROUNDUP_KIND),
    )
    row = cur.fetchone() or {}
    return row.get("at")


def email_ugc_roundups(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                       test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Pro creators with Autopilot on: fresh paid UGC gigs every 48h, by email and in Polly."""
    from services.polly_alerts import _default_send, _log_email, _recipient, _unsubscribe_url, ensure_alert_tables
    from services.polly_autopilot import ensure_autopilot_tables, utc_now
    from services.waitlist_gate import WAITLIST_EXCLUDE_SQL

    now = _aware(now or utc_now())
    if now.hour not in SEND_HOURS_UTC:
        return []
    ensure_alert_tables(conn)
    ensure_autopilot_tables(conn)
    window_start = now - ROUNDUP_WINDOW
    pool = load_fresh_gigs(conn, window_start.replace(tzinfo=None))
    if not pool:
        return []
    send = send_fn or _default_send
    cur = _cursor(conn)
    cur.execute(
        f"""
        SELECT c.id AS creator_id
        FROM creators c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN polly_autopilot a ON a.creator_id = c.id
        WHERE (LOWER(COALESCE(c.subscription_tier, '')) = 'pro' OR c.unlocks_tier = 'pro')
          AND COALESCE(a.enabled, TRUE)
          AND u.email IS NOT NULL AND u.unsubscribed_at IS NULL
          AND {WAITLIST_EXCLUDE_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM polly_email_log l
              WHERE l.creator_id = c.id AND l.kind = %s AND l.sent_at > %s
          )
          AND NOT EXISTS (
              SELECT 1 FROM polly_email_log l
              WHERE l.creator_id = c.id AND l.kind = ANY(%s) AND l.ok AND l.sent_at > %s
          )
        ORDER BY c.id
        LIMIT %s
        """,
        (ROUNDUP_KIND, now - ROUNDUP_EVERY, list(SAME_DAY_KINDS), now - timedelta(hours=12), limit),
    )
    out: List[Dict[str, Any]] = []
    for row in [dict(r) for r in cur.fetchall()]:
        cid = row["creator_id"]
        who = _recipient(conn, cid)
        if not who or not who.get("is_pro"):
            continue
        try:
            last = _last_roundup_at(conn, cid)
            since = max(window_start, _aware(last)) if last else window_start
            tokens, applied = _creator_context(conn, cid)
            gigs = pick_gigs([g for g in pool if _aware(g["published_at"]) > since], tokens, applied)
        except Exception as err:
            print(f"[UGC roundup] {cid} skipped: {err}")
            conn.rollback()
            continue
        if not gigs:
            if not dry_run:
                _log_email(conn, cid, ROUNDUP_KIND, None, None, "", {"success": False, "error": "no_gigs"})
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = roundup_email(who["first"], gigs, unsub)
        item = {"creator_id": cid, "kind": ROUNDUP_KIND, "subject": mail["subject"], "to": who["email"],
                "gigs": len(gigs)}
        if dry_run:
            out.append(item)
            continue
        try:
            post_roundup_note(conn, cid, gigs)
        except Exception as err:
            print(f"[UGC roundup] thread note skipped: {err}")
            conn.rollback()
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, ROUNDUP_KIND)
        _log_email(conn, cid, ROUNDUP_KIND, None, None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
