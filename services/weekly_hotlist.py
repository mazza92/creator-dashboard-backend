"""Polly's weekly Brand Hotlist: replaces "Your weekly report" on the first Polly open of the week.

Why (Nov 2026): the weekly report posted "Last week: no pitches went out" to creators who had done nothing, and a
recap gives them no reason to act. Fresh brands in their niche that are advertising right now (so hiring creators)
do: every card has Contact, free creators spend a free pitch per brand, and the footer sells Pro (Polly pitches
every hot brand, every week). Pro creators see last week's Autopilot numbers in one line, then the brands.

Brands: the gate-passed Meta-ads pool (services.hot_brands), niche-strict, minus anything queued, pitched or shown
in the last few hotlists; topped up from verified published brands when the pool is thin. Fewer than 3: no post.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

HOTLIST_SIZE = 6
MIN_BRANDS = 3
REPEAT_AFTER = timedelta(days=28)   # a brand comes back in a hotlist only after 4 weeks

_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS creator_hotlist_shown (
        creator_id INTEGER NOT NULL, brand_id INTEGER NOT NULL, shown_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (creator_id, brand_id))""")
    conn.commit()
    _READY = True


def recently_shown(conn, creator_id: int, now: Optional[datetime] = None) -> set:
    ensure_table(conn)
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()
    cur.execute("SELECT brand_id FROM creator_hotlist_shown WHERE creator_id = %s AND shown_at > %s",
                (creator_id, now - REPEAT_AFTER))
    return {int(r[0]) for r in cur.fetchall()}


def record_shown(conn, creator_id: int, brand_ids: List[int]) -> None:
    ensure_table(conn)
    cur = conn.cursor()
    for bid in brand_ids:
        cur.execute("""INSERT INTO creator_hotlist_shown (creator_id, brand_id) VALUES (%s, %s)
                       ON CONFLICT (creator_id, brand_id) DO UPDATE SET shown_at = NOW()""", (creator_id, bid))
    conn.commit()


def _card(b: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe card for the chat message."""
    found = b.get("found_on") or b.get("ads_last_seen")
    return {
        "brand_id": int(b["brand_id"]),
        "brand_name": b.get("brand_name") or "Brand",
        "logo": b.get("logo"),
        "category": (b.get("category") or "").replace("_", " "),
        "reason": b.get("reason") or "",
        "ads": int(b.get("active_ad_count") or 0),
        "gifted_your_size": int(b.get("gifted_your_size") or 0),
        "creator_style": b.get("ugc_style") is True,
        "fresh": bool(found and isinstance(found, date) and (date.today() - found).days <= 14),
    }


def weekly_hotlist(conn, creator_id: int, now: Optional[datetime] = None, limit: int = HOTLIST_SIZE
                   ) -> List[Dict[str, Any]]:
    from services.creator_niches import recorded_niches
    from services.hot_brands import (exclusions, fallback_pool, gifted_followers, load_creators, passing_pool,
                                     rank_for_creator)

    creator = load_creators(conn, [int(creator_id)]).get(int(creator_id))
    if not creator or not recorded_niches(creator):
        return []
    excluded = set(exclusions(conn, [int(creator_id)]).get(int(creator_id), set()))
    excluded |= recently_shown(conn, creator_id, now)
    picks: List[Dict[str, Any]] = []
    pool = passing_pool(conn)
    if pool:
        gifted = gifted_followers(conn, [int(b["id"]) for b in pool])
        picks = rank_for_creator(creator, pool, excluded, gifted, limit=limit)
    if len(picks) < limit:
        taken = excluded | {int(p["brand_id"]) for p in picks}
        picks += rank_for_creator(creator, fallback_pool(conn), taken, {}, limit=limit - len(picks))
    return [_card(p) for p in picks[:limit]]


def hotlist_message(conn, creator_id: int, first: str = "", is_pro: bool = False,
                    now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The weekly chat message, or None when there aren't enough fresh brands to be worth a post."""
    from services.pro_manager import last_week_facts, week_label

    brands = weekly_hotlist(conn, creator_id, now)
    if len(brands) < MIN_BRANDS:
        return None
    niches = sorted({b["category"] for b in brands if b["category"]})[:2]
    niche_txt = " and ".join(niches) if niches else "your niche"
    name = (first or "").strip()
    content = (f"🔥 Your brand hotlist{', ' + name if name else ''}: {len(brands)} {niche_txt} brands worth pitching "
               "this week. Brands running ads are the ones hiring creators, so pitch them while it's hot.")
    pro_line = None
    if is_pro:
        try:
            last = last_week_facts(conn, creator_id, now)
            sent = int(last.get("pitched") or 0)
            replies = len(last.get("reply_names") or [])
            opens = len(last.get("kit_names") or [])
            bits = [f"{sent} pitch{'es' if sent != 1 else ''} sent"]
            if opens:
                bits.append(f"{opens} kit open{'s' if opens != 1 else ''}")
            if replies:
                bits.append(f"{replies} repl{'ies' if replies != 1 else 'y'}")
            pro_line = "Last week Autopilot: " + " · ".join(bits)
        except Exception:
            conn.rollback()
    chips = ([{"id": "pitch_all", "label": "Write pitches for the top 3", "action": "pitch_batch",
               "brand_ids": [b["brand_id"] for b in brands[:3]]}] if is_pro
             else [{"id": "unlock_pro", "label": "Start my free week", "action": "unlock_pro"}])
    record_shown(conn, creator_id, [b["brand_id"] for b in brands])
    return {
        "role": "assistant",
        "kind": "hotlist",
        "content": content,
        "hotlist": {"week": week_label(now), "brands": brands, "is_pro": is_pro, "pro_line": pro_line},
        "task_chips": chips,
    }
