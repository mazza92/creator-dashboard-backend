"""Promote Newcollab: creators post UGC about Newcollab and earn per view.

Flow: a creator applies with their handles, an admin approves, and that unlocks the brief, a referral link and
the earnings dashboard. They paste each posted TikTok or Reel; a daily cron refreshes views (Apify, once a day,
not live, since every refresh costs credits). Views freeze FREEZE_DAYS after posting, then an admin pays out
by hand (PayPal or Wise) and marks it paid.

Pay: CPM_CENTS per 1,000 views from MIN_VIEWS, capped per video, per creator per month, and by the program
budget. Earnings are allocated in submission order, so the budget never goes past BUDGET_CENTS: once it's used,
later videos earn nothing more and new submissions pause. A creator whose referral link brings a creator who
renews Pro once earns PRO_BONUS_CENTS (also counted against the budget).
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

CPM_CENTS = 250                 # $2.50 per 1,000 views (above SideShift's typical view bonus, ~2x Whop's median)
MIN_VIEWS = 2000                # nothing below this: 1K-view posts rarely bring signups
VIDEO_CAP_CENTS = 4000          # $40 per video (16K views)
CREATOR_MONTH_CAP_CENTS = 15000  # $150 per creator per calendar month (by submission)
BUDGET_CENTS = int(os.getenv("UGC_PROGRAM_BUDGET_CENTS") or 50000)  # $500 total
PRO_BONUS_CENTS = 1000          # $10 per referred creator after their first Pro renewal
FREEZE_DAYS = 30
SPIKE_MIN_VIEWS = 10000         # engagement check only kicks in past this
SPIKE_MIN_ENGAGEMENT = 0.003    # likes+comments under 0.3% of views on a big video: hold for review

PLATFORMS = ("tiktok", "instagram")
_TIKTOK = re.compile(r"^https?://(www\.|m\.|vm\.|vt\.)?tiktok\.com/", re.I)
_INSTAGRAM = re.compile(r"^https?://(www\.)?instagram\.com/(reel|reels|p)/[\w-]+", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def platform_for(url: str) -> Optional[str]:
    url = (url or "").strip()
    if _TIKTOK.match(url):
        return "tiktok"
    if _INSTAGRAM.match(url):
        return "instagram"
    return None


def clean_url(url: str) -> str:
    return (url or "").strip().split("?")[0].rstrip("/")


def clean_handle(raw: Any) -> str:
    h = str(raw or "").strip()
    h = re.sub(r"^https?://(www\.)?(tiktok|instagram)\.com/@?", "", h, flags=re.I)
    return h.lstrip("@").split("/")[0].split("?")[0].strip().lower()


def new_ref_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].lower()


def raw_cents(views: Any) -> int:
    """Uncapped pay for a view count."""
    v = int(views or 0)
    return 0 if v < MIN_VIEWS else v * CPM_CENTS // 1000


def _month(at: Any) -> str:
    if isinstance(at, datetime):
        return at.strftime("%Y-%m")
    return str(at or "")[:7]


def allocate(videos: Iterable[Dict[str, Any]], bonuses: Iterable[Dict[str, Any]] = (),
             budget_cents: Optional[int] = None) -> Tuple[Dict[int, Dict[str, Any]], int]:
    """Earnings per video (and bonus), in submission order, under every cap. Returns ({id: row}, used_cents).

    videos: dicts with id, creator_id, views, submitted_at, status. Rejected videos earn nothing.
    bonuses: dicts with id, created_at (always $10 unless the budget is gone).
    Each output row: cents, capped_by ('' | 'min_views' | 'video' | 'month' | 'budget').
    """
    budget_cents = BUDGET_CENTS if budget_cents is None else budget_cents
    items = [("v", dict(v)) for v in videos] + [("b", dict(b)) for b in bonuses]
    items.sort(key=lambda kv: (str(kv[1].get("submitted_at") or kv[1].get("created_at") or ""), kv[0], kv[1]["id"]))
    used = 0
    month_used: Dict[Tuple[int, str], int] = {}
    out: Dict[Any, Dict[str, Any]] = {}
    for kind, row in items:
        if kind == "b":
            cents = min(PRO_BONUS_CENTS, max(0, budget_cents - used))
            out[("b", row["id"])] = {"cents": cents, "capped_by": "" if cents == PRO_BONUS_CENTS else "budget"}
            used += cents
            continue
        if row.get("status") == "rejected":
            out[row["id"]] = {"cents": 0, "capped_by": ""}
            continue
        want = raw_cents(row.get("views"))
        capped_by = "min_views" if want == 0 and int(row.get("views") or 0) > 0 else ""
        if want > VIDEO_CAP_CENTS:
            want, capped_by = VIDEO_CAP_CENTS, "video"
        key = (row.get("creator_id"), _month(row.get("submitted_at")))
        room = CREATOR_MONTH_CAP_CENTS - month_used.get(key, 0)
        if want > room:
            want, capped_by = max(0, room), "month"
        if want > budget_cents - used:
            want, capped_by = max(0, budget_cents - used), "budget"
        month_used[key] = month_used.get(key, 0) + want
        used += want
        out[row["id"]] = {"cents": want, "capped_by": capped_by}
    return out, used


def budget_open(used_cents: int, budget_cents: Optional[int] = None) -> bool:
    """New submissions pause once less than one minimum-view video's pay is left."""
    budget_cents = BUDGET_CENTS if budget_cents is None else budget_cents
    return budget_cents - used_cents >= raw_cents(MIN_VIEWS)


def frozen(posted_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    return bool(posted_at) and (now or _now()) - posted_at >= timedelta(days=FREEZE_DAYS)


def looks_suspicious(views: Any, likes: Any, comments: Any) -> bool:
    v = int(views or 0)
    if v < SPIKE_MIN_VIEWS:
        return False
    return (int(likes or 0) + int(comments or 0)) / v < SPIKE_MIN_ENGAGEMENT


def dollars(cents: Any) -> str:
    c = int(cents or 0)
    return f"${c // 100:,}" if c % 100 == 0 else f"${c / 100:,.2f}"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

import traceback  # noqa: E402

from psycopg2.extras import RealDictCursor  # noqa: E402

_READY = False
REFRESH_EVERY = timedelta(hours=20)
IG_PER_PASS = 40


def ensure_tables(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ugc_program_members (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'applied',
            tiktok_handle TEXT,
            instagram_handle TEXT,
            why TEXT,
            payout_method TEXT,
            payout_handle TEXT,
            ref_code TEXT UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMPTZ,
            decided_note TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ugc_program_videos (
            id SERIAL PRIMARY KEY,
            member_id INTEGER NOT NULL,
            creator_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            external_id TEXT,
            author TEXT,
            posted_at TIMESTAMPTZ,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            views_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'counting',
            note TEXT,
            earned_cents INTEGER NOT NULL DEFAULT 0,
            capped_by TEXT,
            paid_cents INTEGER,
            paid_at TIMESTAMPTZ,
            payout_ref TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ugc_program_referrals (
            id SERIAL PRIMARY KEY,
            member_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ugc_program_bonuses (
            id SERIAL PRIMARY KEY,
            member_id INTEGER NOT NULL,
            referred_user_id INTEGER NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            earned_cents INTEGER NOT NULL DEFAULT 0,
            paid_at TIMESTAMPTZ,
            payout_ref TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ugc_program_runs (
            id SERIAL PRIMARY KEY,
            apify_run_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'running',
            videos INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ugc_videos_member ON ugc_program_videos (member_id, submitted_at)")
    conn.commit()
    _READY = True


def _cur(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def member_for(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT * FROM ugc_program_members WHERE creator_id = %s", (creator_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def ref_link(code: str) -> str:
    from services.creator_directory_search import frontend_base

    return f"{frontend_base().rstrip('/')}/register/creator?promo={code}"


# ---------------------------------------------------------------------------
# Earnings over the whole program (allocation depends on everyone's submission order)
# ---------------------------------------------------------------------------

def recompute(conn) -> int:
    """Re-run the allocation for every video and bonus and store each one's earnings. Returns cents used."""
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT id, creator_id, views, submitted_at, status FROM ugc_program_videos")
    videos = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT id, created_at FROM ugc_program_bonuses")
    bonuses = [dict(r) for r in cur.fetchall()]
    out, used = allocate(videos, bonuses)
    for v in videos:
        res = out[v["id"]]
        cur.execute("UPDATE ugc_program_videos SET earned_cents = %s, capped_by = %s WHERE id = %s",
                    (res["cents"], res["capped_by"] or None, v["id"]))
    for b in bonuses:
        cur.execute("UPDATE ugc_program_bonuses SET earned_cents = %s WHERE id = %s", (out[("b", b["id"])]["cents"], b["id"]))
    conn.commit()
    return used


def budget_used(conn) -> int:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("""SELECT COALESCE((SELECT SUM(earned_cents) FROM ugc_program_videos WHERE status <> 'rejected'), 0)
                        + COALESCE((SELECT SUM(earned_cents) FROM ugc_program_bonuses), 0) AS used""")
    return int((cur.fetchone() or {}).get("used") or 0)


def program_public(conn) -> Dict[str, Any]:
    used = budget_used(conn)
    return {"cpm_cents": CPM_CENTS, "min_views": MIN_VIEWS, "video_cap_cents": VIDEO_CAP_CENTS,
            "creator_month_cap_cents": CREATOR_MONTH_CAP_CENTS, "budget_cents": BUDGET_CENTS,
            "budget_left_cents": max(0, BUDGET_CENTS - used), "open": budget_open(used),
            "pro_bonus_cents": PRO_BONUS_CENTS, "freeze_days": FREEZE_DAYS}


# ---------------------------------------------------------------------------
# Creator actions
# ---------------------------------------------------------------------------

def apply(conn, creator_id: int, body: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    tiktok = clean_handle(body.get("tiktok_handle"))
    instagram = clean_handle(body.get("instagram_handle"))
    if not tiktok and not instagram:
        return None, "Add your TikTok or Instagram handle"
    why = str(body.get("why") or "").strip()[:600]
    method = str(body.get("payout_method") or "").strip().lower()
    handle = str(body.get("payout_handle") or "").strip()[:200]
    if method not in ("paypal", "wise"):
        return None, "Choose PayPal or Wise for payouts"
    if not handle:
        return None, "Add your PayPal or Wise email"
    existing = member_for(conn, creator_id)
    if existing and existing["status"] in ("approved", "paused"):
        return existing, None
    cur = _cur(conn)
    cur.execute(
        """INSERT INTO ugc_program_members (creator_id, status, tiktok_handle, instagram_handle, why, payout_method, payout_handle)
           VALUES (%s, 'applied', %s, %s, %s, %s, %s)
           ON CONFLICT (creator_id) DO UPDATE SET status = 'applied', tiktok_handle = EXCLUDED.tiktok_handle,
             instagram_handle = EXCLUDED.instagram_handle, why = EXCLUDED.why, payout_method = EXCLUDED.payout_method,
             payout_handle = EXCLUDED.payout_handle, applied_at = NOW(), decided_at = NULL, decided_note = NULL
           RETURNING *""",
        (creator_id, tiktok or None, instagram or None, why, method, handle),
    )
    row = dict(cur.fetchone())
    conn.commit()
    return row, None


def update_payout(conn, creator_id: int, body: Dict[str, Any]) -> Optional[str]:
    method = str(body.get("payout_method") or "").strip().lower()
    handle = str(body.get("payout_handle") or "").strip()[:200]
    if method not in ("paypal", "wise") or not handle:
        return "Choose PayPal or Wise and add the email"
    cur = _cur(conn)
    cur.execute("UPDATE ugc_program_members SET payout_method = %s, payout_handle = %s WHERE creator_id = %s",
                (method, handle, creator_id))
    conn.commit()
    return None


def _resolve_short(url: str) -> str:
    """vm.tiktok.com / vt.tiktok.com links redirect to the full /@handle/video/<id> URL."""
    import requests

    try:
        resp = requests.get(url, allow_redirects=True, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        return resp.url or url
    except Exception:
        return url


_TT_FULL = re.compile(r"tiktok\.com/@([\w.]+)/video/(\d+)", re.I)
_IG_CODE = re.compile(r"instagram\.com/(?:reel|reels|p)/([\w-]+)", re.I)


def tiktok_posted_at(video_id: str) -> Optional[datetime]:
    """TikTok video ids carry their creation time in the top 32 bits."""
    try:
        return datetime.fromtimestamp(int(video_id) >> 32, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _instagram_meta(code: str) -> Optional[Dict[str, Any]]:
    from services.inhouse_social_scraper import _ig_fetch_embed_post_meta, _session

    return _ig_fetch_embed_post_meta(_session(), code)


def submit_video(conn, creator_id: int, raw_url: str, *, ig_fetch=None,
                 resolve=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    member = member_for(conn, creator_id)
    if not member or member["status"] != "approved":
        return None, "Your application isn't approved yet"
    if not budget_open(budget_used(conn)):
        return None, "This round's budget is used up. We'll email you when the next one opens."
    url = (raw_url or "").strip()
    platform = platform_for(url)
    if not platform:
        return None, "Paste a TikTok video or Instagram Reel link"
    approved_at = member.get("decided_at")
    if platform == "tiktok":
        if not _TT_FULL.search(url):
            url = (resolve or _resolve_short)(url)
        m = _TT_FULL.search(url)
        if not m:
            return None, "Paste the full TikTok link, like tiktok.com/@you/video/123"
        author, vid = m.group(1).lower(), m.group(2)
        if member.get("tiktok_handle") and author != member["tiktok_handle"]:
            return None, f"This video is from @{author}, not your account @{member['tiktok_handle']}"
        posted = tiktok_posted_at(vid)
        clean = f"https://www.tiktok.com/@{author}/video/{vid}"
        meta = {"views": 0, "likes": 0, "comments": 0}
    else:
        m = _IG_CODE.search(url)
        if not m:
            return None, "Paste the Reel link, like instagram.com/reel/ABC123"
        vid = m.group(1)
        clean = f"https://www.instagram.com/reel/{vid}"
        got = (ig_fetch or _instagram_meta)(vid) or {}
        owner = clean_handle(got.get("ownerUsername"))
        if owner and member.get("instagram_handle") and owner != member["instagram_handle"]:
            return None, f"This Reel is from @{owner}, not your account @{member['instagram_handle']}"
        author = owner or member.get("instagram_handle")
        posted = None
        if got.get("timestamp"):
            try:
                posted = datetime.fromisoformat(str(got["timestamp"]).replace("Z", "+00:00"))
            except ValueError:
                posted = None
        meta = {"views": int(got.get("videoViewCount") or 0), "likes": int(got.get("likesCount") or 0),
                "comments": int(got.get("commentsCount") or 0)}
    if posted and approved_at and posted < approved_at - timedelta(hours=1):
        return None, "Only videos posted after you joined count. Post a new one for Newcollab."
    cur = _cur(conn)
    cur.execute("SELECT 1 FROM ugc_program_videos WHERE url = %s", (clean,))
    if cur.fetchone():
        return None, "This video is already submitted"
    cur.execute(
        """INSERT INTO ugc_program_videos (member_id, creator_id, platform, url, external_id, author, posted_at,
               views, likes, comments, views_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (member["id"], creator_id, platform, clean, vid, author, posted, meta["views"], meta["likes"], meta["comments"],
         _now() if meta["views"] else None),
    )
    row = dict(cur.fetchone())
    conn.commit()
    recompute(conn)
    return row, None


def _video_public(v: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    posted = v.get("posted_at")
    pays_on = (posted + timedelta(days=FREEZE_DAYS)) if posted else None
    return {
        "id": v["id"], "platform": v["platform"], "url": v["url"], "views": int(v.get("views") or 0),
        "likes": int(v.get("likes") or 0), "status": v["status"], "note": v.get("note") or "",
        "earned_cents": int(v.get("earned_cents") or 0), "capped_by": v.get("capped_by") or "",
        "paid_cents": v.get("paid_cents"),
        "posted_at": posted.isoformat() if posted else None,
        "views_at": v["views_at"].isoformat() if v.get("views_at") else None,
        "submitted_at": v["submitted_at"].isoformat() if v.get("submitted_at") else None,
        "pays_on": pays_on.isoformat() if pays_on else None,
        "counting": v["status"] == "counting" and not frozen(posted, now),
    }


def dashboard(conn, creator_id: int) -> Dict[str, Any]:
    ensure_tables(conn)
    now = _now()
    out: Dict[str, Any] = {"program": program_public(conn), "member": None, "videos": [], "totals": None,
                           "referrals": None}
    m = member_for(conn, creator_id)
    if not m:
        return out
    out["member"] = {
        "status": m["status"], "tiktok_handle": m.get("tiktok_handle") or "",
        "instagram_handle": m.get("instagram_handle") or "", "payout_method": m.get("payout_method") or "",
        "payout_handle": m.get("payout_handle") or "", "note": m.get("decided_note") or "",
        "ref_link": ref_link(m["ref_code"]) if m.get("ref_code") and m["status"] == "approved" else "",
        "applied_at": m["applied_at"].isoformat() if m.get("applied_at") else None,
    }
    if m["status"] not in ("approved", "paused"):
        return out
    cur = _cur(conn)
    cur.execute("SELECT * FROM ugc_program_videos WHERE member_id = %s ORDER BY submitted_at DESC", (m["id"],))
    videos = [_video_public(dict(v), now) for v in cur.fetchall()]
    cur.execute("SELECT COUNT(*) AS n FROM ugc_program_referrals WHERE member_id = %s", (m["id"],))
    signups = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(earned_cents), 0) AS earned, "
                "COALESCE(SUM(earned_cents) FILTER (WHERE paid_at IS NOT NULL), 0) AS paid "
                "FROM ugc_program_bonuses WHERE member_id = %s", (m["id"],))
    b = cur.fetchone() or {}
    live = [v for v in videos if v["status"] != "rejected"]
    earned = sum(v["earned_cents"] for v in live) + int(b.get("earned") or 0)
    paid = sum(int(v["paid_cents"] or 0) for v in live if v["status"] == "paid") + int(b.get("paid") or 0)
    month = now.strftime("%Y-%m")
    month_cents = sum(v["earned_cents"] for v in live if (v["submitted_at"] or "")[:7] == month)
    out["videos"] = videos
    out["totals"] = {"earned_cents": earned, "paid_cents": paid, "owed_cents": max(0, earned - paid),
                     "views": sum(v["views"] for v in live), "month_cents": month_cents,
                     "month_left_cents": max(0, CREATOR_MONTH_CAP_CENTS - month_cents)}
    out["referrals"] = {"signups": signups, "pro_bonuses": int(b.get("n") or 0)}
    return out


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def decide_member(conn, member_id: int, action: str, note: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    status = {"approve": "approved", "reject": "rejected", "pause": "paused"}.get(action)
    if not status:
        return None, "Unknown action"
    cur = _cur(conn)
    cur.execute("SELECT * FROM ugc_program_members WHERE id = %s", (member_id,))
    m = cur.fetchone()
    if not m:
        return None, "Not found"
    code = m.get("ref_code") or (new_ref_code() if status == "approved" else None)
    cur.execute("""UPDATE ugc_program_members SET status = %s, decided_at = COALESCE(%s, decided_at, NOW()),
                       decided_note = %s, ref_code = %s WHERE id = %s RETURNING *""",
                (status, _now() if status == "approved" and m["status"] != "approved" else None,
                 (note or "").strip()[:400] or None, code, member_id))
    row = dict(cur.fetchone())
    conn.commit()
    return row, None


def review_video(conn, video_id: int, action: str, note: str = "", payout_ref: str = "") -> Optional[str]:
    cur = _cur(conn)
    cur.execute("SELECT * FROM ugc_program_videos WHERE id = %s", (video_id,))
    v = cur.fetchone()
    if not v:
        return "Not found"
    note = (note or "").strip()[:400] or None
    if action == "reject":
        cur.execute("UPDATE ugc_program_videos SET status = 'rejected', note = %s WHERE id = %s", (note, video_id))
    elif action == "clear":  # a flagged video looks fine: back to counting, or ready if frozen
        status = "ready" if frozen(v.get("posted_at")) else "counting"
        cur.execute("UPDATE ugc_program_videos SET status = %s, note = %s WHERE id = %s", (status, note, video_id))
    elif action == "paid":
        if v["status"] != "ready":
            return "Only videos past their 30 days (ready) can be marked paid"
        cur.execute("""UPDATE ugc_program_videos SET status = 'paid', paid_cents = earned_cents, paid_at = NOW(),
                           payout_ref = %s, note = COALESCE(%s, note) WHERE id = %s""",
                    ((payout_ref or "").strip()[:120] or None, note, video_id))
    else:
        return "Unknown action"
    conn.commit()
    recompute(conn)
    return None


def admin_overview(conn, status: str = "") -> Dict[str, Any]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("""SELECT m.*, cr.username, u.email, u.first_name,
                          (SELECT COUNT(*) FROM ugc_program_videos v WHERE v.member_id = m.id) AS videos,
                          (SELECT COALESCE(SUM(v.earned_cents), 0) FROM ugc_program_videos v
                             WHERE v.member_id = m.id AND v.status <> 'rejected') AS earned_cents,
                          (SELECT COUNT(*) FROM ugc_program_referrals r WHERE r.member_id = m.id) AS signups
                   FROM ugc_program_members m
                   JOIN creators cr ON cr.id = m.creator_id
                   LEFT JOIN users u ON u.id = cr.user_id
                   ORDER BY CASE m.status WHEN 'applied' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, m.applied_at DESC""")
    members = [_jsonable(dict(r)) for r in cur.fetchall()]
    cur.execute("""SELECT v.*, m.payout_method, m.payout_handle, cr.username
                   FROM ugc_program_videos v JOIN ugc_program_members m ON m.id = v.member_id
                   JOIN creators cr ON cr.id = v.creator_id
                   ORDER BY CASE v.status WHEN 'flagged' THEN 0 WHEN 'ready' THEN 1 WHEN 'counting' THEN 2 ELSE 3 END,
                            v.submitted_at DESC LIMIT 300""")
    videos = [_jsonable(dict(r)) for r in cur.fetchall()]
    cur.execute("SELECT b.*, m.payout_method, m.payout_handle FROM ugc_program_bonuses b "
                "JOIN ugc_program_members m ON m.id = b.member_id ORDER BY b.created_at DESC")
    bonuses = [_jsonable(dict(r)) for r in cur.fetchall()]
    used = budget_used(conn)
    owed = sum(int(v["earned_cents"] or 0) for v in videos if v["status"] == "ready")
    paid = sum(int(v.get("paid_cents") or 0) for v in videos if v["status"] == "paid")
    return {"members": members, "videos": videos, "bonuses": bonuses,
            "summary": {"budget_cents": BUDGET_CENTS, "used_cents": used, "owed_cents": owed, "paid_cents": paid,
                        "open": budget_open(used)}}


def _jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


# ---------------------------------------------------------------------------
# Referrals and the Pro bonus
# ---------------------------------------------------------------------------

def record_signup(conn, user_id: int, code: Any) -> bool:
    """A new creator account that arrived through a member's link. Ignores unknown codes and self-referrals."""
    code = re.sub(r"[^a-z0-9]", "", str(code or "").lower())[:16]
    if not code or not user_id:
        return False
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("""SELECT m.id FROM ugc_program_members m JOIN creators cr ON cr.id = m.creator_id
                   WHERE m.ref_code = %s AND m.status IN ('approved', 'paused') AND cr.user_id <> %s""", (code, user_id))
    m = cur.fetchone()
    if not m:
        return False
    cur.execute("INSERT INTO ugc_program_referrals (member_id, user_id) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                (m["id"], user_id))
    conn.commit()
    return bool(cur.rowcount)


def record_renewal(conn, user_id: int) -> bool:
    """A referred creator's Pro renewed (not the first payment): their referrer earns the bonus, once."""
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT member_id FROM ugc_program_referrals WHERE user_id = %s", (user_id,))
    r = cur.fetchone()
    if not r:
        return False
    cur.execute("INSERT INTO ugc_program_bonuses (member_id, referred_user_id) VALUES (%s, %s) "
                "ON CONFLICT (referred_user_id) DO NOTHING", (r["member_id"], user_id))
    added = bool(cur.rowcount)
    conn.commit()
    if added:
        recompute(conn)
    return added


# ---------------------------------------------------------------------------
# Daily view refresh (cron)
# ---------------------------------------------------------------------------

def _due(cur, platform: str, now: datetime, limit: int) -> List[Dict[str, Any]]:
    cur.execute("""SELECT * FROM ugc_program_videos
                   WHERE platform = %s AND status IN ('counting', 'flagged')
                     AND (posted_at IS NULL OR posted_at > %s)
                     AND (views_at IS NULL OR views_at < %s)
                   ORDER BY views_at NULLS FIRST LIMIT %s""",
                (platform, now - timedelta(days=FREEZE_DAYS), now - REFRESH_EVERY, limit))
    return [dict(r) for r in cur.fetchall()]


def _apply_stats(cur, v: Dict[str, Any], views: int, likes: int, comments: int, now: datetime,
                 posted_at: Optional[datetime] = None) -> None:
    views = max(int(views or 0), int(v.get("views") or 0))  # views never go down for pay
    status = v["status"]
    note = v.get("note")
    if status == "counting" and looks_suspicious(views, likes, comments):
        status, note = "flagged", "Views jumped with very little engagement. We're checking before paying."
    cur.execute("""UPDATE ugc_program_videos SET views = %s, likes = %s, comments = %s, views_at = %s,
                       posted_at = COALESCE(posted_at, %s), status = %s, note = %s WHERE id = %s""",
                (views, int(likes or 0), int(comments or 0), now, posted_at, status, note, v["id"]))


def _freeze(cur, now: datetime) -> int:
    cur.execute("""UPDATE ugc_program_videos SET status = 'ready'
                   WHERE status = 'counting' AND posted_at IS NOT NULL AND posted_at <= %s""",
                (now - timedelta(days=FREEZE_DAYS),))
    return cur.rowcount or 0


def refresh(conn, *, now: Optional[datetime] = None, source=None, ig_fetch=None) -> Dict[str, Any]:
    """One cron pass: collect a finished TikTok run, start today's if due, refresh Reels, freeze day-30 videos."""
    ensure_tables(conn)
    now = now or _now()
    if source is None:
        from services import radar_source as source  # noqa: N813
    cur = _cur(conn)
    stats = {"tiktok_updated": 0, "tiktok_started": False, "ig_updated": 0, "frozen": 0}

    cur.execute("SELECT * FROM ugc_program_runs WHERE status = 'running' ORDER BY started_at LIMIT 1")
    run = cur.fetchone()
    if run:
        try:
            state = source.run_state(run["apify_run_id"])
            if state.get("finished"):
                items = source.fetch_items(state["dataset_id"]) if state.get("ok") and state.get("dataset_id") else []
                by_id = {}
                for item in items:
                    n = source.normalize(item)
                    if n:
                        by_id[str(n["tiktok_id"])] = n
                cur.execute("SELECT * FROM ugc_program_videos WHERE platform = 'tiktok' AND status IN ('counting', 'flagged')")
                for v in cur.fetchall():
                    n = by_id.get(str(v["external_id"]))
                    if n:
                        _apply_stats(cur, dict(v), n["views"], n["likes"], int(n.get("comments") or 0), now, n.get("created_at"))
                        stats["tiktok_updated"] += 1
                cur.execute("UPDATE ugc_program_runs SET status = %s, finished_at = NOW() WHERE id = %s",
                            ("done" if state.get("ok") else "failed", run["id"]))
                conn.commit()
            elif now - run["started_at"] > timedelta(hours=3):
                cur.execute("UPDATE ugc_program_runs SET status = 'failed', finished_at = NOW() WHERE id = %s", (run["id"],))
                conn.commit()
        except Exception:
            conn.rollback()
            traceback.print_exc()
    else:
        due = _due(cur, "tiktok", now, 200)
        cur.execute("SELECT 1 FROM ugc_program_runs WHERE started_at > %s LIMIT 1", (now - REFRESH_EVERY,))
        if due and not cur.fetchone():
            try:
                run_id = source.start_posts_run([v["url"] for v in due])
                cur.execute("INSERT INTO ugc_program_runs (apify_run_id, videos) VALUES (%s, %s)", (run_id, len(due)))
                conn.commit()
                stats["tiktok_started"] = True
            except Exception:
                conn.rollback()
                traceback.print_exc()

    fetch = ig_fetch or _instagram_meta
    for v in _due(cur, "instagram", now, IG_PER_PASS):
        try:
            got = fetch(v["external_id"]) or None
        except Exception:
            got = None
        if not got:
            cur.execute("UPDATE ugc_program_videos SET views_at = %s WHERE id = %s", (now, v["id"]))
            continue
        posted = None
        if got.get("timestamp"):
            try:
                posted = datetime.fromisoformat(str(got["timestamp"]).replace("Z", "+00:00"))
            except ValueError:
                posted = None
        _apply_stats(cur, v, got.get("videoViewCount"), got.get("likesCount"), got.get("commentsCount"), now, posted)
        stats["ig_updated"] += 1
    conn.commit()
    stats["frozen"] = _freeze(cur, now)
    conn.commit()
    stats["used_cents"] = recompute(conn)
    return stats


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def build_approved_email(first_name: Any, link: str) -> Dict[str, str]:
    from services.roster_delivery import _first_name, _link, _p, _wrap

    subject = "You're in: get paid to post about Newcollab"
    inner = "".join([
        _p(f"Hi {_first_name(first_name)},"),
        _p(f"You're approved for Promote Newcollab. You earn {dollars(CPM_CENTS)} per 1,000 views on every TikTok or "
           f"Reel you post about Newcollab, up to {dollars(VIDEO_CAP_CENTS)} a video."),
        _p("Open your dashboard for the brief, hook ideas and your referral link. Post, paste the link, and watch "
           "your earnings update every day."),
        _p(f"Your referral link: {link}"),
    ])
    url = _link("/creator/dashboard/promote", "ugc_program", "email")
    return {"subject": subject, "html": _wrap(subject, "Your brief and referral link are ready.", inner, url,
                                              "Open my dashboard")}


def email_approved(conn, member: Dict[str, Any], send_fn=None) -> bool:
    from services.roster_delivery import _default_send

    cur = _cur(conn)
    cur.execute("SELECT u.email, u.first_name FROM creators cr JOIN users u ON u.id = cr.user_id WHERE cr.id = %s",
                (member["creator_id"],))
    who = cur.fetchone()
    if not who or not who.get("email"):
        return False
    mail = build_approved_email(who.get("first_name"), ref_link(member["ref_code"]))
    try:
        res = (send_fn or _default_send)(who["email"], mail["subject"], mail["html"], "ugc_program_approved")
        return bool(res.get("success"))
    except Exception:
        traceback.print_exc()
        return False
