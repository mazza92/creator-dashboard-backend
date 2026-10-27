"""Pitch facts: what every pitch is allowed to say about the creator, confirmed once by them.

One card over the stores that already exist, so Polly, Autopilot, the Directory and PR packages agree:
  followers          creator_stats.save_override (a typed number wins until a sync shows more)
  engagement shown   pitch prefs show_engagement_rate
  niches             creator_niches.save_creator_niches (categories brands are matched on)
  niche_label        notes["pitch_facts"]["niche_label"]: their own words for the pitch ("motherhood and lifestyle")
  what I make        pitch prefs offer_type (gifted_post / gifted_ugc / gifted_ugc_photos / paid_only)
  usage rights       pitch prefs usage_rights_months (0 = never offered)
  ship-to city       creators.shipping_address (+ city / country columns)
  confirmed_at       notes["pitch_facts_confirmed_at"]

When a fact changes, unsent Autopilot pitches are dropped so the next fill rewrites them with the new facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

OFFER_LABELS = {
    "gifted_post": "Gifted: 1 organic post",
    "gifted_ugc": "Gifted: post + a raw UGC video",
    "gifted_ugc_photos": "Gifted: post + UGC video + photos",
    "paid_only": "Paid work only",
}
CONFIRMED_KEY = "pitch_facts_confirmed_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _creator(conn, creator_id: int) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT to_jsonb(c) AS c, u.country AS user_country FROM creators c
                   LEFT JOIN users u ON u.id = c.user_id WHERE c.id = %s""", (creator_id,))
    row = cur.fetchone() or {}
    out = dict(row.get("c") or {})
    if not out.get("country") and row.get("user_country"):
        out["country"] = row["user_country"]
    return out


def _scrape(conn, creator: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from routes.polly import _load_scrape
        return _load_scrape(conn, creator.get("user_id")) or {}
    except Exception:
        conn.rollback()
        return {}


def offer_for(notes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the pitch templates offer: {offer_type, usage_rights_months, show_engagement}. Unset = gifted post."""
    from services.polly_prefs import get_prefs

    prefs = get_prefs(notes)
    return {
        "niche_label": ((notes or {}).get("pitch_facts") or {}).get("niche_label") or "",
        "offer_type": prefs.get("offer_type") or None,
        "usage_rights_months": prefs.get("usage_rights_months"),
        "show_engagement": prefs.get("show_engagement_rate") is not False,
    }


def load(conn, creator_id: int) -> Dict[str, Any]:
    from services.creator_niches import recorded_niches
    from services.creator_stats import pitch_stats
    from services.polly import resolve_pitch_location
    from services.polly_memory import load_thread

    notes = load_thread(conn, creator_id).get("notes") or {}
    creator = _creator(conn, creator_id)
    scrape = _scrape(conn, creator)
    try:
        snap = pitch_stats(conn, creator_id, creator.get("user_id"), scrape, notes)["snap"]
    except Exception as err:
        conn.rollback()
        print(f"[pitch-facts] stats skipped: {err}")
        snap = {}
    loc = resolve_pitch_location(creator, scrape, notes)
    offer = offer_for(notes)
    try:
        from services.creator_niches import NICHE_IDS
        options: List[str] = list(NICHE_IDS)
    except Exception:
        options = []
    return {
        "followers": int(snap.get("followers") or 0) or None,
        "followers_source": "you" if snap.get("source") == "manual" else "synced",
        "stats_synced_at": snap.get("synced_at"),
        "engagement_rate": snap.get("engagement_rate"),
        "show_engagement": offer["show_engagement"],
        "niches": recorded_niches(creator),
        "niche_label": offer["niche_label"],
        "niche_options": options,
        "offer_type": offer["offer_type"],
        "offer_options": [{"id": k, "label": v} for k, v in OFFER_LABELS.items()],
        "usage_rights_months": offer["usage_rights_months"],
        "city": "" if loc.get("needs_location") else (loc.get("city") or ""),
        "country": "" if loc.get("needs_location") else (loc.get("country") or ""),
        "confirmed_at": notes.get(CONFIRMED_KEY),
        "needs_confirm": not notes.get(CONFIRMED_KEY),
    }


def _save_location(conn, creator_id: int, city: str, country: str) -> None:
    city, country = str(city or "").strip()[:80], str(country or "").strip()[:80]
    if not city:
        return
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT to_jsonb(c) -> 'shipping_address' AS a, to_jsonb(c) ? 'city' AS has_city "
                "FROM creators c WHERE c.id = %s", (creator_id,))
    row = cur.fetchone() or {}
    addr = dict(row.get("a") or {}) if isinstance(row.get("a"), dict) else {}
    addr["city"] = city
    if country:
        addr["country"] = country
    cur.execute("UPDATE creators SET shipping_address = %s WHERE id = %s", (Json(addr), creator_id))
    if row.get("has_city"):  # the profile city wins over the address when pitches resolve a location
        cur.execute("UPDATE creators SET city = %s WHERE id = %s", (city, creator_id))
        if country:
            cur.execute("UPDATE creators SET country = %s WHERE id = %s", (country, creator_id))
    conn.commit()


def _drop_unsent_autopilot(conn, creator_id: int) -> int:
    try:
        cur = conn.cursor()
        cur.execute("""DELETE FROM polly_autopilot_queue
                       WHERE creator_id = %s AND status IN ('draft', 'approved') AND sent_at IS NULL""", (creator_id,))
        n = cur.rowcount
        cur.execute("UPDATE polly_autopilot SET filled_at = NULL WHERE creator_id = %s", (creator_id,))
        conn.commit()
        return n
    except Exception as err:
        conn.rollback()
        print(f"[pitch-facts] autopilot reset skipped: {err}")
        return 0


def save(conn, creator_id: int, patch: Dict[str, Any], confirm: bool = False) -> Dict[str, Any]:
    """Apply the edits, mark confirmed when asked, and rewrite unsent pitches if anything changed."""
    from services.creator_niches import save_creator_niches
    from services.creator_stats import save_override
    from services.polly_memory import load_thread, save_notes
    from services.polly_prefs import merge_prefs

    before = load(conn, creator_id)
    notes = dict(load_thread(conn, creator_id).get("notes") or {})
    changed = []
    followers = patch.get("followers")
    if followers not in (None, "") and int(float(str(followers).replace(",", "") or 0)) != (before["followers"] or 0):
        notes = save_override(conn, creator_id, notes, followers)
        changed.append("followers")
    prefs_patch: Dict[str, Any] = {}
    if "show_engagement" in patch and bool(patch["show_engagement"]) != before["show_engagement"]:
        prefs_patch["show_engagement_rate"] = bool(patch["show_engagement"])
    if "offer_type" in patch and (patch["offer_type"] or None) != before["offer_type"]:
        if patch["offer_type"] and patch["offer_type"] not in OFFER_LABELS:
            raise ValueError("unknown offer_type")
        prefs_patch["offer_type"] = patch["offer_type"] or None
    if "usage_rights_months" in patch and patch["usage_rights_months"] != before["usage_rights_months"]:
        prefs_patch["usage_rights_months"] = patch["usage_rights_months"]
    if prefs_patch:
        notes = merge_prefs(notes, prefs_patch)
        changed += list(prefs_patch)
    if "niche_label" in patch:
        label = " ".join(str(patch.get("niche_label") or "").split())[:60]
        if label != before["niche_label"]:
            notes["pitch_facts"] = {**(notes.get("pitch_facts") or {}), "niche_label": label}
            changed.append("niche_label")
    if confirm:
        notes[CONFIRMED_KEY] = _now()
    save_notes(conn, creator_id, notes)
    if isinstance(patch.get("niches"), list) and [n.lower() for n in patch["niches"]] != [n.lower() for n in before["niches"]]:
        save_creator_niches(conn, creator_id, patch["niches"])
        changed.append("niches")
    if patch.get("city") and (patch.get("city"), patch.get("country") or "") != (before["city"], before["country"]):
        _save_location(conn, creator_id, patch["city"], patch.get("country") or "")
        changed.append("city")
    rewritten = _drop_unsent_autopilot(conn, creator_id) if changed else 0
    return {**load(conn, creator_id), "changed": changed, "rewritten": rewritten}
