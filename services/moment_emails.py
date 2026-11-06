"""The three moments a creator is most ready for Pro, each as one value-first email.

- reply_win:     a brand answered their pitch. Polly drafts the answer (with their rate if it's paid). Biggest
                 upgrade moment: on Pro Polly does this for every reply and keeps following up the rest.
- saved_brands:  they saved brands in the Brands feed but haven't pitched them. Cards + one tap; on Pro Polly
                 pitches every saved brand first.
- niche_gifting: brands our daily TikTok scan just caught gifting creators in their niche, as cards to save or
                 pitch. Weekly, the reason to come back.

All go through services.email_policy (via _recipient(kind=...)): caps, audience, one-a-day.
"""

from __future__ import annotations

from datetime import timedelta
from html import escape
from typing import Any, Callable, Dict, List, Optional

from services.brand_feed import NICHE_CATEGORIES

REPLY_WINDOW = timedelta(hours=48)
SAVED_AFTER = timedelta(hours=24)
NICHE_WINDOW = timedelta(days=7)
PRO_LINE_REPLY = ('<span style="color:#6B7280;">On <strong>Pro ($19/mo)</strong> I draft every brand reply with '
                  'your rate, and keep following up the brands that haven\'t answered yet.</span>')
PRO_LINE_SAVED = ('<span style="color:#6B7280;">On <strong>Pro ($19/mo)</strong> I pitch every brand you save, '
                  'first, from your Gmail, then 3 new ones every weekday.</span>')
PRO_LINE_NICHE = ('<span style="color:#6B7280;">On <strong>Pro ($19/mo)</strong> I pitch brands like these for '
                  'you every weekday.</span>')


def _pa():
    from services import polly_alerts
    return polly_alerts


def _feed_link(kind: str) -> str:
    from urllib.parse import urlencode
    pa = _pa()
    q = urlencode({"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": kind})
    return f"{pa.frontend_url()}/creator/dashboard/pr-brands?{q}"


def _cards(conn, rows: List[Dict[str, Any]], href_for: Callable[[Dict[str, Any]], str]) -> str:
    pa = _pa()
    facts = pa.brief_brand_facts(conn, [r.get("brand_id") for r in rows])
    return "".join(pa._brief_card(r.get("brand_name") or "A brand", facts.get(int(r.get("brand_id") or 0)) or {},
                                  href_for(r), label=r.get("cta") or "Send it") for r in rows)


# ----- reply_win --------------------------------------------------------------------------------------------

def reply_email(first: str, brand: str, kind: str, is_pro: bool, href: str, unsub: str) -> Dict[str, str]:
    pa = _pa()
    if kind == "brand_replied_question":
        subject = f"{brand} replied with a question. Your answer is one tap away"
        lead = f"<strong>{escape(brand)}</strong> answered your pitch with a question. Brands that ask usually want to work with you."
    else:
        subject = f"{brand} said yes 🎉 Here's your reply"
        lead = f"<strong>{escape(brand)}</strong> answered your pitch and they're interested."
    lines = [lead, "Reply within a day: brands move on fast. I'll write the answer for you, ready to send from your Gmail."]
    if not is_pro:
        lines.append(PRO_LINE_REPLY)
    buttons = [{"label": f"Write my reply to {brand}", "href": href}]
    return {"subject": subject, "html": pa.render_email(first, subject, lines, buttons, unsub)}


def email_reply_wins(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                     test_email: str = "") -> List[Dict[str, Any]]:
    pa = _pa()
    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    cur = pa._cursor(conn)
    cur.execute("""
        SELECT DISTINCT ON (e.creator_id) e.id, e.creator_id, e.brand_id, e.event_type,
               COALESCE(e.event_data->>'brand_name', b.brand_name) AS brand_name
        FROM polly_timeline_events e LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.event_type IN ('brand_replied_interested', 'brand_replied_question')
          AND e.occurred_at > %s
          AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.kind = 'reply_win' AND l.ref_id = e.id)
        ORDER BY e.creator_id, e.occurred_at DESC
        LIMIT %s
    """, (pa.utc_now() - REPLY_WINDOW, limit))
    out = []
    for row in [dict(r) for r in cur.fetchall()]:
        who = pa._recipient(conn, row["creator_id"], kind="reply_win")
        if not who:
            continue
        brand = row.get("brand_name") or "A brand"
        href = pa.chip_link("paid_reply", brand_id=row.get("brand_id"), brand_name=brand, kind="reply_win")
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = reply_email(who["first"], brand, row["event_type"], who["is_pro"], href, unsub)
        item = {"creator_id": row["creator_id"], "kind": "reply_win", "subject": mail["subject"], "to": who["email"]}
        if not dry_run:
            result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "reply_win")
            pa._log_email(conn, row["creator_id"], "reply_win", row.get("brand_id"), row["id"], mail["subject"], result)
            item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


# ----- saved_brands -----------------------------------------------------------------------------------------

def saved_email(conn, first: str, rows: List[Dict[str, Any]], unsub: str) -> Dict[str, str]:
    pa = _pa()
    n = len(rows)
    subject = (f"Your {n} saved brands are ready to pitch" if n > 1
               else f"{rows[0].get('brand_name') or 'Your saved brand'} is ready to pitch")
    for r in rows:
        r["cta"] = "Pitch it"
    cards = _cards(conn, rows[:3], lambda r: pa.chip_link("pitch_brand", brand_id=r.get("brand_id"),
                                                          brand_name=r.get("brand_name") or "", kind="saved_brands"))
    lines = ["You saved these in Brands. I'll write each pitch for them, sent from your own Gmail.", cards]
    if n > 3:
        lines.append(f"Plus {n - 3} more saved.")
    lines.append(PRO_LINE_SAVED)
    buttons = [{"label": "Pitch my saved brands",
                "href": pa.chip_link("pitch_targets", kind="saved_brands")}]
    return {"subject": subject, "html": pa.render_email(first, subject, lines, buttons, unsub)}


def email_saved_brands(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                       test_email: str = "") -> List[Dict[str, Any]]:
    pa = _pa()
    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    cur = pa._cursor(conn)
    try:
        cur.execute("""
            SELECT t.creator_id, t.brand_id, b.brand_name, t.created_at
            FROM creator_brand_targets t JOIN pr_brands b ON b.id = t.brand_id
            WHERE t.created_at < %s
              AND NOT EXISTS (SELECT 1 FROM polly_drafts d WHERE d.creator_id = t.creator_id AND d.brand_id = t.brand_id)
              AND NOT EXISTS (SELECT 1 FROM polly_autopilot_queue q
                              WHERE q.creator_id = t.creator_id AND q.brand_id = t.brand_id)
            ORDER BY t.creator_id, t.created_at
        """, (pa.utc_now() - SAVED_AFTER,))
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as err:
        conn.rollback()
        print(f"[moment emails] saved brands skipped: {err}")
        return []
    by_creator: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_creator.setdefault(int(r["creator_id"]), []).append(r)
    out = []
    for cid, saved in list(by_creator.items())[:limit]:
        who = pa._recipient(conn, cid, kind="saved_brands")
        if not who:
            continue
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = saved_email(conn, who["first"], saved, unsub)
        item = {"creator_id": cid, "kind": "saved_brands", "subject": mail["subject"], "to": who["email"]}
        if not dry_run:
            result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "saved_brands")
            pa._log_email(conn, cid, "saved_brands", saved[0]["brand_id"], None, mail["subject"], result)
            item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


# ----- niche_gifting ----------------------------------------------------------------------------------------

def niche_email(conn, first: str, niche_label: str, rows: List[Dict[str, Any]], is_pro: bool,
                unsub: str) -> Dict[str, str]:
    pa = _pa()
    n = len(rows)
    subject = f"{n} {niche_label} brand{'s' if n != 1 else ''} started gifting creators this week"
    for r in rows:
        r["cta"] = "Pitch it"
    cards = _cards(conn, rows[:3], lambda r: pa.chip_link("pitch_brand", brand_id=r.get("brand_id"),
                                                          brand_name=r.get("brand_name") or "", kind="niche_gifting"))
    lines = [f"My daily TikTok scan caught these brands sending PR to {niche_label} creators your size.", cards]
    if not is_pro:
        lines.append(PRO_LINE_NICHE)
    buttons = [{"label": "See them in Brands", "href": _feed_link("niche_gifting")}]
    return {"subject": subject, "html": pa.render_email(first, subject, lines, buttons, unsub)}


def email_niche_gifting(conn, limit: int = 60, dry_run: bool = False, send_fn: Optional[Callable] = None,
                        test_email: str = "") -> List[Dict[str, Any]]:
    """Mondays: brands newly confirmed gifting (TikTok scan) in each creator's niche, not pitched yet."""
    pa = _pa()
    if pa.utc_now().weekday() != 0 and not (dry_run or test_email):
        return []
    from services.radar_extract import radar_niche
    send = send_fn or pa._default_send
    cur = pa._cursor(conn)
    try:
        cur.execute("""
            SELECT b.id AS brand_id, b.brand_name, LOWER(b.category) AS category
            FROM pr_brands b
            WHERE COALESCE(b.status, 'published') = 'published'
              AND to_jsonb(b) ->> 'gifting_signal' = 'tiktok'
              AND (to_jsonb(b) ->> 'gifting_confirmed_at')::timestamptz > %s
        """, (pa.utc_now() - NICHE_WINDOW,))
        fresh = [dict(r) for r in cur.fetchall()]
        if not fresh:
            return []
        cur.execute(f"""
            SELECT c.id AS creator_id, c.niche, c.creator_niches
            FROM creators c
            WHERE {pa.WAITLIST_EXCLUDE_SQL}
              AND EXISTS (SELECT 1 FROM polly_usage_events e WHERE e.creator_id = c.id AND e.event = 'open'
                          AND e.created_at > NOW() - INTERVAL '60 days')
              AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.kind = 'niche_gifting'
                              AND l.sent_at > NOW() - INTERVAL '6 days')
            LIMIT %s
        """, (limit * 3,))
        creators = [dict(r) for r in cur.fetchall()]
    except Exception as err:
        conn.rollback()
        print(f"[moment emails] niche gifting skipped: {err}")
        return []
    out = []
    for c in creators:
        if len(out) >= limit:
            break
        parts = list(c.get("creator_niches") or []) + [c.get("niche") or ""]
        niche = next((radar_niche(p) for p in parts if radar_niche(p)), "")
        cats = set(NICHE_CATEGORIES.get(niche) or [])
        if not cats:
            continue
        mine = [b for b in fresh if b.get("category") in cats]
        if not mine:
            continue
        cur.execute("""SELECT brand_id FROM polly_drafts WHERE creator_id = %s AND brand_id = ANY(%s)""",
                    (c["creator_id"], [b["brand_id"] for b in mine]))
        pitched = {r["brand_id"] for r in cur.fetchall()}
        mine = [dict(b) for b in mine if b["brand_id"] not in pitched][:3]
        if not mine:
            continue
        who = pa._recipient(conn, c["creator_id"], kind="niche_gifting")
        if not who:
            continue
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = niche_email(conn, who["first"], niche, mine, who["is_pro"], unsub)
        item = {"creator_id": c["creator_id"], "kind": "niche_gifting", "subject": mail["subject"], "to": who["email"]}
        if not dry_run:
            result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "niche_gifting")
            pa._log_email(conn, c["creator_id"], "niche_gifting", mine[0]["brand_id"], None, mail["subject"], result)
            item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


# ----- first_pitch (onboarding) -----------------------------------------------------------------------------

FIRST_PITCH_AFTER = timedelta(days=2)
FIRST_PITCH_WINDOW = timedelta(days=14)


def first_pitch_email(conn, first: str, rows: List[Dict[str, Any]], unsub: str) -> Dict[str, str]:
    pa = _pa()
    subject = "Your 3 brand matches are still waiting"
    for r in rows:
        r["cta"] = "Pitch it"
    cards = _cards(conn, rows[:3], lambda r: pa.chip_link("pitch_brand", brand_id=r.get("brand_id"),
                                                          brand_name=r.get("brand_name") or "", kind="first_pitch"))
    lines = ["I matched you with these brands when you joined. Your 3 free pitches are ready to use: "
             "I write each one for that brand, you send it from your own Gmail.", cards,
             "Most creators get their first reply within a week of sending."]
    from urllib.parse import urlencode
    polly = f"{pa.frontend_url()}{pa.POLLY_PATH}?" + urlencode(
        {"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": "first_pitch"})
    buttons = [{"label": "Write my 3 free pitches", "href": polly}]  # their matches + the write-all button
    return {"subject": subject, "html": pa.render_email(first, subject, lines, buttons, unsub)}


def email_first_pitch(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                      test_email: str = "") -> List[Dict[str, Any]]:
    """Approved creators who saw their matches in Polly but never had a pitch written, 2-14 days in. Once."""
    pa = _pa()
    send = send_fn or pa._default_send
    cur = pa._cursor(conn)
    now = pa.utc_now()
    try:
        cur.execute(f"""
            SELECT c.id AS creator_id, t.suggested_brands
            FROM creators c JOIN polly_threads t ON t.creator_id = c.id
            WHERE {pa.WAITLIST_EXCLUDE_SQL}
              AND c.created_at < %s AND c.created_at > %s
              AND EXISTS (SELECT 1 FROM polly_usage_events e WHERE e.creator_id = c.id AND e.event = 'matches_shown')
              AND NOT EXISTS (SELECT 1 FROM polly_drafts d WHERE d.creator_id = c.id)
              AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.kind = 'first_pitch')
            LIMIT %s
        """, (now - FIRST_PITCH_AFTER, now - FIRST_PITCH_WINDOW, limit))
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as err:
        conn.rollback()
        print(f"[moment emails] first pitch skipped: {err}")
        return []
    out = []
    for row in rows:
        brands = row.get("suggested_brands") or []
        if isinstance(brands, str):
            import json
            try:
                brands = json.loads(brands)
            except ValueError:
                brands = []
        picks = [{"brand_id": b.get("id"), "brand_name": b.get("name")} for b in brands
                 if isinstance(b, dict) and b.get("id") and b.get("name")][:3]
        if not picks:
            continue
        who = pa._recipient(conn, row["creator_id"], kind="first_pitch")
        if not who:
            continue
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = first_pitch_email(conn, who["first"], picks, unsub)
        item = {"creator_id": row["creator_id"], "kind": "first_pitch", "subject": mail["subject"], "to": who["email"]}
        if not dry_run:
            result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "first_pitch")
            pa._log_email(conn, row["creator_id"], "first_pitch", picks[0]["brand_id"], None, mail["subject"], result)
            item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
