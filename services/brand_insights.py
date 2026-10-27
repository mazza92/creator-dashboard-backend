"""Brand workspace prompt library: niche-level insights for a brand, answered from the Gifting Radar store.

Each prompt is its own query, not a brand scan:
  gifting_brands  Brands seeding small creators in the brand's niche right now (who to watch / copy).
  niche_hooks     Opening lines several creators reuse across the niche (scripts to brief creators on).
  niche_creators  Small creators posting gifted / PR content in the niche, plus vetted Newcollab matches.

Database first. When the niche hasn't been topped up recently, one Apify run of the niche's PR hashtags and
searches fills the store. It's recorded as a normal radar run for that niche, so the creator-side Radar
benefits from it too and nothing is paid twice.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services import brand_scan as scan
from services import insight_agent as agent
from services import gifting_radar as radar
from services import radar_source as source
from services.radar_extract import MAX_CREATOR_FOLLOWERS, brand_key, radar_niche
from services.radar_queries import SHARED_HASHTAGS, niche_queries

KINDS = ("gifting_brands", "niche_hooks", "niche_creators", "ask", "creator_hooks")
# Kinds answered by the insight agent: planned searches across the niche, then a written answer.
# creator_hooks is the creator-side twin of niche_hooks (Polly): same engine, read from creators their size,
# answered to the creator, with the brands gifting that size linked to the Newcollab directory.
DEEP = ("niche_hooks", "niche_creators", "ask", "creator_hooks")
# Follower bands: (label, low, high) for picking creators "their size" out of the pool.
BANDS = (("under 5K", 0, 15_000, 5_000), ("5K to 20K", 2_000, 60_000, 20_000),
         ("20K to 100K", 8_000, 250_000, 100_000), ("100K+", 40_000, 10**9, 10**9))
MIN_BAND_VIDEOS = 15
DEEP_PER_QUERY = 20
ANSWER_FRESH = timedelta(hours=24)
QUESTIONS = {
    "niche_hooks": "Which hooks, opening lines and video formats are working best for {niche} creators on TikTok "
                   "right now, and why?",
    "niche_creators": "Find small {niche} creators on TikTok (under 100K followers) who would be a great fit for a "
                      "gifted product campaign from {brand}, and say what each one is good at.",
}
WINDOW = timedelta(days=60)
FRESH_FOR = timedelta(hours=72)
MIN_VIDEOS = 25
PER_QUERY = 15
SEARCHES = 6
TITLES = {
    "gifting_brands": "Brands seeding creators in {niche}",
    "niche_hooks": "Hooks creators reuse in {niche}",
    "niche_creators": "Creators posting gifted {niche} content",
    "ask": "{niche}",
    "creator_hooks": "Hooks that work for {niche} creators",
}


def size_band(followers: Any) -> str:
    n = int(followers or 0)
    for label, _lo, _hi, top in BANDS:
        if n < top:
            return label
    return BANDS[-1][0]


def creator_question(niche: str, band: str) -> str:
    return (f"Which hooks, opening lines and video formats are working best right now for {niche.replace('_', ' ')} "
            f"creators with {band} followers on TikTok, and which brands are gifting or paying creators that size?")


def same_size(videos: List[Dict[str, Any]], band: str, keep: int = 120) -> List[Dict[str, Any]]:
    """Posts from creators near this band; if too few, the closest ones by follower count."""
    import math

    lo, hi = next(((b[1], b[2]) for b in BANDS if b[0] == band), (0, 10**9))
    inside = [v for v in videos if lo <= int(v.get("creator_followers") or 0) <= hi]
    if len(inside) >= MIN_BAND_VIDEOS:
        return inside[:keep]
    mid = math.log10(max(1000, (lo + min(hi, 10**6)) / 2))
    near = sorted(videos, key=lambda v: abs(math.log10(max(100, int(v.get("creator_followers") or 0))) - mid))
    return sorted(near[:max(keep // 2, MIN_BAND_VIDEOS)], key=lambda v: -int(v.get("views") or 0))


def pool_brands(conn, videos: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    """Brands the radar tied to these exact videos (gifted, paid, tagged), with their directory id when listed."""
    ids = [str(v["tiktok_id"]) for v in videos]
    if not ids:
        return []
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT db.name, db.brand_key, db.brand_id, COUNT(DISTINCT v.creator_handle) AS creators,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.creator_followers) AS median_followers,
                   ARRAY_AGG(DISTINCT s.signal_type) AS types, ARRAY_AGG(DISTINCT v.tiktok_id) AS video_ids
            FROM brand_signals s
            JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
            JOIN discovered_brands db ON db.id = s.discovered_brand_id
            WHERE s.tiktok_id = ANY(%s) AND db.status <> 'rejected'
            GROUP BY db.id ORDER BY COUNT(DISTINCT v.creator_handle) DESC, db.name LIMIT %s
        """, (ids, int(limit)))
        return [{**dict(r), "creators": int(r["creators"] or 0), "median_followers": int(r["median_followers"] or 0),
                 "types": [t for t in (r["types"] or []) if t], "video_ids": [str(x) for x in r["video_ids"] or []]}
                for r in cur.fetchall()]
    except Exception as err:
        conn.rollback()
        print(f"[brand-insight] pool brands skipped: {type(err).__name__}: {err}")
        return []


def _brand_card(b: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": b["name"], "key": b.get("brand_key"), "creators": b.get("creators") or 0,
            "median_followers_label": scan._k(b.get("median_followers")), "types": b.get("types") or [],
            "pitch_id": b.get("brand_id")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Brand categories the radar has no niche for, mapped to the closest one it indexes.
CATEGORY_NICHE = {"luxury": "fashion", "jewelry": "fashion", "activewear": "fitness", "supplements": "wellness",
                  "sustainable": "home", "lifestyle": "home", "travel": "home", "gaming": "tech", "beauty": "makeup"}


def niche_for(category: Any) -> str:
    text = str(category or "").strip().lower()
    return CATEGORY_NICHE.get(text) or radar_niche(text) or ""


def insight_key(kind: str, niche: str, question: str = "") -> str:
    if kind == "ask":
        import hashlib

        return f"insight:ask:{niche}:{hashlib.sha1(question.strip().lower().encode()).hexdigest()[:10]}"
    return f"insight:{kind}:{niche}"


def question_for(kind: str, niche: str, brand: str = "", question: str = "") -> str:
    if kind in ("ask", "creator_hooks"):
        return " ".join(str(question or "").split())[:300]
    return QUESTIONS.get(kind, "").format(niche=niche.replace("_", " "), brand=brand or "our brand")


# ----- data ----------------------------------------------------------------------------------

def niche_videos(conn, niche: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Recent small-creator posts filed under this niche by the radar (any signal), one row per video."""
    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (v.tiktok_id) v.tiktok_id, v.url, v.caption, v.hashtags, v.mentions, v.views, v.likes,
               v.created_at, v.cover_url, v.is_ad, v.is_sponsored, v.creator_handle, v.creator_followers
        FROM brand_signals s JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
        WHERE s.niche = %s AND v.created_at > %s AND v.creator_followers <= %s
        ORDER BY v.tiktok_id
    """, (niche, now - WINDOW, MAX_CREATOR_FOLLOWERS))
    return sorted((dict(r) for r in cur.fetchall()), key=lambda v: -int(v.get("views") or 0))


def gifting_brands(conn, niche: str, exclude_key: str = "", now: Optional[datetime] = None,
                   thumb=None, limit: int = 12) -> List[Dict[str, Any]]:
    """Brands with the most distinct small creators posting their gifted / paid product in the niche."""
    now = now or _now()
    rows = [r for r in radar._stats_rows(conn, niche) if brand_key(r.get("brand_key")) != exclude_key]
    rows.sort(key=lambda r: (-int(r.get("creators_30d") or 0), -int(r.get("creators_60d") or 0)))
    rows = rows[:limit]
    examples = radar._examples(conn, [int(r["id"]) for r in rows], niche)
    out = []
    for r in rows:
        vids = sorted(examples.get(int(r["id"]), []), key=lambda v: -int(v.get("views") or 0))
        seen, picks = set(), []
        for v in vids:
            if v["creator_handle"] in seen:
                continue
            seen.add(v["creator_handle"])
            picks.append(scan.video_card({**v, "caption": "", "hashtags": [], "mentions": []}, r["brand_key"], now, thumb))
            if len(picks) == 3:
                break
        types = [t for t in (r.get("signal_types") or []) if t]
        out.append({
            "name": r["name"],
            "key": r["brand_key"],
            "creators_60d": int(r.get("creators_60d") or 0),
            "creators_30d": int(r.get("creators_30d") or 0),
            "median_followers_label": scan._k(r.get("median_followers")),
            "types": types,
            "last_post": scan._age(r.get("last_signal_at"), now),
            "examples": picks,
        })
    return out


def build(kind: str, conn, niche: str, *, brand_name: str = "", now: Optional[datetime] = None, thumb=None,
          matches=None, scan_id: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    now = now or _now()
    label = niche.replace("_", " ")
    out: Dict[str, Any] = {"status": "ready", "kind": kind, "scan_id": scan_id, "niche": niche,
                           "title": TITLES[kind].format(niche=label), "insight": True}
    own = brand_key(brand_name)
    if kind == "gifting_brands":
        brands = gifting_brands(conn, niche, own, now, thumb)
        out["brands"] = brands
        if brands:
            top = brands[0]
            out["summary"] = (f"**{len(brands)} brands** are seeding small {label} creators right now. "
                              f"**{top['name']}** leads with **{top['creators_30d'] or top['creators_60d']} creators** "
                              f"posting in the last {'30' if top['creators_30d'] else '60'} days. Scan any of them to "
                              "see their full creator list and hooks.")
        else:
            out["status"] = "empty"
            out["summary"] = f"No brands seeding {label} creators in our index yet. Check back shortly."
    else:
        videos = niche_videos(conn, niche, now)
        if kind == "niche_hooks":
            groups = scan.group_hooks(videos)[:10]
            out["hooks"] = [{
                "hook": scan.short_hook(g["videos"][0].get("caption")), "videos": len(g["videos"]),
                "creators": g["creators"], "views": g["views"], "views_label": scan._k(g["views"]),
                "examples": [scan.video_card(v, "", now, thumb) for v in g["videos"][:3]],
            } for g in groups]
            out["top_videos"] = [scan.video_card(v, "", now, thumb) for v in videos[:12]]
            if groups:
                h = out["hooks"][0]
                out["summary"] = (f"Across **{len(videos)} recent {label} creator posts**, **{len(groups)} opening "
                                  f"lines** repeat. The most used, “{h['hook']}”, ran on **{h['videos']} videos** "
                                  f"from {h['creators']} creators. Brief your creators on the ones that fit you.")
            else:
                out["status"] = "empty" if not videos else "ready"
                out["summary"] = (f"No repeated hooks yet across {len(videos)} recent {label} posts. Here are the "
                                  "top videos instead." if videos else f"No recent {label} creator posts indexed yet.")
        else:
            creators = scan.creator_rows(videos, "")[:30]
            out["creators"] = creators
            out["top_videos"] = [scan.video_card(v, "", now, thumb) for v in videos[:12]]
            out["matches"] = matches
            if creators:
                out["summary"] = (f"**{len(creators)} small creators** posted gifted or PR {label} content in the "
                                  "last 60 days. Below are the ones getting the most views, then vetted Newcollab "
                                  "creators who are open to gifting your brand.")
            else:
                out["status"] = "empty"
                out["summary"] = f"No recent {label} creator posts indexed yet. Your Newcollab matches are below."
    if note:
        out["note"] = note
    return out


# ----- runs ------------------------------------------------------------------------------------

def query_plan(niche: str) -> List[Dict[str, Any]]:
    bank = niche_queries(niche)
    tags = list(dict.fromkeys(list(SHARED_HASHTAGS) + bank["hashtags"]))
    plan = [{"key": f"hashtag:{t}", "type": "hashtag", "query": t, "ttl": radar.HASHTAG_TTL} for t in tags]
    plan += [{"key": f"search:{q}", "type": "search", "query": q, "ttl": radar.QUERY_TTL}
             for q in bank["searches"][:SEARCHES]]
    return plan


def _respond(kind, conn, niche, *, brand_name, redis_client, scan_id=None, note=""):
    from services.cover_refresh import refresh

    try:
        if kind == "gifting_brands":
            rows = radar._stats_rows(conn, niche)
            ex = radar._examples(conn, [int(r["id"]) for r in rows[:12]], niche)
            refresh(conn, sorted((v for vs in ex.values() for v in vs), key=lambda v: -int(v.get("views") or 0)),
                    redis_client)
        else:
            refresh(conn, niche_videos(conn, niche), redis_client)
    except Exception as err:
        conn.rollback()
        print(f"[brand-insight] cover refresh skipped: {type(err).__name__}: {err}")
    matches = scan.creator_matches(niche, conn=conn, redis_client=redis_client) if kind == "niche_creators" else None
    return build(kind, conn, niche, brand_name=brand_name, matches=matches, scan_id=scan_id, note=note)


def searching(run_id: int, niche: str, kind: str, items: int = 0, ingesting: bool = False) -> Dict[str, Any]:
    label = niche.replace("_", " ")
    if ingesting:
        step, line = 3, "Grouping brands, hooks and creators…"
    elif items:
        step, line = 2, f"Found {items} {label} creator posts. Reading them…"
    else:
        step, line = 1, f"Searching TikTok for gifted and PR {label} posts…"
    return {"status": "searching", "scan_id": run_id, "kind": kind, "niche": niche, "step": step, "line": line,
            "items": items, "insight": True}


def start(conn, kind: str, niche: str, *, brand_name: str = "", redis_client=None,
          can_start: bool = True, question: str = "", band: str = "") -> Dict[str, Any]:
    if kind not in KINDS or not niche:
        return {"status": "invalid"}
    if kind in DEEP:
        return deep_start(conn, kind, niche, brand_name=brand_name, question=question, redis_client=redis_client,
                          can_start=can_start, band=band)
    radar.finalize_stale(conn)
    count = len(niche_videos(conn, niche))
    fresh_at = radar.last_topup(conn, niche)
    fresh = bool(fresh_at) and _now() - fresh_at < FRESH_FOR
    if (count >= MIN_VIDEOS and fresh) or count >= MIN_VIDEOS * 4:
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client)
    running = radar._inflight(conn, niche)
    if running:
        return searching(running, niche, kind)
    plan = radar.stale_queries(conn, query_plan(niche))
    est = source.estimate_cost(len(plan) * PER_QUERY)
    if not plan or not can_start or radar.spend_today(conn) + est > scan.DAILY_CAP_USD:
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client,
                        note="" if not plan else "limit")
    try:
        apify_id = source.start_run([p["query"] for p in plan if p["type"] == "hashtag"],
                                    [p["query"] for p in plan if p["type"] == "search"], PER_QUERY)
    except Exception as err:
        print(f"[brand-insight] start failed: {type(err).__name__}: {err}")
        return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client, note="source_down")
    run_id = radar._record(conn, {"niche": niche, "creator_id": None}, status="running",
                           served_from="partial_topup" if count else "full_apify", run_ids=[apify_id],
                           queries=[{k: p[k] for k in ("key", "type", "query")} for p in plan],
                           params={"kind": "brand_insight", "insight": kind, "est_cost": est, "per_query": PER_QUERY,
                                   "round": 1, "brand": brand_name[:80]})
    print(f"[brand-insight] run {run_id} niche={niche} kind={kind} queries={len(plan)} est=${est}")
    return {**searching(run_id, niche, kind), "started": True}


def poll(conn, run_id: int, kind: str, *, brand_name: str = "", redis_client=None) -> Dict[str, Any]:
    run = radar._load_run(conn, run_id)
    # Insight runs, or a creator-side radar run of the same niche that was already in flight. Never brand scans.
    if not run or not run.get("niche") or str(run["niche"]).startswith("scan:"):
        return {"status": "not_found"}
    if (run.get("params") or {}).get("deep"):
        return deep_poll(conn, run, kind, brand_name=brand_name, redis_client=redis_client)
    niche = run["niche"]
    kind = kind if kind in KINDS else (run.get("params") or {}).get("insight") or "gifting_brands"
    note = ""
    if run["status"] in ("running", "ingesting"):
        age = _now() - radar._round_started(run)
        if run["status"] == "ingesting":
            if age < radar.RUN_TIMEOUT + timedelta(minutes=2):
                return searching(run_id, niche, kind, ingesting=True)
        else:
            try:
                step = radar._finish(conn, run)
            except Exception as err:
                conn.rollback()
                print(f"[brand-insight] poll failed run={run_id}: {type(err).__name__}: {err}")
                return searching(run_id, niche, kind)
            if not step.get("done"):
                return searching(run_id, niche, kind, items=step.get("items") or 0, ingesting=bool(step.get("ingesting")))
            if step.get("failed"):
                note = "source_down"
    elif run["status"] == "failed":
        note = "source_down"
    return _respond(kind, conn, niche, brand_name=brand_name, redis_client=redis_client, scan_id=run_id, note=note)


# ----- deep answers (insight agent) -----------------------------------------------------------------

def _fallback_searches(kind: str, niche: str) -> List[str]:
    bank = niche_queries(niche)["searches"]
    label = niche.replace("_", " ")
    generic = [f"{label} review", f"{label} haul", f"{label} routine", f"{label} must haves", f"best {label} products",
               f"{label} tips", f"{label} unboxing", f"honest {label} review"]
    return list(dict.fromkeys(generic + list(bank)))


def _plan_entries(queries: List[str]) -> List[Dict[str, Any]]:
    return [{"key": f"search:{q}", "type": "search", "query": q, "ttl": radar.QUERY_TTL} for q in queries]


def _recent_run(conn, niche: str, question: str) -> Optional[Dict[str, Any]]:
    """The last deep run for this exact question, so its plan (and answer) can be reused."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, status, params, created_at FROM discovery_runs
                   WHERE niche = %s AND params->>'question' = %s AND created_at > %s
                   ORDER BY id DESC LIMIT 1""", (niche, question, _now() - radar.QUERY_TTL))
    row = cur.fetchone()
    return dict(row) if row else None


def _creator_card(c: Dict[str, Any], videos: List[Dict[str, Any]], now: datetime, thumb=None) -> Dict[str, Any]:
    mine = sorted((v for v in videos if v.get("creator_handle") == c["handle"]), key=lambda v: -int(v.get("views") or 0))
    return {"handle": f"@{c['handle']}", "url": f"https://www.tiktok.com/@{c['handle']}",
            "followers": int(c.get("followers") or 0), "followers_label": scan._k(c.get("followers")),
            "region": c.get("region") or "", "bio": (c.get("bio") or "")[:220],
            "videos": len(mine), "best_views_label": scan._k(mine[0]["views"] if mine else 0),
            "examples": [scan.video_card(v, "", now, thumb) for v in mine[:3]]}


def deep_build(kind: str, conn, niche: str, question: str, data: Dict[str, Any], answer: Optional[Dict[str, Any]], *,
               queries: List[str], now: Optional[datetime] = None, thumb=None, matches=None,
               scan_id: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    now = now or _now()
    label = niche.replace("_", " ")
    videos = data.get("videos") or []
    creators = data.get("creators") or {}
    by_id = {str(v["tiktok_id"]): v for v in videos}
    out: Dict[str, Any] = {"status": "ready" if videos else "empty", "kind": kind, "scan_id": scan_id, "niche": niche,
                           "insight": True, "deep": True, "question": question,
                           "title": (answer or {}).get("headline") or TITLES[kind].format(niche=label),
                           "queries": queries,
                           "stats": {"videos": len(videos), "creators": len(creators), "searches": len(queries)}}
    brands = {str(b["name"]).strip().lower(): b for b in data.get("brands") or []}
    if answer:
        sections = []
        for s in answer["sections"]:
            items = []
            for it in s["items"]:
                item = {**it, "videos": [scan.video_card(by_id[x], "", now, thumb) for x in it["videos"] if x in by_id]}
                if it.get("handle") and it["handle"] in creators:
                    item["creator"] = _creator_card(creators[it["handle"]], videos, now, thumb)
                if s["kind"] == "brands" and it["title"].strip().lower() in brands:
                    b = brands[it["title"].strip().lower()]
                    item["brand"] = _brand_card(b)
                    if not item["videos"]:
                        item["videos"] = [scan.video_card(by_id[x], "", now, thumb) for x in b["video_ids"][:3] if x in by_id]
                items.append(item)
            sections.append({**s, "items": items})
        if kind == "creator_hooks" and brands and not any(s["kind"] == "brands" for s in sections):
            sections.append(_brands_section(list(brands.values()), by_id, now, thumb))
        out.update({"summary": answer["summary"], "sections": sections, "shortlist": answer.get("shortlist") or "",
                    "followups": answer.get("followups") or []})
    elif videos:
        groups = scan.group_hooks(videos)[:8]
        out["hooks"] = [{"hook": scan.short_hook(g["videos"][0].get("caption")), "videos": len(g["videos"]),
                         "creators": g["creators"], "views": g["views"], "views_label": scan._k(g["views"]),
                         "examples": [scan.video_card(v, "", now, thumb) for v in g["videos"][:3]]} for g in groups]
        out["summary"] = (f"We read **{len(videos)} recent {label} posts** from **{len(creators)} creators** across "
                          f"{len(queries)} searches. Here are the most viewed, plus openings several creators reuse.")
    else:
        out["summary"] = f"We couldn't find recent {label} posts for this yet. Try again in a minute."
    out["top_videos"] = [scan.video_card(v, "", now, thumb) for v in videos[:12]]
    if kind == "niche_creators":
        out["creators"] = scan.creator_rows(videos, "")[:30]
    if kind == "niche_creators" or (answer and any(s["kind"] == "creators" for s in answer["sections"])):
        out["matches"] = matches
    if note:
        out["note"] = note
    return out


def _brands_section(brands: List[Dict[str, Any]], by_id, now, thumb) -> Dict[str, Any]:
    items = []
    for b in brands[:6]:
        n = int(b.get("creators") or 0)
        how = " and ".join(t for t in (b.get("types") or []) if t in ("gifted", "paid")) or "featured"
        items.append({"title": b["name"], "text": f"{n} creator{'s' if n != 1 else ''} your size posted {how} content "
                      f"for them here.", "tag": f"{n} creator{'s' if n != 1 else ''}", "handle": "",
                      "videos": [scan.video_card(by_id[x], "", now, thumb) for x in b["video_ids"][:3] if x in by_id],
                      "brand": _brand_card(b)})
    return {"kind": "brands", "title": "Brands working with creators your size",
            "intro": "Brands seen gifting or paying creators in these videos.", "items": items}


def deep_respond(kind, conn, niche, question, keys, queries, *, brand_name, redis_client, scan_id=None, note="",
                 run=None, write=None, band=""):
    niche_rows = niche_videos(conn, niche)[:60] if kind != "ask" else []
    data = agent.pool(conn, keys, niche_rows)
    band = band or ((run or {}).get("params") or {}).get("band") or ""
    if kind == "creator_hooks":
        data["videos"] = same_size(data["videos"], band)
        handles = {v["creator_handle"] for v in data["videos"]}
        data["creators"] = {h: c for h, c in data["creators"].items() if h in handles}
        data["brands"] = pool_brands(conn, data["videos"])
    from services.cover_refresh import refresh

    refresh(conn, data["videos"], redis_client, shown=48)
    params = (run or {}).get("params") or {}
    answer = params.get("answer") if params.get("answer_at") else None
    if not answer:
        answer = agent.synthesize(question, niche, brand_name, data, write=write,
                                  band=band if kind == "creator_hooks" else "")
        if answer and scan_id:
            cur = conn.cursor()
            cur.execute("UPDATE discovery_runs SET params = params || %s::jsonb WHERE id = %s",
                        (json.dumps({"answer": answer, "answer_at": _now().isoformat()}), scan_id))
            conn.commit()
    wants_matches = kind != "creator_hooks" and (
        kind == "niche_creators" or (answer and any(s["kind"] == "creators" for s in answer["sections"])))
    matches = scan.creator_matches(niche, conn=conn, redis_client=redis_client) if wants_matches else None
    return deep_build(kind, conn, niche, question, data, answer, queries=queries, matches=matches, scan_id=scan_id,
                      note=note)


def deep_searching(run_id: int, niche: str, kind: str, queries: List[str], label: str = "", items: int = 0,
                   ingesting: bool = False, writing: bool = False) -> Dict[str, Any]:
    n = len(queries)
    if writing:
        step, line = 3, "Reading the videos and writing your answer…"
    elif ingesting:
        step, line = 2, "Checking creators and their recent posts…"
    elif items:
        step, line = 1, f"Searched TikTok with {n} queries. Found {items} posts so far…"
    else:
        step, line = 1, f"Searching TikTok with {n} queries" + (f" ({label})" if label else "") + "…"
    return {"status": "searching", "scan_id": run_id, "kind": kind, "niche": niche, "step": step, "line": line,
            "items": items, "insight": True, "deep": True, "queries": queries}


def deep_start(conn, kind: str, niche: str, *, brand_name: str = "", question: str = "", redis_client=None,
               can_start: bool = True, write=None, band: str = "") -> Dict[str, Any]:
    question = question_for(kind, niche, brand_name, question)
    if not question:
        return {"status": "invalid"}
    radar.finalize_stale(conn)
    recent = _recent_run(conn, niche, question)
    if recent and recent["status"] in ("running", "ingesting"):
        p = recent["params"] or {}
        return deep_searching(recent["id"], niche, kind, p.get("queries_text") or [], p.get("label") or "")
    if recent and (recent["params"] or {}).get("keys"):
        p = recent["params"]
        queries, label = p.get("queries_text") or [], p.get("label") or ""
    else:
        planned = agent.plan(question, niche, brand_name, write=write, fallback=_fallback_searches(kind, niche))
        queries, label = planned["queries"], planned["label"]
    plan = _plan_entries(queries)
    keys = [p["key"] for p in plan]
    stale = radar.stale_queries(conn, plan)
    fresh_answer = (recent and (recent["params"] or {}).get("answer_at")
                    and _now() - datetime.fromisoformat(recent["params"]["answer_at"]) < ANSWER_FRESH)
    est = source.estimate_cost(len(stale) * DEEP_PER_QUERY)
    if not stale or fresh_answer or not can_start or radar.spend_today(conn) + est > scan.DAILY_CAP_USD:
        note = "" if (not stale or fresh_answer) else "limit"
        if not recent:  # answered from stored videos: still record the run so its answer is kept and reused
            recent = {"id": radar._record(conn, {"niche": niche, "creator_id": None}, status="done",
                                          served_from="database",
                                          params={"kind": "brand_insight", "insight": kind, "deep": True,
                                                  "question": question, "keys": keys, "queries_text": queries,
                                                  "label": label, "brand": brand_name[:80], "band": band}),
                      "params": {}}
        return deep_respond(kind, conn, niche, question, keys, queries, brand_name=brand_name,
                            redis_client=redis_client, scan_id=recent["id"], note=note, run=recent, write=write,
                            band=band)
    try:
        apify_id = source.start_run([], [p["query"] for p in stale], DEEP_PER_QUERY)
    except Exception as err:
        print(f"[brand-insight] deep start failed: {type(err).__name__}: {err}")
        return deep_respond(kind, conn, niche, question, keys, queries, brand_name=brand_name,
                            redis_client=redis_client, note="source_down", write=write, band=band)
    run_id = radar._record(conn, {"niche": niche, "creator_id": None}, status="running", served_from="full_apify",
                           run_ids=[apify_id], queries=[{k: p[k] for k in ("key", "type", "query")} for p in stale],
                           params={"kind": "brand_insight", "insight": kind, "deep": True, "question": question,
                                   "keys": keys, "queries_text": queries, "label": label, "est_cost": est,
                                   "per_query": DEEP_PER_QUERY, "round": 1, "brand": brand_name[:80], "band": band})
    print(f"[brand-insight] deep run {run_id} niche={niche} kind={kind} searches={len(stale)}/{len(queries)} est=${est}")
    return {**deep_searching(run_id, niche, kind, queries, label), "started": True}


def deep_poll(conn, run: Dict[str, Any], kind: str, *, brand_name: str = "", redis_client=None,
              write=None) -> Dict[str, Any]:
    p = run.get("params") or {}
    niche, question = run["niche"], p.get("question") or ""
    queries, keys, label = p.get("queries_text") or [], p.get("keys") or [], p.get("label") or ""
    kind = p.get("insight") or kind
    note = ""
    if run["status"] in ("running", "ingesting"):
        age = _now() - radar._round_started(run)
        if run["status"] == "ingesting":
            if age < radar.RUN_TIMEOUT + timedelta(minutes=2):
                return deep_searching(run["id"], niche, kind, queries, label, ingesting=True)
        else:
            try:
                step = radar._finish(conn, run)
            except Exception as err:
                conn.rollback()
                print(f"[brand-insight] deep poll failed run={run['id']}: {type(err).__name__}: {err}")
                return deep_searching(run["id"], niche, kind, queries, label)
            if not step.get("done"):
                return deep_searching(run["id"], niche, kind, queries, label, items=step.get("items") or 0,
                                      ingesting=bool(step.get("ingesting")))
            if not step.get("failed"):
                # Ingest used this request; the answer gets its own (next poll).
                return deep_searching(run["id"], niche, kind, queries, label, writing=True)
            note = "source_down"
    elif run["status"] == "failed":
        note = "source_down"
    return deep_respond(kind, conn, niche, question, keys, queries, brand_name=brand_name, redis_client=redis_client,
                        scan_id=run["id"], note=note, run=run, write=write)
