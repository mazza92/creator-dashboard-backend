"""Hotlist + one daily "hot brand" per creator, from Meta-ads brands that passed the quality gate.

- ``hotlist_for_creator``: gate-passed meta_ads_library brands (plus admin approvals) in the creator's niches, strict
  (services.polly_discovery.categories_for_niches: only true synonyms are added), not pitched, queued or avoided by
  the creator, each with a reason a creator can read ("Running 9 UGC-style ads since Oct 28 · Gifted 4 creators
  your size").
- ``assign_hot_brands``: once a day, ONE brand per active creator with recorded niches (and every Pro creator with
  niches). Nothing passes -> nothing stored (never padded). Pro creators with Autopilot on and a working sender get
  the pitch written by Autopilot's own path (routes.polly_autopilot._write_draft) and queued; it takes the
  creator's next free send slot by swapping with the earliest scheduled pitch, so daily send caps don't move.
- ``hot_brand_today``: what the Tracker / daily brief shows.

Country is not a matching signal (no shipping-region filter).
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from psycopg2.extras import RealDictCursor

ACTIVE_DAYS = 30          # creators active (login or Polly thread) in this window get a daily pick
REPEAT_AFTER_DAYS = 30    # a brand is not someone's hot brand twice within this window
HOT_ORIGIN = "hot_brand"
# Same rule as services.polly_autopilot.PRO_SQL, read through to_jsonb so a database without the columns still works.
PRO_SQL_C = ("(COALESCE(to_jsonb(c) ->> 'unlocks_tier', '') = 'pro'"
             " OR COALESCE(to_jsonb(c) ->> 'subscription_tier', '') = 'pro')")

DDL = """
CREATE TABLE IF NOT EXISTS creator_hot_brands (
    creator_id INTEGER NOT NULL,
    day DATE NOT NULL,
    brand_id INTEGER NOT NULL,
    reason TEXT,
    queued_item_id INTEGER,
    draft_error TEXT,
    draft_attempted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (creator_id, day)
)
"""
_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_index

    cur = conn.cursor()
    cur.execute("SELECT to_regclass('creator_hot_brands') IS NOT NULL")
    if not cur.fetchone()[0]:
        cur.execute(DDL)
    ensure_index(cur, "CREATE INDEX IF NOT EXISTS idx_creator_hot_brands_brand ON creator_hot_brands (creator_id, brand_id)")
    conn.commit()
    _READY = True


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _rows(conn, sql: str, params: tuple = (), label: str = "") -> List[Dict[str, Any]]:
    """A read that may hit a table this database doesn't have yet: empty instead of a failed transaction."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SAVEPOINT hot_read")
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT hot_read")
        return rows
    except Exception as err:
        cur.execute("ROLLBACK TO SAVEPOINT hot_read")
        print(f"[hot brands] {label or 'read'} skipped: {type(err).__name__}: {err}")
        return []


# ----- the pool (gate-passed brands) ------------------------------------------------------------------------------

def passing_pool(conn) -> List[Dict[str, Any]]:
    """Every brand a creator may be shown as hot: gate passed (manual approve included), emailable."""
    from services.brand_quality_gate import _ready

    _ready(conn)
    return _rows(conn, """
        SELECT b.id, b.brand_name, LOWER(COALESCE(b.category, '')) AS category, b.description, b.hero_product,
               b.logo_url, b.website, to_jsonb(b) ->> 'target_audience' AS target_audience,
               s.active_ad_count, s.ads_first_seen, s.ads_last_seen, s.ad_library_url, q.override
        FROM pr_brands b
        JOIN brand_quality_checks q ON q.brand_id = b.id AND q.passed
        LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
        WHERE NULLIF(TRIM(b.contact_email), '') IS NOT NULL
          AND (q.override = 'approved' OR (b.source = 'meta_ads_library' AND b.status = 'published'))
    """, label="pool")


def _ids(rows: Iterable[Dict[str, Any]], key: str = "brand_id") -> Set[int]:
    return {int(r[key]) for r in rows if r.get(key) is not None}


def exclusions(conn, creator_ids: List[int], include_recent_hot: bool = False, day: Optional[date] = None
               ) -> Dict[int, Set[int]]:
    """Brand ids each creator must not be shown: queued (any status, a skip is a no), pitched in their pipeline,
    and (for the daily pick) their hot brands of the last REPEAT_AFTER_DAYS."""
    out: Dict[int, Set[int]] = {int(c): set() for c in creator_ids}
    if not creator_ids:
        return out
    ids = [int(c) for c in creator_ids]
    sources = [
        ("queue", "SELECT creator_id, brand_id FROM polly_autopilot_queue WHERE creator_id = ANY(%s)", (ids,)),
        ("pipeline", """SELECT creator_id, brand_id FROM creator_pipeline
                        WHERE creator_id = ANY(%s) AND brand_id IS NOT NULL
                          AND (pitched_at IS NOT NULL OR send_confirmed IS TRUE)""", (ids,)),
    ]
    if include_recent_hot:
        day = day or _today()
        sources.append(("recent hot", """SELECT creator_id, brand_id FROM creator_hot_brands
                                         WHERE creator_id = ANY(%s) AND day > %s AND day < %s""",
                        (ids, day - timedelta(days=REPEAT_AFTER_DAYS), day)))
    for label, sql, params in sources:
        for row in _rows(conn, sql, params, label=label):
            if row.get("brand_id") is not None:
                out.setdefault(int(row["creator_id"]), set()).add(int(row["brand_id"]))
    return out


def load_creators(conn, creator_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    rows = _rows(conn, """
        SELECT c.id, c.user_id, c.creator_niches, c.niche,
               GREATEST(COALESCE((to_jsonb(c) ->> 'followers_count')::numeric, 0),
                        COALESCE((to_jsonb(c) ->> 'social_follower_count')::numeric, 0))::bigint AS followers,
               t.notes
        FROM creators c LEFT JOIN polly_threads t ON t.creator_id = c.id
        WHERE c.id = ANY(%s)""", ([int(c) for c in creator_ids],), label="creators")
    if not rows:  # no polly_threads table (fresh DB): creators alone
        rows = _rows(conn, """
            SELECT c.id, c.user_id, c.creator_niches, c.niche,
                   GREATEST(COALESCE((to_jsonb(c) ->> 'followers_count')::numeric, 0),
                            COALESCE((to_jsonb(c) ->> 'social_follower_count')::numeric, 0))::bigint AS followers
            FROM creators c WHERE c.id = ANY(%s)""", ([int(c) for c in creator_ids],), label="creators")
    return {int(r["id"]): r for r in rows}


def gifted_followers(conn, brand_ids: List[int]) -> Dict[int, List[int]]:
    """Follower counts of the distinct TikTok creators the gifting radar saw posting each brand's gifted product."""
    if not brand_ids:
        return {}
    rows = _rows(conn, """
        SELECT d.brand_id, v.creator_handle, MAX(v.creator_followers) AS followers
        FROM discovered_brands d
        JOIN brand_signals s ON s.discovered_brand_id = d.id AND s.signal_type = 'gifted'
        JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE d.brand_id = ANY(%s)
        GROUP BY d.brand_id, v.creator_handle""", ([int(b) for b in brand_ids],), label="gifted")
    out: Dict[int, List[int]] = {}
    for r in rows:
        out.setdefault(int(r["brand_id"]), []).append(int(r.get("followers") or 0))
    return out


def your_size(followers: int, counts: List[int]) -> int:
    """Gifted creators within ~3x of this creator's following either way."""
    if not followers or not counts:
        return 0
    lo, hi = followers / 3.0, max(followers * 3.0, 3000)
    return sum(1 for n in counts if lo <= n <= hi)


def _day_label(d: Optional[date], today: date) -> str:
    if not d:
        return ""
    return f"{d:%b} {d.day}" if d.year == today.year else f"{d:%b} {d.day}, {d.year}"


def reason_for(brand: Dict[str, Any], gifted: int = 0, today: Optional[date] = None) -> str:
    today = today or _today()
    n = int(brand.get("active_ad_count") or 0)
    first = brand.get("ads_first_seen")
    if isinstance(first, datetime):
        first = first.date()
    if n:
        text = f"Running {n} UGC-style ad{'s' if n != 1 else ''}"
        if first:
            text += f" since {_day_label(first, today)}"
    else:
        text = "Hand-picked by the Newcollab team"
    if gifted:
        text += f" · Gifted {gifted} creator{'s' if gifted != 1 else ''} your size"
    return text


def _notes(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def rank_for_creator(creator: Dict[str, Any], pool: List[Dict[str, Any]], excluded: Set[int],
                     gifted: Dict[int, List[int]], today: Optional[date] = None, limit: int = 30
                     ) -> List[Dict[str, Any]]:
    """Pure: the creator's hotlist from the pool. Strict niches, exclusions, avoid-preferences, reasons."""
    from services.creator_niches import recorded_niches
    from services.polly_discovery import categories_for_niches
    from services.polly_prefs import filter_brands_by_prefs

    niches = recorded_niches(creator)
    if not niches:
        return []
    cats = set(categories_for_niches(niches))
    notes = _notes(creator.get("notes"))
    pitched = set()
    try:
        from services.polly import pitched_id_set
        pitched = pitched_id_set(notes)
    except Exception:
        pass
    pitched_names = {str(n).strip().lower() for n in notes.get("pitched_brand_names") or [] if n}
    today = today or _today()
    followers = int(creator.get("followers") or 0)
    picks = []
    for b in pool:
        bid = int(b["id"])
        if b.get("category") not in cats or bid in excluded or bid in pitched:
            continue
        if str(b.get("brand_name") or "").strip().lower() in pitched_names:
            continue
        picks.append({**b, "name": b.get("brand_name")})
    picks = filter_brands_by_prefs(picks, notes)
    out = []
    for b in picks:
        g = your_size(followers, gifted.get(int(b["id"]), []))
        out.append({
            "brand_id": int(b["id"]), "brand_name": b.get("brand_name"), "logo": b.get("logo_url"),
            "category": b.get("category"), "website": b.get("website"), "active_ad_count": b.get("active_ad_count"),
            "ads_first_seen": b.get("ads_first_seen"), "ads_last_seen": b.get("ads_last_seen"),
            "ad_library_url": b.get("ad_library_url"), "gifted_your_size": g,
            "reason": reason_for(b, g, today),
        })

    def key(r):
        last = r.get("ads_last_seen")
        return (-(r["gifted_your_size"] > 0), -int(r.get("active_ad_count") or 0),
                -(last.toordinal() if isinstance(last, date) else 0), r["brand_id"])

    out.sort(key=key)
    return out[:limit]


def hotlist_for_creator(conn, creator_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """Gate-passed Meta-ads brands for this creator, best first:
    [{brand_id, brand_name, logo, category, website, active_ad_count, ads_first_seen, ads_last_seen,
      ad_library_url, gifted_your_size, reason}]."""
    creator = load_creators(conn, [int(creator_id)]).get(int(creator_id))
    if not creator:
        return []
    pool = passing_pool(conn)
    if not pool:
        return []
    excluded = exclusions(conn, [int(creator_id)]).get(int(creator_id), set())
    gifted = gifted_followers(conn, [int(b["id"]) for b in pool])
    return rank_for_creator(creator, pool, excluded, gifted, limit=limit)


# ----- daily assignment -------------------------------------------------------------------------------------------

def daily_candidates(conn, day: date) -> List[int]:
    """Creators active in the last ACTIVE_DAYS (login or Polly thread activity) plus every Pro creator.
    Niches are checked by the caller (recorded_niches), so creators without niches simply get nothing."""
    since = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=ACTIVE_DAYS)
    rows = _rows(conn, f"""
        SELECT c.id FROM creators c
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN polly_threads t ON t.creator_id = c.id
        WHERE u.last_login > %s OR t.updated_at > %s OR {PRO_SQL_C}
        ORDER BY c.id""", (since, since), label="candidates")
    return [int(r["id"]) for r in rows]


def assign_hot_brands(conn, day: Optional[date] = None, creator_ids: Optional[List[int]] = None,
                      draft: bool = True, draft_fn: Optional[Callable] = None,
                      time_budget: float = 200.0) -> Dict[str, Any]:
    """Pick today's ONE hot brand per eligible creator (idempotent per day), then queue Pro pitches.
    Returns counts. Nothing passing for a creator -> no row (never padded)."""
    ensure_table(conn)
    day = day or _today()
    started = time.monotonic()
    ids = [int(c) for c in creator_ids] if creator_ids is not None else daily_candidates(conn, day)
    have = _ids(_rows(conn, "SELECT creator_id FROM creator_hot_brands WHERE day = %s AND creator_id = ANY(%s)",
                      (day, ids), label="assigned"), "creator_id")
    todo = [c for c in ids if c not in have]
    out = {"day": day.isoformat(), "candidates": len(ids), "already": len(have), "assigned": 0, "none": 0,
           "no_niche": 0, "drafted": 0, "draft_failed": 0}
    pool = passing_pool(conn) if todo else []
    if todo and not pool:
        out["none"] = len(todo)
    if pool:
        gifted = gifted_followers(conn, [int(b["id"]) for b in pool])
        cur = conn.cursor()
        for start in range(0, len(todo), 200):
            chunk = todo[start:start + 200]
            creators = load_creators(conn, chunk)
            excluded = exclusions(conn, chunk, include_recent_hot=True, day=day)
            for cid in chunk:
                creator = creators.get(cid)
                from services.creator_niches import recorded_niches
                if not creator or not recorded_niches(creator):
                    out["no_niche"] += 1
                    continue
                picks = rank_for_creator(creator, pool, excluded.get(cid, set()), gifted, today=day, limit=1)
                if not picks:
                    out["none"] += 1
                    continue
                pick = picks[0]
                cur.execute(
                    """INSERT INTO creator_hot_brands (creator_id, day, brand_id, reason) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (creator_id, day) DO NOTHING""",
                    (cid, day, pick["brand_id"], pick["reason"]))
                out["assigned"] += cur.rowcount
            conn.commit()
    if draft:
        left = max(0.0, time_budget - (time.monotonic() - started))
        got = draft_pending(conn, day, draft_fn=draft_fn, time_budget=left)
        out["drafted"], out["draft_failed"] = got["drafted"], got["failed"]
        out["draft_left"] = got["left"]
    return out


def autopilot_creators(conn, creator_ids: List[int]) -> Set[int]:
    """Of these, the Pro creators with Autopilot on and a working sender (Gmail or Newcollab relay)."""
    from services.polly_autopilot import PRO_SQL, SENDER_SQL

    if not creator_ids:
        return set()
    rows = _rows(conn, f"""
        SELECT a.creator_id FROM polly_autopilot a
        LEFT JOIN polly_gmail_accounts g ON g.creator_id = a.creator_id AND g.revoked_at IS NULL
        JOIN creators pc ON pc.id = a.creator_id AND {PRO_SQL} AND {SENDER_SQL}
        WHERE a.enabled AND a.creator_id = ANY(%s)""", ([int(c) for c in creator_ids],), label="autopilot")
    return _ids(rows, "creator_id")


def draft_pending(conn, day: Optional[date] = None, draft_fn: Optional[Callable] = None,
                  time_budget: float = 200.0, limit: int = 200) -> Dict[str, int]:
    """Write + queue today's hot pitch for Autopilot creators who don't have it yet. One attempt per row:
    draft_attempted_at is set first, so a slow or failing pitch never loops. Re-running continues where it left."""
    ensure_table(conn)
    day = day or _today()
    draft_fn = draft_fn or queue_hot_pitch
    rows = _rows(conn, """SELECT creator_id, brand_id, reason FROM creator_hot_brands
                          WHERE day = %s AND queued_item_id IS NULL AND draft_attempted_at IS NULL
                          ORDER BY creator_id LIMIT %s""", (day, int(limit)), label="pending")
    eligible = autopilot_creators(conn, [r["creator_id"] for r in rows])
    rows = [r for r in rows if int(r["creator_id"]) in eligible]
    deadline = time.monotonic() + max(0.0, time_budget)
    out = {"drafted": 0, "failed": 0, "left": 0}
    cur = conn.cursor()
    for i, row in enumerate(rows):
        if time.monotonic() >= deadline:
            out["left"] = len(rows) - i
            break
        cid, bid = int(row["creator_id"]), int(row["brand_id"])
        cur.execute("UPDATE creator_hot_brands SET draft_attempted_at = NOW() WHERE creator_id = %s AND day = %s",
                    (cid, day))
        conn.commit()
        try:
            item_id, error = draft_fn(conn, cid, bid, row.get("reason") or "")
        except Exception as err:
            try:
                conn.rollback()
            except Exception:
                pass
            item_id, error = None, f"error: {type(err).__name__}: {err}"[:300]
        cur = conn.cursor()
        cur.execute("UPDATE creator_hot_brands SET queued_item_id = %s, draft_error = %s WHERE creator_id = %s AND day = %s",
                    (item_id, None if item_id else (error or "draft_failed"), cid, day))
        conn.commit()
        out["drafted" if item_id else "failed"] += 1
    return out


def queue_hot_pitch(conn, creator_id: int, brand_id: int, reason: str) -> Tuple[Optional[int], Optional[str]]:
    """Autopilot's own pitch path for one brand (inbox check, PR package, prefs, pitch_writer.finalize_for, add_draft),
    then tag it hot, schedule it, and move it to the creator's next free send slot."""
    from flask import current_app, session

    from routes.polly_autopilot import _creator_context, _load_creator, _write_draft
    from services.polly_autopilot import approve

    creator = _load_creator(conn, creator_id)
    if not creator:
        return None, "no_creator"
    with current_app.test_request_context("/api/polly/autopilot/fill", method="POST"):
        session["creator_id"] = creator_id
        session["user_id"] = creator["user_id"]
        notes, scrape = _creator_context(conn, creator)
        item, error = _write_draft(conn, creator_id, creator, int(brand_id), notes, scrape)
    if not item:
        return None, error or "draft_failed"
    tag_hot(conn, int(item["id"]), reason)
    approve(conn, creator_id, [item["id"]])
    jump_queue(conn, creator_id, int(item["id"]))
    return int(item["id"]), None


def tag_hot(conn, item_id: int, reason: str) -> None:
    cur = conn.cursor()
    cur.execute("""UPDATE polly_autopilot_queue SET pitch_meta = COALESCE(pitch_meta, '{}'::jsonb) || %s::jsonb
                   WHERE id = %s""", (json.dumps({"origin": HOT_ORIGIN, "hot": True, "reason": reason}), item_id))
    conn.commit()


def jump_queue(conn, creator_id: int, item_id: int, now: Optional[datetime] = None) -> bool:
    """Swap the hot pitch's slot with the earliest upcoming scheduled pitch, so it goes out first (today when the
    creator sends today) while every day keeps the same number of sends."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT scheduled_for FROM polly_autopilot_queue WHERE id = %s AND status = 'approved'", (item_id,))
    row = cur.fetchone()
    if not row or not row.get("scheduled_for"):
        return False
    cur.execute(
        """SELECT id, scheduled_for FROM polly_autopilot_queue
           WHERE creator_id = %s AND id <> %s AND status = 'approved' AND sent_at IS NULL
             AND scheduled_for >= %s AND scheduled_for < %s
             AND COALESCE(pitch_meta ->> 'origin', '') <> %s
           ORDER BY scheduled_for LIMIT 1""",
        (creator_id, item_id, now, row["scheduled_for"], HOT_ORIGIN))
    other = cur.fetchone()
    if not other:
        return False
    cur.execute("UPDATE polly_autopilot_queue SET scheduled_for = %s WHERE id = %s", (row["scheduled_for"], other["id"]))
    cur.execute("UPDATE polly_autopilot_queue SET scheduled_for = %s WHERE id = %s", (other["scheduled_for"], item_id))
    conn.commit()
    return True


# ----- read helper ------------------------------------------------------------------------------------------------

def hot_brand_today(conn, creator_id, day=None):
    """{brand_id, brand_name, logo, reason, queued_item_id} for the creator's hot brand of ``day`` (UTC today by
    default), or None. A brand an admin rejected since the pick is not shown."""
    day = day or _today()
    if isinstance(day, str):
        day = date.fromisoformat(day[:10])
    elif isinstance(day, datetime):
        day = day.date()
    rows = _rows(conn, """
        SELECT h.brand_id, b.brand_name, b.logo_url AS logo, h.reason, h.queued_item_id
        FROM creator_hot_brands h
        JOIN pr_brands b ON b.id = h.brand_id
        LEFT JOIN brand_quality_checks q ON q.brand_id = h.brand_id
        WHERE h.creator_id = %s AND h.day = %s AND q.override IS DISTINCT FROM 'rejected'""",
                 (int(creator_id), day), label="today")
    if not rows:
        return None
    r = rows[0]
    return {"brand_id": int(r["brand_id"]), "brand_name": r.get("brand_name"), "logo": r.get("logo"),
            "reason": r.get("reason"), "queued_item_id": r.get("queued_item_id")}
