"""UGC Pulse: what's working in UGC ads this week, per niche. Shown on the Pulse page and in the daily brief.

Every item points at its sources, nothing is invented:
- hooks (daily, ours): opening lines of real ads in the niche, from the Meta Ad Library samples our scanner stores
  (brand_ad_signals.ad_samples). An LLM picks the strongest openers, tags the pattern and says why it works; it
  may only quote or lightly clean an ad's own first line, and each hook keeps the ads it came from.
- trend + tip (daily, ours): the format pattern that recurs most in those ads this week, and one editing tip for it.
- news / trends / tips from the Hermes social agent: posted to POST /api/pulse/ingest (cron secret), shown as is.

Free creators see the hook of the day, the next 2 hooks, the trend, tip and news; the rest of the week's hooks,
the archive and "pitch this brand with this angle" are Pro.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from psycopg2.extras import Json, RealDictCursor

KINDS = ("hook", "trend", "tip", "news")
PATTERNS = ("POV", "Problem → fix", "Confession", "Myth", "Results", "Question", "List", "Before / after",
            "Story", "Other")
FREE_HOOKS = 3            # hook of the day + 2
KEEP_DAYS = 90
MIN_SAMPLES = 6           # fewer real ads than this in a niche: no pulse that day
MAX_SAMPLES = 60
LOOKBACK_DAYS = 60

DDL = """
CREATE TABLE IF NOT EXISTS ugc_pulse_items (
    id SERIAL PRIMARY KEY,
    day DATE NOT NULL,
    niche TEXT NOT NULL,
    kind TEXT NOT NULL,
    pattern TEXT,
    text TEXT NOT NULL,
    why TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_type TEXT NOT NULL DEFAULT 'meta_ads',
    text_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (day, niche, kind, text_hash)
);
CREATE INDEX IF NOT EXISTS ugc_pulse_items_niche_day ON ugc_pulse_items (niche, day DESC);
CREATE TABLE IF NOT EXISTS creator_pulse_saves (
    creator_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL REFERENCES ugc_pulse_items(id) ON DELETE CASCADE,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (creator_id, item_id)
);
"""

_READY = False


def ensure_tables(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()
    _READY = True


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join(str(text or "").lower().split()).encode()).hexdigest()[:20]


def _cur(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


# ---------------------------------------------------------------------------------------------------------------
# Building the day
# ---------------------------------------------------------------------------------------------------------------

_DOMAIN_PREFIX = re.compile(r"^\s*[A-Z0-9][A-Z0-9.-]*\.(?:COM|CO|NET|ORG|SHOP|STORE|BEAUTY|IO)(?:\.[A-Z]{2})?\b\s*", re.I)
_SPONSORED = re.compile(r"^.{0,60}?\bSponsored\b\s*", re.I)
_CTA_TAIL = re.compile(r"\s*\b(Shop now|Learn more|Buy now|Sign up|Order now|Get offer)\b\s*$", re.I)


def clean_ad(text: str) -> str:
    """The ad's own words: no 'BRAND.COM' card prefix, no '<Page> Sponsored', no trailing CTA button, no bare URLs."""
    t = " ".join(str(text or "").split())
    if re.fullmatch(r"https?://\S+", t):
        return ""
    t = _SPONSORED.sub("", t, count=1)
    t = _DOMAIN_PREFIX.sub("", t, count=1)
    t = _CTA_TAIL.sub("", t)
    return t.strip()


def first_line(text: str, limit: int = 160) -> str:
    """An ad's opener: its first sentence or line, trimmed."""
    raw = " ".join(str(text or "").split())
    for stop in (". ", "! ", "? ", " | ", "\n"):
        i = raw.find(stop)
        if 12 <= i <= limit:
            return raw[: i + 1].strip()
    return raw[:limit].strip()


def niche_samples(conn, niche: str, since: date, limit: int = MAX_SAMPLES) -> List[Dict[str, Any]]:
    """Real ad texts from brands in this niche that we saw advertising recently, UGC-style first."""
    cur = _cur(conn)
    cur.execute("""
        SELECT b.id AS brand_id, b.brand_name, b.logo_url, (b.source = 'meta_ads_library') AS hot,
               s.ad_samples, s.ad_library_url, s.ugc_style
        FROM brand_ad_signals s JOIN pr_brands b ON b.id = s.brand_id
        WHERE LOWER(COALESCE(b.category, '')) = %s
          AND COALESCE(b.status, 'published') = 'published'
          AND (s.ads_last_seen >= %s OR b.created_at >= %s)
        ORDER BY (s.ugc_style IS TRUE) DESC, s.ads_last_seen DESC NULLS LAST, b.created_at DESC
        LIMIT 200""", (niche, since, since))
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        samples = row.get("ad_samples")
        if isinstance(samples, str):
            try:
                samples = json.loads(samples)
            except ValueError:
                samples = []
        for text in samples or []:
            text = text.get("text") if isinstance(text, dict) else text
            text = clean_ad(text) if isinstance(text, str) else ""
            if len(text) < 25:
                continue
            out.append({"i": len(out), "brand_id": int(row["brand_id"]), "brand_name": row["brand_name"],
                        "logo": row.get("logo_url"), "hot": bool(row.get("hot")), "ad_url": row.get("ad_library_url"),
                        "ugc": bool(row.get("ugc_style")), "text": text.strip()[:500], "opener": first_line(text)})
            if len(out) >= limit:
                return out
    return out


PULSE_SYSTEM = (
    "You are a UGC (user-generated content) ad strategist. You get real ad texts from brands in one niche, each "
    "with an index. Return JSON {\"hooks\": [...], \"trend\": {...}, \"tip\": \"...\"}.\n"
    "hooks: the 5 to 8 strongest scroll-stopping OPENING LINES. Each item: {\"hook\": the ad's own opening line, "
    "quoted or lightly cleaned (fix typos, cut emojis/hashtags, max 18 words; never invent claims, numbers or "
    "product names that aren't in the ad), \"pattern\": one of " + ", ".join(PATTERNS) + ", \"why\": one sentence "
    "(max 30 words) on why it stops the scroll and how a creator should film it, \"sources\": [indexes of ads that "
    "open this way, best first]}. Rank by how many ads use the same opening idea, then by strength. Skip openers "
    "that are just discounts, shipping offers or the brand name.\n"
    "trend: {\"title\": max 9 words, \"body\": max 35 words} the format or angle that recurs most across these ads "
    "this week, said as advice to a creator. tip: one practical editing tip for that format, max 25 words.\n"
    "Plain language, no hype, no hashtags."
)


def _llm_default(system: str, user: str, max_tokens: int = 1200) -> Dict[str, Any]:
    from services.pitch_writer import gemini_json
    return gemini_json(system, user, max_tokens=max_tokens)


def generate(samples: List[Dict[str, Any]], llm: Optional[Callable] = None) -> Dict[str, Any]:
    llm = llm or _llm_default
    payload = [{"i": s["i"], "brand": s["brand_name"], "ad": s["text"][:300]} for s in samples]
    got = llm(PULSE_SYSTEM, json.dumps({"ads": payload}), max_tokens=1600) or {}
    return got if isinstance(got, dict) else {}


def _sources(idx: Iterable[Any], samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_i = {s["i"]: s for s in samples}
    seen, out = set(), []
    for i in idx or []:
        try:
            s = by_i.get(int(i))
        except (TypeError, ValueError):
            s = None
        if not s or s["brand_id"] in seen:
            continue
        seen.add(s["brand_id"])
        out.append({"brand_id": s["brand_id"], "brand_name": s["brand_name"], "logo": s.get("logo"),
                    "hot": s["hot"], "ad_url": s.get("ad_url")})
    return out


def _grounded(hook: str, srcs_idx: Iterable[Any], samples: List[Dict[str, Any]]) -> bool:
    """A hook must share real words with at least one of its source ads: no invented openers."""
    words = {w for w in "".join(c if c.isalnum() else " " for c in hook.lower()).split() if len(w) > 3}
    if not words:
        return False
    by_i = {s["i"]: s for s in samples}
    for i in srcs_idx or []:
        try:
            s = by_i.get(int(i))
        except (TypeError, ValueError):
            continue
        if not s:
            continue
        ad = s["text"].lower()
        if sum(1 for w in words if w in ad) >= max(2, len(words) // 2):
            return True
    return False


def build_items(niche: str, day: date, samples: List[Dict[str, Any]], result: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for h in result.get("hooks") or []:
        if not isinstance(h, dict):
            continue
        text = " ".join(str(h.get("hook") or "").split()).strip(' "“”')
        idx = h.get("sources") or []
        if len(text) < 12 or not _grounded(text, idx, samples):
            continue
        srcs = _sources(idx, samples)
        if not srcs:
            continue
        pattern = h.get("pattern") if h.get("pattern") in PATTERNS else "Other"
        items.append({"day": day, "niche": niche, "kind": "hook", "pattern": pattern, "text": f"“{text}”",
                      "why": str(h.get("why") or "").strip()[:300] or None, "score": len(idx), "sources": srcs})
    trend = result.get("trend") if isinstance(result.get("trend"), dict) else {}
    if trend.get("title") and trend.get("body"):
        top = _sources([s["i"] for s in samples if s.get("ugc")][:6] or [s["i"] for s in samples][:6], samples)
        items.append({"day": day, "niche": niche, "kind": "trend", "pattern": None,
                      "text": str(trend["title"]).strip()[:120], "why": str(trend["body"]).strip()[:300],
                      "score": 0, "sources": top[:4]})
    if isinstance(result.get("tip"), str) and len(result["tip"].strip()) > 10:
        items.append({"day": day, "niche": niche, "kind": "tip", "pattern": None, "text": result["tip"].strip()[:240],
                      "why": None, "score": 0, "sources": []})
    return items


def save_items(conn, items: List[Dict[str, Any]], source_type: str = "meta_ads") -> int:
    cur = conn.cursor()
    n = 0
    for it in items:
        cur.execute("""
            INSERT INTO ugc_pulse_items (day, niche, kind, pattern, text, why, score, sources, source_type, text_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (day, niche, kind, text_hash) DO NOTHING""",
            (it["day"], it["niche"], it["kind"], it.get("pattern"), it["text"], it.get("why"), int(it.get("score") or 0),
             Json(it.get("sources") or []), source_type, _hash(it["text"])))
        n += cur.rowcount or 0
    conn.commit()
    return n


def active_niches(conn) -> List[str]:
    """Niches at least one approved creator has picked."""
    from services.creator_niches import NICHE_IDS, clean_niches
    cur = _cur(conn)
    cur.execute("""SELECT c.niche, c.creator_niches FROM creators c
                   WHERE COALESCE(c.approval_status, 'approved') = 'approved'""")
    seen: Dict[str, int] = {}
    for row in cur.fetchall():
        vals = row.get("creator_niches") or row.get("niche") or []
        for n in clean_niches(vals if not isinstance(vals, str) else [vals]):
            seen[n] = seen.get(n, 0) + 1
    return [n for n in sorted(seen, key=lambda k: -seen[k]) if n in NICHE_IDS]


def build_day(conn, day: Optional[date] = None, niches: Optional[List[str]] = None,
              llm: Optional[Callable] = None, budget: float = 200.0) -> Dict[str, Any]:
    """Daily cron: one pulse per niche from that niche's real ads. Idempotent: a niche already built today is
    skipped. Old items (> KEEP_DAYS) are pruned, saved ones are kept."""
    ensure_tables(conn)
    day = day or _today()
    since = day - timedelta(days=LOOKBACK_DAYS)
    out: Dict[str, Any] = {"day": day.isoformat(), "built": {}, "skipped": {}}
    cur = conn.cursor()
    import time
    start = time.monotonic()
    for niche in niches or active_niches(conn):
        if time.monotonic() - start > budget:
            out["skipped"][niche] = "time_budget"  # the second cron run picks it up
            continue
        cur.execute("SELECT 1 FROM ugc_pulse_items WHERE day = %s AND niche = %s AND source_type = 'meta_ads' LIMIT 1",
                    (day, niche))
        if cur.fetchone():
            out["skipped"][niche] = "done"
            continue
        samples = niche_samples(conn, niche, since)
        if len(samples) < MIN_SAMPLES:
            out["skipped"][niche] = f"{len(samples)} ads"
            continue
        try:
            items = build_items(niche, day, samples, generate(samples, llm))
        except Exception as err:  # one niche failing never stops the others
            print(f"[pulse] {niche} failed: {err}")
            conn.rollback()
            out["skipped"][niche] = "llm_failed"
            continue
        out["built"][niche] = save_items(conn, items)
    cur.execute("""DELETE FROM ugc_pulse_items i WHERE day < %s
                   AND NOT EXISTS (SELECT 1 FROM creator_pulse_saves s WHERE s.item_id = i.id)""",
                (day - timedelta(days=KEEP_DAYS),))
    conn.commit()
    return out


# ---------------------------------------------------------------------------------------------------------------
# Hermes ingest
# ---------------------------------------------------------------------------------------------------------------

def ingest(conn, items: List[Dict[str, Any]], day: Optional[date] = None) -> Dict[str, Any]:
    """Items from the Hermes social agent: {niche, kind: news|trend|tip|hook, text, why?, sources?: [{url, label}]}.
    niche 'all' shows in every niche."""
    from services.creator_niches import NICHE_IDS
    ensure_tables(conn)
    day = day or _today()
    clean, rejected = [], 0
    for it in items or []:
        if not isinstance(it, dict):
            rejected += 1
            continue
        niche = str(it.get("niche") or "").strip().lower()
        kind = str(it.get("kind") or "").strip().lower()
        text = " ".join(str(it.get("text") or "").split())
        if (niche not in NICHE_IDS and niche != "all") or kind not in KINDS or not (8 <= len(text) <= 400):
            rejected += 1
            continue
        srcs = [{"url": str(s.get("url"))[:500], "label": str(s.get("label") or "")[:80]}
                for s in (it.get("sources") or []) if isinstance(s, dict) and str(s.get("url") or "").startswith("http")]
        clean.append({"day": day, "niche": niche, "kind": kind, "pattern": None, "text": text,
                      "why": " ".join(str(it.get("why") or "").split())[:300] or None,
                      "score": 0, "sources": srcs[:5]})
    saved = save_items(conn, clean, source_type="hermes")
    return {"saved": saved, "rejected": rejected}


# ---------------------------------------------------------------------------------------------------------------
# Reading (Pulse page, brief)
# ---------------------------------------------------------------------------------------------------------------

def _item(row: Dict[str, Any], saved: set) -> Dict[str, Any]:
    return {"id": int(row["id"]), "day": row["day"].isoformat() if hasattr(row["day"], "isoformat") else row["day"],
            "niche": row["niche"], "kind": row["kind"], "pattern": row.get("pattern"), "text": row["text"],
            "why": row.get("why"), "score": int(row.get("score") or 0), "sources": row.get("sources") or [],
            "source_type": row.get("source_type"), "saved": int(row["id"]) in saved}


def saved_ids(conn, creator_id: int) -> set:
    cur = conn.cursor()
    cur.execute("SELECT item_id FROM creator_pulse_saves WHERE creator_id = %s", (creator_id,))
    return {int(r[0]) for r in cur.fetchall()}


def latest_day(conn, niche: str, on_or_before: Optional[date] = None) -> Optional[date]:
    cur = conn.cursor()
    cur.execute("""SELECT MAX(day) FROM ugc_pulse_items WHERE niche IN (%s, 'all') AND day <= %s""",
                (niche, on_or_before or _today()))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def day_view(conn, niche: str, creator_id: Optional[int] = None, is_pro: bool = False,
             day: Optional[date] = None) -> Dict[str, Any]:
    """Today's pulse for a niche (or the latest day that has one)."""
    ensure_tables(conn)
    d = latest_day(conn, niche, day)
    out: Dict[str, Any] = {"niche": niche, "day": d.isoformat() if d else None, "hero": None, "hooks": [],
                           "trend": None, "tip": None, "news": [], "locked_count": 0}
    if not d:
        return out
    saved = saved_ids(conn, creator_id) if creator_id else set()
    cur = _cur(conn)
    cur.execute("""SELECT * FROM ugc_pulse_items WHERE day = %s AND niche IN (%s, 'all')
                   ORDER BY (kind = 'hook') DESC, score DESC, id""", (d, niche))
    rows = [_item(r, saved) for r in cur.fetchall()]
    hooks = [r for r in rows if r["kind"] == "hook"]
    out["trend"] = next((r for r in rows if r["kind"] == "trend" and r["niche"] == niche), None) \
        or next((r for r in rows if r["kind"] == "trend"), None)
    out["tip"] = next((r for r in rows if r["kind"] == "tip" and r["niche"] == niche), None) \
        or next((r for r in rows if r["kind"] == "tip"), None)
    out["news"] = [r for r in rows if r["kind"] == "news"][:3]
    if hooks:
        out["hero"] = hooks[0]
        rest = hooks[1:]
        visible = rest if is_pro else rest[: FREE_HOOKS - 1]
        out["hooks"] = visible
        out["locked_count"] = 0 if is_pro else max(0, len(rest) - len(visible))
    out["week_count"] = week_hook_count(conn, niche, d)
    return out


def week_hook_count(conn, niche: str, until: date) -> int:
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(DISTINCT text_hash) FROM ugc_pulse_items
                   WHERE kind = 'hook' AND niche = %s AND day > %s AND day <= %s""",
                (niche, until - timedelta(days=7), until))
    return int(cur.fetchone()[0] or 0)


def week_view(conn, niche: str, creator_id: Optional[int] = None, is_pro: bool = False,
              days: int = 7, offset_days: int = 0) -> Dict[str, Any]:
    """Every hook, trend and tip of the last `days` days (newest first), one row per distinct text. Pro only;
    free creators get the count."""
    ensure_tables(conn)
    until = _today() - timedelta(days=offset_days)
    since = until - timedelta(days=days)
    cur = _cur(conn)
    cur.execute("""SELECT DISTINCT ON (text_hash) * FROM ugc_pulse_items
                   WHERE niche IN (%s, 'all') AND day > %s AND day <= %s
                   ORDER BY text_hash, day DESC""", (niche, since, until))
    saved = saved_ids(conn, creator_id) if creator_id else set()
    rows = sorted((_item(r, saved) for r in cur.fetchall()), key=lambda r: (r["day"], r["score"]), reverse=True)
    if not is_pro:
        return {"niche": niche, "items": [], "locked_count": len(rows), "since": since.isoformat()}
    return {"niche": niche, "items": rows, "locked_count": 0, "since": since.isoformat(),
            "more": offset_days + days < KEEP_DAYS}


def saved_view(conn, creator_id: int) -> Dict[str, Any]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("""SELECT i.*, s.saved_at FROM creator_pulse_saves s JOIN ugc_pulse_items i ON i.id = s.item_id
                   WHERE s.creator_id = %s ORDER BY s.saved_at DESC LIMIT 300""", (creator_id,))
    rows = cur.fetchall()
    ids = {int(r["id"]) for r in rows}
    return {"items": [_item(r, ids) for r in rows]}


def set_saved(conn, creator_id: int, item_id: int, saved: bool) -> bool:
    ensure_tables(conn)
    cur = conn.cursor()
    if saved:
        cur.execute("""INSERT INTO creator_pulse_saves (creator_id, item_id)
                       SELECT %s, id FROM ugc_pulse_items WHERE id = %s
                       ON CONFLICT DO NOTHING""", (creator_id, item_id))
    else:
        cur.execute("DELETE FROM creator_pulse_saves WHERE creator_id = %s AND item_id = %s", (creator_id, item_id))
    conn.commit()
    return True


def brief_pulse(conn, niches: List[str]) -> Optional[Dict[str, Any]]:
    """For the daily brief: today's hook of the day, trend and tip in the creator's first niche that has one."""
    try:
        ensure_tables(conn)
        for niche in niches or []:
            v = day_view(conn, niche)
            if v.get("hero") and v.get("day") == _today().isoformat():
                return {"niche": niche, "hook": v["hero"], "trend": v.get("trend"), "tip": v.get("tip"),
                        "more": max(0, (v.get("week_count") or 0) - 1)}
    except Exception as err:
        print(f"[pulse] brief section skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
    return None
