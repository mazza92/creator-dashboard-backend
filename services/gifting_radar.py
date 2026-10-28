"""Gifting Radar (beta): brands publicly gifting or paying creators this creator's size.

Database first: every search warms the shared index, so a niche searched in the last 48h
answers instantly. Otherwise one async Apify run fills only the stale queries, and the
chat card polls until it's ingested. Polly only renders what this module returns.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import radar_source as source
from services.radar_extract import (
    MAX_CREATOR_FOLLOWERS,
    NICHE_WORDS,
    WINDOW_DAYS,
    brand_key,
    brand_signals,
    display_name,
    keep_video,
    key_variants,
    post_niches,
    radar_niche,
    video_niches,
)
from services.radar_queries import SHARED_HASHTAGS, ai_queries, niche_queries

DAILY_CAP_USD = float(os.getenv("RADAR_DAILY_CAP_USD") or "inf")
SEARCHES_PER_DAY = {"free": 1, "pro": 3}
PER_QUERY = 12
ROUND_SIZE = 20
DEEPER_SIZE = 14
AI_QUERIES = 16
AI_QUERIES_DEEPER = 12
MAX_ROUNDS = 2
DEEPER_BELOW = 3
HASHTAG_TTL = timedelta(hours=24)
QUERY_TTL = timedelta(days=3)
FRESH_FOR = timedelta(hours=48)
INSTANT_MIN = 5
PAGE = 7
RUN_TIMEOUT = timedelta(minutes=4)
MIN_CREATORS = 2
DRAFT_NOTE = "TIKTOK RADAR"

CAVEAT = ("Gifting is based on public posts tagged #gifted, #prpackage or similar. "
          "It's a strong signal, not a guarantee they'll pick you.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----- query plan ---------------------------------------------------------------

def query_plan(niche: str, searches: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """PR hashtags, then searches (the niche's fixed bank unless given). Best first."""
    bank = niche_queries(niche)
    tags = list(dict.fromkeys(list(SHARED_HASHTAGS) + bank["hashtags"]))
    plan = [{"key": f"hashtag:{t}", "type": "hashtag", "query": t, "ttl": HASHTAG_TTL} for t in tags]
    plan += [{"key": f"search:{q}", "type": "search", "query": q, "ttl": QUERY_TTL}
             for q in (bank["searches"] if searches is None else searches)]
    return plan


def next_round(conn, niche: str, profile: Optional[Dict[str, str]] = None, avoid: List[str] = (),
               size: int = ROUND_SIZE, write=None) -> List[Dict[str, Any]]:
    """One round of queries: uncached PR hashtags, then narrow sub-niche searches the model writes for
    this creator (each surfaces different creators, so little overlap), topped up from the fixed bank."""
    asked = {str(a).lower() for a in avoid or []}
    first = not asked
    written = ai_queries(niche, profile, sorted(asked), n=AI_QUERIES if first else AI_QUERIES_DEEPER,
                         write=write)
    searches = written + [q for q in niche_queries(niche)["searches"] if q not in written]
    plan = [p for p in query_plan(niche, searches) if p["query"] not in asked]
    return stale_queries(conn, plan)[:size]


def stale_queries(conn, plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT cache_key FROM apify_query_cache WHERE cache_key = ANY(%s) AND expires_at > NOW()",
                ([p["key"] for p in plan],))
    fresh = {r[0] for r in cur.fetchall()}
    return [p for p in plan if p["key"] not in fresh]


# ----- limits ---------------------------------------------------------------------

def free_radar_used(conn, creator_id: int) -> bool:
    """Free creators get one radar result set; after that the radar is Pro."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM discovery_runs WHERE creator_id = %s AND brands_returned > 0 LIMIT 1",
                (creator_id,))
    return cur.fetchone() is not None


def searches_today(conn, creator_id: int) -> int:
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM discovery_runs
                   WHERE creator_id = %s AND served_from <> 'database'
                     AND created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
                (creator_id,))
    return int(cur.fetchone()[0] or 0)


def spend_today(conn) -> float:
    cur = conn.cursor()
    cur.execute("""SELECT COALESCE(SUM(COALESCE(cost_usd, 0)
                                       + CASE WHEN status IN ('running', 'ingesting')
                                              THEN COALESCE((params->>'est_cost')::numeric, 0) ELSE 0 END), 0)
                   FROM discovery_runs
                   WHERE created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""")
    return float(cur.fetchone()[0] or 0)


def last_topup(conn, niche: str) -> Optional[datetime]:
    cur = conn.cursor()
    cur.execute("""SELECT MAX(finished_at) FROM discovery_runs
                   WHERE niche = %s AND status = 'done' AND served_from <> 'database'""", (niche,))
    row = cur.fetchone()
    return row[0] if row else None


# ----- directory resolution ---------------------------------------------------------

def _domain_key(url: Any) -> str:
    host = str(url or "").lower()
    host = host.split("//")[-1].split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return brand_key(host.split(".")[0]) if host else ""


def load_directory(conn) -> Dict[str, Dict[str, Any]]:
    """brand_key -> directory row, keyed by TikTok handle, Instagram handle, name and website.

    Drafts are included so the team sees the link; cards only offer a pitch for published rows."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, brand_name AS name, slug, logo_url, category, contact_email,
                          application_form_url, tiktok_handle, instagram_handle, website, regions
                   FROM pr_brands WHERE COALESCE(status, 'published') IN ('published', 'draft')
                   ORDER BY (COALESCE(status, 'published') = 'published') DESC, id""")
    out: Dict[str, Dict[str, Any]] = {}
    for row in cur.fetchall():
        row = dict(row)
        for raw in (row.get("tiktok_handle"), row.get("instagram_handle"), row.get("name")):
            key = brand_key(raw)
            if len(key) >= 3:
                out.setdefault(key, row)
        dkey = _domain_key(row.get("website"))
        if len(dkey) >= 4:
            out.setdefault(dkey, row)
    return out


def match_directory(key: str, directory: Dict[str, Dict[str, Any]]):
    """(canonical key, directory row or {}) trying run-together country/store tails."""
    for variant in key_variants(key):
        if variant in directory:
            return variant, directory[variant]
    return key, {}


def _upsert_brand(cur, sig: Dict[str, Any], directory: Dict[str, Dict[str, Any]], seen_at) -> int:
    key, row = match_directory(sig["key"], directory)
    cur.execute("""
        INSERT INTO discovered_brands (brand_key, brand_id, name, tiktok_handle, last_signal_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (brand_key) DO UPDATE SET
          brand_id = COALESCE(discovered_brands.brand_id, EXCLUDED.brand_id),
          name = CASE WHEN discovered_brands.brand_id IS NULL AND EXCLUDED.brand_id IS NOT NULL
                      THEN EXCLUDED.name ELSE discovered_brands.name END,
          tiktok_handle = COALESCE(discovered_brands.tiktok_handle, EXCLUDED.tiktok_handle),
          last_signal_at = GREATEST(discovered_brands.last_signal_at, EXCLUDED.last_signal_at)
        RETURNING id
    """, (key, row.get("id"), row.get("name") or display_name(sig["name"]), sig.get("handle") or None, seen_at))
    return int(cur.fetchone()[0])


def relink(conn, directory: Optional[Dict[str, Dict[str, Any]]] = None, *, commit: bool = True) -> int:
    """Link discovered brands that the directory gained (or that a better match rule now finds)."""
    directory = directory if directory is not None else load_directory(conn)
    cur = conn.cursor()
    cur.execute("SELECT id, brand_key FROM discovered_brands WHERE brand_id IS NULL")
    linked = 0
    for bid, key in cur.fetchall():
        canon, row = match_directory(key, directory)
        if row:
            cur.execute("""
                UPDATE discovered_brands SET brand_id = %s, name = %s,
                  brand_key = CASE WHEN EXISTS (SELECT 1 FROM discovered_brands d WHERE d.brand_key = %s)
                                   THEN brand_key ELSE %s END
                WHERE id = %s
            """, (row["id"], row["name"], canon, canon, bid))
            linked += 1
    if commit:
        conn.commit()
    return linked


_CLASSIFY_PROMPT = (
    "You sort consumer brands by what they mainly sell. For each brand name, answer with one niche from "
    "this list: {niches}, or \"other\" if none fits or you don't know the brand. "
    "Return JSON: {{\"brands\": {{\"<name>\": \"<niche>\"}}}}."
)


def brand_niches(brands: Dict[str, str], classify=None) -> Dict[str, str]:
    """name -> radar niche for brands named in posts that don't say their niche.

    brands maps name -> directory category ('' when unknown). Known categories decide; the rest go to
    the model in one call. 'other' means the brand fits no niche; a missing name means we don't know."""
    out: Dict[str, str] = {}
    unknown = []
    for name, category in brands.items():
        niche = radar_niche(category)
        if niche:
            out[name] = niche
        else:
            unknown.append(name)
    if not unknown:
        return out
    if classify is None:
        from services.pr_ready import _call_gemini_json as classify
    allowed = list(NICHE_WORDS)
    try:
        reply = classify(_CLASSIFY_PROMPT.format(niches=", ".join(allowed)), json.dumps(unknown[:80]))
        for name, niche in ((reply or {}).get("brands") or {}).items():
            niche = str(niche or "").strip().lower()
            if name in unknown and (niche in allowed or niche == "other"):
                out[name] = niche
    except Exception as err:
        print(f"[radar] brand classify skipped: {err}")
    return out


def signal_niches(video: Dict[str, Any], query_niche: str, brand_niche: str = "") -> List[str]:
    """Where to file one brand's signal. The post's words win; a post that names no niche (a PR haul
    roundup) goes by what the brand sells, and only falls back to the search's niche when unknown."""
    if video_niches(video):
        return post_niches(video, query_niche)
    if brand_niche == "other":
        return ["general"]
    if brand_niche:
        return [brand_niche]
    return post_niches(video, query_niche)


# ----- ingest -----------------------------------------------------------------------

def ingest(conn, items: List[Dict[str, Any]], niche: str, plan: List[Dict[str, Any]],
           cost_usd: float, now: Optional[datetime] = None, classify=None) -> Dict[str, int]:
    """Store every fetched video, then signals for the small-creator ones. Returns counts."""
    now = now or _now()
    videos = [v for v in (source.normalize(i) for i in items or []) if v]
    directory = load_directory(conn)
    sigs = {v["tiktok_id"]: brand_signals(v, directory.keys()) for v in videos if keep_video(v, now)}
    vague = {}
    for v in videos:
        if v["tiktok_id"] in sigs and not video_niches(v):
            for s in sigs[v["tiktok_id"]]:
                _, row = match_directory(s["key"], directory)
                vague[s["key"]] = (row.get("name") or display_name(s["name"]), row.get("category") or "")
    by_name = brand_niches({name: cat for name, cat in vague.values()}, classify) if vague else {}
    key_niche = {k: by_name.get(name, "") for k, (name, _) in vague.items()}
    cur = conn.cursor()
    kept = signals = 0
    for v in videos:
        c = v["creator"]
        cur.execute("""
            INSERT INTO tiktok_videos (tiktok_id, url, caption, hashtags, mentions, views, likes, created_at,
                                       cover_url, is_ad, is_sponsored, language, creator_handle,
                                       creator_followers, creator_region, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (tiktok_id) DO UPDATE SET views = EXCLUDED.views, likes = EXCLUDED.likes,
              cover_url = CASE WHEN tiktok_videos.cover_url LIKE '%%/storage/v1/object/public/%%'
                               THEN tiktok_videos.cover_url ELSE EXCLUDED.cover_url END,
              creator_followers = EXCLUDED.creator_followers, fetched_at = NOW()
        """, (v["tiktok_id"], v["url"], v["caption"], v["hashtags"], v["mentions"], v["views"], v["likes"],
              v["created_at"], v["cover_url"], v["is_ad"], v["is_sponsored"], v["language"], c["handle"],
              c["followers"], c["region"]))
        cur.execute("""
            INSERT INTO tiktok_creators (handle, followers, region, bio, fetched_at) VALUES (%s,%s,%s,%s,NOW())
            ON CONFLICT (handle) DO UPDATE SET followers = EXCLUDED.followers, bio = EXCLUDED.bio, fetched_at = NOW()
        """, (c["handle"], c["followers"], c["region"], c["bio"]))
        if v["tiktok_id"] not in sigs:
            continue
        kept += 1
        for sig in sigs[v["tiktok_id"]]:
            bid = _upsert_brand(cur, sig, directory, v["created_at"])
            for kind in sig["types"]:
                for n in signal_niches(v, niche, key_niche.get(sig["key"], "")):
                    cur.execute("""INSERT INTO brand_signals (discovered_brand_id, tiktok_id, signal_type, niche)
                                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (bid, v["tiktok_id"], kind, n))
                    signals += cur.rowcount
    share = round(cost_usd / max(1, len(plan)), 4)
    for p in plan:
        if p["type"] == "search":
            ids = [v["tiktok_id"] for v in videos if v.get("query") == p["query"]]
        else:
            ids = [v["tiktok_id"] for v in videos if not v.get("query") and p["query"] in v["hashtags"]]
        cur.execute("""
            INSERT INTO apify_query_cache (cache_key, query_type, query, video_ids, result_count, cost_usd,
                                           fetched_at, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s)
            ON CONFLICT (cache_key) DO UPDATE SET video_ids = EXCLUDED.video_ids,
              result_count = EXCLUDED.result_count, cost_usd = EXCLUDED.cost_usd, fetched_at = NOW(),
              expires_at = EXCLUDED.expires_at
        """, (p["key"], p["type"], p["query"], ids, len(ids), share, now + p["ttl"]))
    relink(conn, directory, commit=False)
    conn.commit()
    stored = _store_covers(conn, videos)
    return {"fetched": len(videos), "kept": kept, "signals": signals, "covers": stored}


INGEST_COVERS = 150


def _store_covers(conn, videos: List[Dict[str, Any]]) -> int:
    """TikTok cover links expire within days, so copy the small-creator ones while they're minutes old."""
    small = [v for v in videos if int(v["creator"].get("followers") or 0) <= MAX_CREATOR_FOLLOWERS * 2]
    small.sort(key=lambda v: -int(v.get("views") or 0))
    try:
        from services.brand_scan import persist_covers

        return persist_covers(conn, small, limit=INGEST_COVERS, budget_s=25.0)
    except Exception as err:
        conn.rollback()
        print(f"[radar] cover store skipped: {type(err).__name__}: {err}")
        return 0


# ----- ranking + cards -------------------------------------------------------------------

def _stats_rows(conn, niche: str, signal_type: str = "any") -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    having = "" if signal_type in ("", "any", None) else "AND BOOL_OR(s.signal_type = %(kind)s)"
    cur.execute(f"""
        SELECT db.id, db.name, db.brand_key, db.brand_id, db.tiktok_handle, db.status,
               COUNT(DISTINCT v.creator_handle) AS creators_60d,
               COUNT(DISTINCT v.creator_handle) FILTER (WHERE v.created_at > NOW() - INTERVAL '30 days') AS creators_30d,
               COUNT(DISTINCT v.creator_handle) FILTER (WHERE v.created_at > NOW() - INTERVAL '14 days') AS creators_14d,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.creator_followers) AS median_followers,
               MAX(v.creator_followers) AS max_followers,
               MAX(v.created_at) AS last_signal_at,
               ARRAY_AGG(DISTINCT s.signal_type) AS signal_types
        FROM brand_signals s
        JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        JOIN discovered_brands db ON db.id = s.discovered_brand_id
        WHERE s.niche = %(niche)s AND v.created_at > NOW() - make_interval(days => %(days)s)
          AND v.creator_followers <= %(cap)s AND db.status <> 'rejected'
        GROUP BY db.id
        HAVING (COUNT(DISTINCT v.creator_handle) >= %(min)s
                OR BOOL_OR(s.signal_type IN ('gifted', 'paid'))) {having}
    """, {"niche": niche, "cap": MAX_CREATOR_FOLLOWERS, "min": MIN_CREATORS, "kind": signal_type,
          "days": WINDOW_DAYS})
    return [dict(r) for r in cur.fetchall()]


def _directory_rows(conn, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, brand_name AS name, slug, logo_url, category, contact_email,
                          application_form_url, regions
                   FROM pr_brands WHERE id = ANY(%s) AND slug IS NOT NULL
                     AND COALESCE(status, 'published') = 'published'""", (ids,))
    return {int(r["id"]): dict(r) for r in cur.fetchall()}


def _examples(conn, brand_ids: List[int], niche: str) -> Dict[int, List[Dict[str, Any]]]:
    if not brand_ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT s.discovered_brand_id AS bid, v.tiktok_id, v.url, v.cover_url, v.views,
               v.creator_handle, v.creator_followers, v.created_at
        FROM brand_signals s JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE s.discovered_brand_id = ANY(%s) AND s.niche = %s
          AND v.created_at > NOW() - make_interval(days => %s) AND v.creator_followers <= %s
    """, (brand_ids, niche, WINDOW_DAYS, MAX_CREATOR_FOLLOWERS))
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in cur.fetchall():
        out.setdefault(int(r["bid"]), []).append(dict(r))
    return out


def score(row: Dict[str, Any], followers: int, region_match: bool, has_contact: bool) -> float:
    median = float(row.get("median_followers") or 0)
    near = bool(followers) and median and 0.5 * followers <= median <= 1.5 * followers
    return (int(row.get("creators_60d") or 0) * 3 + int(row.get("creators_14d") or 0) * 2
            + (3 if near else 0) + (2 if region_match else 0) + (2 if has_contact else 0))


def _k(n: float) -> str:
    n = float(n or 0)
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(int(n))


def _age(when: Optional[datetime], now: datetime) -> str:
    if not when:
        return ""
    days = max(0, (now - when).days)
    return "today" if days == 0 else f"{days}d"


def size_label(followers: int) -> str:
    return "10K" if followers <= 10_000 else "25K" if followers <= 25_000 else "50K"


def evidence_line(row: Dict[str, Any]) -> str:
    size = size_label(int(row.get("max_followers") or 0))
    recent = int(row.get("creators_30d") or 0)
    total = int(row.get("creators_60d") or 0)
    if total < MIN_CREATORS:
        return f"1 creator with {_k(row.get('median_followers'))} followers · last {WINDOW_DAYS} days"
    if recent >= MIN_CREATORS:
        count, window = recent, "last 30 days"
    else:
        count, window = total, f"last {WINDOW_DAYS} days"
    return (f"{count} creators under {size} · {window} · median "
            f"{_k(row.get('median_followers'))} followers")


_BADGES = {"gifted": "🎁 Gifting", "paid": "💸 Paid", "affiliate": "🛍 TikTok Shop / affiliate"}


def pick_examples(videos: List[Dict[str, Any]], followers: int, limit: int = 3) -> List[Dict[str, Any]]:
    """Top videos from creators closest to the user's size, one per creator."""
    def closeness(v):
        f = max(1, int(v.get("creator_followers") or 1))
        return abs(math.log(f / max(1, followers))) if followers else 0

    out, handles = [], set()
    for v in sorted(videos, key=lambda v: (round(closeness(v), 1), -int(v.get("views") or 0))):
        if v["creator_handle"] in handles:
            continue
        handles.add(v["creator_handle"])
        out.append(v)
        if len(out) >= limit:
            break
    return out


def _thumb(cover: Optional[str], url: str) -> str:
    try:
        from media_proxy_routes import to_proxied_media_url
        return to_proxied_media_url(cover, post_url=url) if cover else ""
    except Exception:
        return cover or ""


def build_card(row: Dict[str, Any], directory: Dict[str, Any], videos: List[Dict[str, Any]], *,
               followers: int, country: Optional[str], now: datetime) -> Dict[str, Any]:
    from services.brand_shipping import country_name, ships_to_country

    types = [t for t in ("gifted", "paid", "affiliate") if t in (row.get("signal_types") or [])]
    ships = ships_to_country(directory.get("regions"), country) if directory and country else None
    contact = ("pitch" if directory.get("contact_email") else
               "apply" if directory.get("application_form_url") else "find_contact")
    examples = pick_examples(videos, followers)
    return {
        "id": f"radar-{row['id']}",
        "discovered_brand_id": int(row["id"]),
        "brand_id": directory.get("id") or None,
        "name": directory.get("name") or display_name(row["name"]),
        "slug": directory.get("slug") or None,
        "logo": directory.get("logo_url") or None,
        "category": directory.get("category") or None,
        "badges": [_BADGES[t] for t in types],
        "signal_types": types,
        "headline": ("gifted a creator your size" if int(row.get("creators_60d") or 0) < MIN_CREATORS
                     else "gifting small creators now" if int(row.get("creators_14d") or 0)
                     else "gifting small creators recently"),
        "evidence": evidence_line(row),
        "ships": ("Ships to you" if ships else f"Doesn't list {country_name(country)}" if ships is False
                  else "Shipping not confirmed") if country else None,
        "contact": contact,
        "application_form_url": directory.get("application_form_url") if contact == "apply" else None,
        "in_directory": bool(directory),
        "evidence_data": {
            "creators_30d": int(row.get("creators_30d") or 0),
            "creators_60d": int(row.get("creators_60d") or 0),
            "median_followers": int(row.get("median_followers") or 0),
            "max_followers": int(row.get("max_followers") or 0),
            "last_signal_at": row["last_signal_at"].isoformat() if row.get("last_signal_at") else None,
        },
        "videos": [{
            "url": v["url"], "handle": v["creator_handle"], "followers": _k(v["creator_followers"]),
            "views": _k(v["views"]), "age": _age(v.get("created_at"), now), "thumb": _thumb(v.get("cover_url"), v["url"]),
        } for v in examples],
    }


def ranked(conn, ctx: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Every qualifying brand for this creator, best first (cards)."""
    from services.brand_shipping import ships_to_country

    now = now or _now()
    rows = _stats_rows(conn, ctx["niche"], ctx.get("signal_type") or "any")
    directory = _directory_rows(conn, [int(r["brand_id"]) for r in rows if r.get("brand_id")])
    skip_ids = {int(i) for i in ctx.get("exclude_brand_ids") or [] if str(i).isdigit()}
    avoid = {str(c).lower() for c in ctx.get("avoid_categories") or []}
    followers = int(ctx.get("followers") or 0)
    country = ctx.get("country")
    scored = []
    for r in rows:
        d = directory.get(int(r["brand_id"])) if r.get("brand_id") else {}
        d = d or {}
        if d.get("id") and int(d["id"]) in skip_ids:
            continue
        if d.get("category") and str(d["category"]).lower() in avoid:
            continue
        ships = ships_to_country(d.get("regions"), country) if d and country else None
        if ships is False:
            continue
        scored.append((score(r, followers, bool(ships), bool(d.get("contact_email"))), r, d))
    scored.sort(key=lambda x: (int(x[1].get("creators_60d") or 0) < MIN_CREATORS, -x[0],
                               -(x[1]["last_signal_at"] or now).timestamp()))
    videos = _examples(conn, [int(r["id"]) for _, r, _ in scored[:40]], ctx["niche"])
    return [build_card(r, d, videos.get(int(r["id"]), []), followers=followers, country=country, now=now)
            for _, r, d in scored[:40]]


def envelope(cards: List[Dict[str, Any]], ctx: Dict[str, Any], *, run_id: Optional[int], served_from: str,
             note: str = "") -> Dict[str, Any]:
    offset = int(ctx.get("offset") or 0)
    page = cards[offset:offset + PAGE]
    niche = ctx["niche"]
    if len(page) >= 3:
        status = "found"
        biggest = max(int((b.get("evidence_data") or {}).get("max_followers") or 0) for b in page)
        intro = (f"I found {len(page)} brands that sent PR to {niche} creators under {size_label(biggest)} "
                 f"followers in the last {WINDOW_DAYS} days. They're ranked by how often they work with creators "
                 "your size, not by brand size.")
    elif page:
        status = "weak"
        intro = (f"Only {len(page)} brand{'s' if len(page) != 1 else ''} came up for {niche} this week. "
                 "Want me to widen the search?")
    else:
        status = "none"
        intro = (f"No brands came up gifting {niche} creators your size yet. "
                 "Want me to try a broader niche?")
    return {"status": status, "run_id": run_id, "niche": niche, "brands": page, "total": len(cards),
            "has_more": len(cards) > offset + PAGE, "offset": offset, "served_from": served_from,
            "title": f"Brands gifting {niche} creators like you", "intro": intro,
            "caveat": CAVEAT if page else "", "note": note}


# ----- search + poll ----------------------------------------------------------------------

def _record(conn, ctx: Dict[str, Any], *, status: str, served_from: str, queries=None, run_ids=None,
            params=None, brands: int = 0) -> int:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discovery_runs (creator_id, niche, region, status, served_from, cache_hit, apify_run_ids,
                                    queries, params, brands_returned, finished_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, CASE WHEN %s = 'done' THEN NOW() END)
        RETURNING id
    """, (ctx.get("creator_id"), ctx["niche"], ctx.get("country"), status, served_from,
          served_from == "database", list(run_ids or []), json.dumps(queries or []), json.dumps(params or {}),
          brands, status))
    run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def _inflight(conn, niche: str) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("""SELECT id FROM discovery_runs WHERE niche = %s AND status IN ('running', 'ingesting')
                     AND COALESCE((params->>'round_started_at')::timestamptz, created_at) > NOW() - %s
                   ORDER BY id DESC LIMIT 1""", (niche, RUN_TIMEOUT))
    row = cur.fetchone()
    return int(row[0]) if row else None


_DRAFT_CATEGORY = {"makeup": "beauty", "nails": "beauty", "fragrance": "beauty"}


def save_drafts(conn, cards: List[Dict[str, Any]], niche: str) -> int:
    """Brands we show that aren't in the directory become pr_brands drafts (notes TIKTOK RADAR), so the
    team can review and publish them. No email goes on the draft: outreach tooling reads draft inboxes."""
    ids = [int(c["discovered_brand_id"]) for c in cards if not c.get("brand_id")]
    if not ids:
        return 0
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, name, tiktok_handle, website FROM discovered_brands
                   WHERE id = ANY(%s) AND brand_id IS NULL""", (ids,))
    linked = 0
    for r in cur.fetchall():
        name = display_name(r["name"])[:200]
        cur.execute("""INSERT INTO pr_brands (brand_name, status, source, notes, category, tiktok_handle, website)
                       VALUES (%s, 'draft', 'tiktok_radar', %s, %s, %s, %s)
                       ON CONFLICT (brand_name) DO NOTHING RETURNING id""",
                    (name, DRAFT_NOTE, _DRAFT_CATEGORY.get(niche, niche), r["tiktok_handle"], r["website"]))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM pr_brands WHERE brand_name = %s", (name,))
            row = cur.fetchone()
        if row:
            cur.execute("UPDATE discovered_brands SET brand_id = %s WHERE id = %s AND brand_id IS NULL",
                        (row["id"], r["id"]))
            linked += 1
    conn.commit()
    return linked


def retag_signals(conn, apply: bool = False, classify=None) -> Dict[str, int]:
    """Re-file signals written before signal_niches existed: a post about another niche, or a niche-less
    roundup whose brand sells something else. Then re-categorize radar drafts by their brand's main
    niche. Dry run unless apply=True."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT s.discovered_brand_id, s.tiktok_id, s.signal_type, s.niche, v.caption, v.hashtags,
                          COALESCE(p.brand_name, db.name) AS brand, COALESCE(p.category, '') AS category
                   FROM brand_signals s JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
                   JOIN discovered_brands db ON db.id = s.discovered_brand_id
                   LEFT JOIN pr_brands p ON p.id = db.brand_id
                   WHERE s.niche <> 'general'""")
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["own"] = video_niches({"caption": r["caption"], "hashtags": r["hashtags"]})
    vague = {r["brand"]: r["category"] for r in rows if not r["own"]}
    by_name = brand_niches(vague, classify) if vague else {}
    moved = 0
    for r in rows:
        if r["own"]:
            target = [] if r["niche"] in r["own"] else r["own"]
        else:
            bn = by_name.get(r["brand"], "")
            target = [] if bn in ("", r["niche"]) else (["general"] if bn == "other" else [bn])
        if not target:
            continue
        moved += 1
        if not apply:
            continue
        for n in target:
            cur.execute("""INSERT INTO brand_signals (discovered_brand_id, tiktok_id, signal_type, niche)
                           VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (r["discovered_brand_id"], r["tiktok_id"], r["signal_type"], n))
        cur.execute("""DELETE FROM brand_signals WHERE discovered_brand_id = %s AND tiktok_id = %s
                         AND signal_type = %s AND niche = %s""",
                    (r["discovered_brand_id"], r["tiktok_id"], r["signal_type"], r["niche"]))
    cur.execute("""
        SELECT p.id, p.category, top.niche FROM pr_brands p
        JOIN discovered_brands db ON db.brand_id = p.id
        JOIN LATERAL (SELECT s.niche FROM brand_signals s WHERE s.discovered_brand_id = db.id
                        AND s.niche <> 'general'
                      GROUP BY s.niche ORDER BY COUNT(DISTINCT s.tiktok_id) DESC LIMIT 1) top ON TRUE
        WHERE p.source = 'tiktok_radar' AND p.status = 'draft'""")
    recategorized = 0
    for r in cur.fetchall():
        category = _DRAFT_CATEGORY.get(r["niche"], r["niche"])
        if category == r["category"]:
            continue
        recategorized += 1
        if apply:
            cur.execute("UPDATE pr_brands SET category = %s WHERE id = %s", (category, r["id"]))
    if apply:
        conn.commit()
    else:
        conn.rollback()
    return {"signals_moved": moved, "drafts_recategorized": recategorized}


def _respond(conn, cards: List[Dict[str, Any]], ctx: Dict[str, Any], **kw) -> Dict[str, Any]:
    out = envelope(cards, ctx, **kw)
    try:
        save_drafts(conn, out["brands"], ctx["niche"])
    except Exception as err:
        conn.rollback()
        print(f"[radar] draft save skipped: {err}")
    return out


def _round_started(run: Dict[str, Any]) -> datetime:
    raw = (run.get("params") or {}).get("round_started_at")
    try:
        return datetime.fromisoformat(raw) if raw else run["created_at"]
    except (TypeError, ValueError):
        return run["created_at"]


def _start_round(plan: List[Dict[str, Any]]) -> str:
    return source.start_run([p["query"] for p in plan if p["type"] == "hashtag"],
                            [p["query"] for p in plan if p["type"] == "search"], PER_QUERY)


def _go_deeper(conn, run: Dict[str, Any]) -> bool:
    """A thin first round starts one more on the next unused queries, under the same run id."""
    params = run.get("params") or {}
    if int(params.get("round") or 1) >= MAX_ROUNDS or run["status"] != "done":
        return False
    asked = list(params.get("asked") or []) + [q.get("query") for q in run.get("queries") or []]
    plan = next_round(conn, run["niche"], params.get("profile"), asked, size=DEEPER_SIZE)
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if not plan or spend_today(conn) + est > DAILY_CAP_USD:
        return False
    try:
        apify_id = _start_round(plan)
    except source.SourceUnavailable as err:
        print(f"[radar] deeper round failed to start run={run['id']}: {err}")
        return False
    cur = conn.cursor()
    cur.execute("""UPDATE discovery_runs SET status = 'running', finished_at = NULL,
                     apify_run_ids = array_append(apify_run_ids, %s), queries = %s,
                     params = params || %s::jsonb
                   WHERE id = %s AND status = 'done'""",
                (apify_id, json.dumps([{k: p[k] for k in ("key", "type", "query")} for p in plan]),
                 json.dumps({"round": int(params.get("round") or 1) + 1, "est_cost": est,
                             "round_started_at": _now().isoformat(),
                             "asked": list(dict.fromkeys(asked + [p["query"] for p in plan]))}), run["id"]))
    conn.commit()
    print(f"[radar] run {run['id']} round 2 queries={len(plan)} est=${est}")
    return True


def search(conn, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ctx: creator_id, niche, followers, country, is_pro, exclude_brand_ids, avoid_categories,
    signal_type, offset. Returns an envelope, or {"status": "searching", "run_id"}."""
    niche = radar_niche(ctx.get("niche"))
    if not niche:
        return {"status": "needs_niche"}
    ctx = {**ctx, "niche": niche}
    if niche in {str(c).lower() for c in ctx.get("avoid_categories") or []}:
        return {"status": "excluded_category", "niche": niche}
    cards = ranked(conn, ctx)
    fresh_at = last_topup(conn, niche)
    fresh = bool(fresh_at) and _now() - fresh_at < FRESH_FOR
    if (len(cards) >= INSTANT_MIN and fresh) or int(ctx.get("offset") or 0) > 0:
        run_id = _record(conn, ctx, status="done", served_from="database",
                         brands=len(cards[:PAGE]))
        return _respond(conn, cards, ctx, run_id=run_id, served_from="database")
    running = _inflight(conn, niche)
    if running:
        return searching(running, niche, age_s=0)
    tier = "pro" if ctx.get("is_pro") else "free"
    if searches_today(conn, ctx["creator_id"]) >= SEARCHES_PER_DAY[tier]:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return _respond(conn, cards, ctx, run_id=run_id, served_from="database", note="daily_limit")
    profile = {k: str(v)[:300] for k, v in (ctx.get("profile") or {}).items() if v}
    plan = next_round(conn, niche, profile)
    if not plan:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return _respond(conn, cards, ctx, run_id=run_id, served_from="database")
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if spend_today(conn) + est > DAILY_CAP_USD:
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return _respond(conn, cards, ctx, run_id=run_id, served_from="database", note="budget")
    try:
        apify_id = _start_round(plan)
    except source.SourceUnavailable as err:
        print(f"[radar] start failed: {err}")
        run_id = _record(conn, ctx, status="done", served_from="database", brands=len(cards[:PAGE]))
        return _respond(conn, cards, ctx, run_id=run_id, served_from="database", note="source_down")
    served = "partial_topup" if cards else "full_apify"
    run_id = _record(conn, ctx, status="running", served_from=served, run_ids=[apify_id],
                     queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                     params={"est_cost": est, "per_query": PER_QUERY, "round": 1, "profile": profile,
                             "asked": [p["query"] for p in plan]})
    print(f"[radar] run {run_id} niche={niche} queries={len(plan)} est=${est}")
    return searching(run_id, niche, age_s=0)


def status_line(niche: str, age_s: float, items: int = 0, ingesting: bool = False, deeper: bool = False) -> str:
    if ingesting:
        return "Pulling the best examples…"
    if deeper and not items:
        return f"Only a few so far, so I'm searching more {niche} posts…"
    if items:
        return f"Found {items} posts. Checking which brands work with creators your size…"
    return f"Searching TikTok for gifted and PR posts in {niche}…"


def searching(run_id: int, niche: str, age_s: float, items: int = 0, ingesting: bool = False,
              deeper: bool = False) -> Dict[str, Any]:
    return {"status": "searching", "run_id": run_id, "niche": niche,
            "line": status_line(niche, age_s, items, ingesting, deeper),
            "title": f"Brands gifting {niche} creators like you"}


def _load_run(conn, run_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM discovery_runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _finish(conn, run: Dict[str, Any]) -> Dict[str, Any]:
    """Claim a finished Apify run and ingest it once. Returns {"done": bool, "items": n}."""
    state = source.run_state(run["apify_run_ids"][-1])
    age = _now() - _round_started(run)
    if not state["finished"]:
        if age > RUN_TIMEOUT:
            cur = conn.cursor()
            cur.execute("UPDATE discovery_runs SET status = 'failed', error = 'timeout', finished_at = NOW() "
                        "WHERE id = %s AND status = 'running'", (run["id"],))
            conn.commit()
            return {"done": True, "failed": True}
        return {"done": False, "items": state["items_so_far"]}
    cur = conn.cursor()
    cur.execute("UPDATE discovery_runs SET status = 'ingesting' WHERE id = %s AND status = 'running' RETURNING id",
                (run["id"],))
    claimed = cur.fetchone()
    conn.commit()
    if not claimed:
        return {"done": False, "ingesting": True}
    try:
        items = source.fetch_items(state["dataset_id"]) if state["ok"] and state["dataset_id"] else []
        plan = [{**q, "ttl": HASHTAG_TTL if q["type"] == "hashtag" else QUERY_TTL} for q in run["queries"] or []]
        # Apify's usage total lags the final bill, so the daily cap counts whichever is higher.
        cost = max(float(state["cost_usd"] or 0), source.estimate_cost(len(items)))
        counts = ingest(conn, items, run["niche"], plan, cost)
        cur = conn.cursor()
        cur.execute("""UPDATE discovery_runs SET status = %s, cost_usd = COALESCE(cost_usd, 0) + %s,
                         videos_fetched = COALESCE(videos_fetched, 0) + %s,
                         duration_ms = EXTRACT(EPOCH FROM (NOW() - created_at)) * 1000,
                         finished_at = NOW(), error = %s WHERE id = %s""",
                    ("done" if state["ok"] else "failed", cost, counts["fetched"],
                     None if state["ok"] else state["status"], run["id"]))
        conn.commit()
        print(f"[radar] run {run['id']} ingested {counts} cost=${cost:.3f}")
        return {"done": True}
    except Exception as err:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("UPDATE discovery_runs SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                    (str(err)[:300], run["id"]))
        conn.commit()
        print(f"[radar] ingest failed run={run['id']}: {err}")
        return {"done": True, "failed": True}


def poll(conn, run_id: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    run = _load_run(conn, run_id)
    if not run:
        return {"status": "not_found"}
    niche = run["niche"]
    ctx = {**ctx, "niche": niche}
    finished_now = False
    if run["status"] in ("running", "ingesting"):
        age = (_now() - _round_started(run)).total_seconds()
        deeper = int((run.get("params") or {}).get("round") or 1) > 1
        if run["status"] == "ingesting" and age > (RUN_TIMEOUT + timedelta(minutes=2)).total_seconds():
            cur = conn.cursor()
            cur.execute("UPDATE discovery_runs SET status = 'failed', error = 'ingest stalled', finished_at = NOW() "
                        "WHERE id = %s AND status = 'ingesting'", (run_id,))
            conn.commit()
            run = _load_run(conn, run_id) or run
        elif run["status"] == "ingesting":
            return searching(run_id, niche, age, ingesting=True)
        if run["status"] == "running":
            try:
                step = _finish(conn, run)
            except source.SourceUnavailable as err:
                print(f"[radar] poll failed run={run_id}: {err}")
                return searching(run_id, niche, age, deeper=deeper)
            if not step.get("done"):
                return searching(run_id, niche, age, items=step.get("items") or 0,
                                 ingesting=bool(step.get("ingesting")), deeper=deeper)
            finished_now = not step.get("failed")
            run = _load_run(conn, run_id) or run
    cards = ranked(conn, ctx)
    strong = sum(1 for c in cards if int((c.get("evidence_data") or {}).get("creators_60d") or 0) >= MIN_CREATORS)
    if finished_now and strong < DEEPER_BELOW and _go_deeper(conn, run):
        return searching(run_id, niche, 0, deeper=True)
    if run.get("creator_id") == ctx.get("creator_id") and not run.get("brands_returned"):
        cur = conn.cursor()
        cur.execute("UPDATE discovery_runs SET brands_returned = %s WHERE id = %s", (len(cards[:PAGE]), run_id))
        conn.commit()
    note = "source_down" if run["status"] == "failed" else ""
    return _respond(conn, cards, ctx, run_id=run_id, served_from=run["served_from"], note=note)


def finalize_stale(conn, limit: int = 1) -> None:
    """Ingest runs nobody polled to the end (tab closed), so paid results aren't lost.

    Cron only (polly alerts): ingesting a run (fetch, classify, store covers) can take over a minute, so it
    must never run inside someone's scan request.
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT * FROM discovery_runs WHERE status = 'running'
                     AND COALESCE((params->>'round_started_at')::timestamptz, created_at) < NOW() - INTERVAL '45 seconds'
                   ORDER BY id LIMIT %s""", (limit,))
    for run in [dict(r) for r in cur.fetchall()]:
        try:
            _finish(conn, run)
        except Exception as err:
            conn.rollback()
            print(f"[radar] finalize skipped run={run['id']}: {err}")


def evidence_pitch_line(brand_name: str, niche: str, evidence: Dict[str, Any]) -> str:
    """One natural line for the pitch. Never names the example creators."""
    recent = int((evidence or {}).get("creators_30d") or 0)
    when = "this month" if recent >= MIN_CREATORS else "recently"
    return (f"I've seen {brand_name} sending PR to {niche} creators {when}, and I'd love to be part "
            "of that next wave.")
