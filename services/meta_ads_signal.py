"""Meta Ad Library signal per brand: how many ads are live, since when, and whether they look like creator (UGC) ads.

Why: 963 meta_ads_library brands are published, but we never kept what made them interesting: that they are paying
for ads right now, and what kind. A brand running several first-person, creator-style ads is a brand that buys UGC.
That is the signal the quality gate (services.brand_quality_gate) and the daily hot brand (services.hot_brands)
read.

Where the data comes from:
- New discoveries: services.meta_ads_library_scraper collects the Ad Library cards it scrolls past (library id,
  "Started running on", body text, video or image) and services.brand_db_writer saves them with the brand.
- Existing brands: scripts/backfill_meta_ad_signals.py searches the Ad Library by brand name, keeps the cards whose
  landing page is the brand's own domain, then opens the advertiser's page view for the full count. It needs
  Playwright + Chromium (and sometimes a logged-in browser via --cdp), so it runs on a machine, never on Vercel.
- Notes: brands inserted before this module only have the "Ad sample: ..." text in pr_brands.notes. ``seed_from_notes``
  keeps that sample (it can still say whether the ad is creator-style) but leaves counts and dates empty, so the gate
  cannot pass them until the backfill has seen their live ads.

UGC style: cheap heuristics first (first-person testimonial phrasing, creator handles, #ad / paid partnership,
talking-head video); the Gemini helper only for inconclusive samples, cached by a hash of the samples.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

from psycopg2.extras import RealDictCursor

MAX_SAMPLES = 3
SAMPLE_CHARS = 220

DDL = """
CREATE TABLE IF NOT EXISTS brand_ad_signals (
    brand_id INTEGER PRIMARY KEY,
    active_ad_count INTEGER,
    ads_first_seen DATE,
    ads_last_seen DATE,
    ad_library_url TEXT,
    page_id TEXT,
    ugc_style BOOLEAN,
    ugc_style_reason TEXT,
    ugc_style_source TEXT,
    ugc_samples_hash TEXT,
    ugc_checked_at TIMESTAMPTZ,
    ad_samples JSONB NOT NULL DEFAULT '[]'::jsonb,
    media_types JSONB NOT NULL DEFAULT '{}'::jsonb,
    signal_source TEXT,
    ad_checked_at TIMESTAMPTZ
)
"""
# Columns added after the first release go here (ensure_columns only alters when one is missing).
ADDED_COLUMNS_DDL = """ALTER TABLE brand_ad_signals
    ADD COLUMN IF NOT EXISTS signal_source TEXT,
    ADD COLUMN IF NOT EXISTS media_types JSONB NOT NULL DEFAULT '{}'::jsonb"""

_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_columns, ensure_index

    cur = conn.cursor()
    cur.execute("SELECT to_regclass('brand_ad_signals') IS NOT NULL")
    if not cur.fetchone()[0]:
        cur.execute(DDL)
    ensure_columns(cur, ADDED_COLUMNS_DDL)
    ensure_index(cur, "CREATE INDEX IF NOT EXISTS idx_brand_ad_signals_checked ON brand_ad_signals (ad_checked_at)")
    conn.commit()
    _READY = True


# ----- Ad Library URLs -----------------------------------------------------------------------------------------

def page_id_from_url(url: Optional[str]) -> Optional[str]:
    """Numeric Facebook page id from an advertiser link (facebook.com/123..., ?id=..., view_all_page_id=...)."""
    if not url:
        return None
    m = re.search(r"(?:view_all_page_id=|[?&]id=|facebook\.com/(?:profile\.php\?id=)?)(\d{6,})", url)
    return m.group(1) if m else None


def ad_library_url(page_id: Optional[str] = None, query: Optional[str] = None, country: str = "ALL") -> Optional[str]:
    """Link an admin (or creator) can open to see the brand's live ads."""
    base = "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=" + (country or "ALL")
    if page_id:
        return f"{base}&view_all_page_id={page_id}&search_type=page&media_type=all"
    if query:
        return f"{base}&q={quote_plus(query)}&search_type=keyword_unordered&media_type=all"
    return None


# ----- parsing Ad Library cards ------------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_STARTED_RE = re.compile(
    r"Started running on\s+(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})"
    r"|(?P<day2>\d{1,2})\s+(?P<mon2>[A-Za-z]{3})[a-z]*\.?,?\s+(?P<year2>\d{4}))", re.I)
_COPIES_RE = re.compile(r"(\d+)\s+ads?\s+use\s+this\s+creative", re.I)
_BOILERPLATE = re.compile(
    r"^(active|inactive|sponsored|library id:?.*|started running on.*|platforms|see ad details|see summary details|"
    r"this ad has multiple versions|open dropdown|\d+ ads? use this creative.*|ad details|shop now|learn more|"
    r"sign up|order now|buy now|get offer|see more|total active time.*|eu transparency|about the advertiser)$", re.I)


def parse_started(text: Optional[str]) -> Optional[date]:
    m = _STARTED_RE.search(text or "")
    if not m:
        return None
    mon = (m.group("mon") or m.group("mon2") or "").lower()[:3]
    day = m.group("day") or m.group("day2")
    year = m.group("year") or m.group("year2")
    try:
        return date(int(year), _MONTHS[mon], int(day))
    except (KeyError, ValueError, TypeError):
        return None


def ad_body(text: Optional[str], advertiser: Optional[str] = None) -> str:
    """The ad's own words from a card's innerText, without the Ad Library chrome around them."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _BOILERPLATE.match(line):
            continue
        if advertiser and line.lower() == advertiser.strip().lower():
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _registrable(host: str) -> str:
    from services.meta_ads_library_scraper import registrable_domain
    return registrable_domain(host)


def normalize_cards(raw_cards: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Raw cards from the scraper's card extractor -> clean, de-duplicated dicts (one per library id)."""
    from services.meta_ads_library_scraper import unwrap_landing_url

    out: List[Dict[str, Any]] = []
    seen = set()
    for card in raw_cards or []:
        if not isinstance(card, dict):
            continue
        text = str(card.get("text") or "")
        lib_id = str(card.get("library_id") or "").strip() or None
        key = lib_id or hashlib.sha1(text.encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        landing = unwrap_landing_url(card.get("landing_url") or "") if card.get("landing_url") else None
        host = (urlparse(landing).hostname or "").lower().removeprefix("www.") if landing else ""
        copies = _COPIES_RE.search(text)
        started = card.get("started")
        if isinstance(started, str):
            try:
                started = date.fromisoformat(started[:10])
            except ValueError:
                started = None
        out.append({
            "library_id": lib_id,
            "started": started or parse_started(card.get("started_text") or text),
            "copies": max(1, int(copies.group(1))) if copies else max(1, int(card.get("copies") or 1)),
            "landing_url": landing,
            "domain": _registrable(host) if host else None,
            "advertiser_page": card.get("advertiser_page"),
            "advertiser_name": card.get("advertiser_name"),
            "media": "video" if card.get("has_video") or card.get("media") == "video" else "image",
            "body": ad_body(card.get("body") or text, card.get("advertiser_name"))[:600],
        })
    return out


def cards_for_domain(cards: List[Dict[str, Any]], domain: str) -> List[Dict[str, Any]]:
    root = _registrable((domain or "").lower().removeprefix("www."))
    return [c for c in cards if c.get("domain") and c["domain"] == root]


# ----- UGC style ---------------------------------------------------------------------------------------------------

_UGC_STRONG = [
    (re.compile(r"\b(i|we) (tried|tested|switched|started using|swapped)\b", re.I),
     "first-person \"I tried\" testimonial"),
    (re.compile(r"\bi(?:'ve| have) been (using|wearing|taking|loving|obsessed)\b", re.I),
     "first-person \"I've been using\""),
    (re.compile(r"\b(i was (so )?(skeptical|sceptical|nervous|doubtful)|not gonna lie|ngl|honestly obsessed|"
                r"game ?changer for me|holy grail|my (skin|hair|routine|go-to|morning)|"
                r"you guys|storytime|pov:|grwm|get ready with me|unboxing|day in my life)\b", re.I),
     "creator-voice phrasing"),
    (re.compile(r"(#ad\b|#sponsored\b|#partner\b|#gifted\b|paid partnership|in partnership with|#ugc\b)", re.I),
     "creator partnership tag"),
    (re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{3,30}\b"), "creator @handle in the ad"),
    (re.compile(r"\b(my (honest )?review|before (and|&) after|results after \d+ (days|weeks))\b", re.I),
     "review / before-after format"),
]
_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|me|my|mine)\b", re.I)
_CATALOG = re.compile(r"(\d+% off|free shipping|shop (now|the sale)|limited time|use code|bundle and save|"
                      r"new arrivals|sale ends|buy \d+ get|order now|\$\d)", re.I)


def ugc_heuristic(samples: List[str], media: Optional[Dict[str, int]] = None) -> Tuple[Optional[bool], str]:
    """(True / False / None when unsure, reason). Video with first-person copy counts as a talking-head ad."""
    texts = [s for s in samples or [] if s and s.strip()]
    if not texts:
        return None, "no ad text"
    hits: List[str] = []
    first_person = 0
    catalog = 0
    for text in texts:
        for rx, label in _UGC_STRONG:
            if rx.search(text) and label not in hits:
                hits.append(label)
        if len(_FIRST_PERSON.findall(text)) >= 2:
            first_person += 1
        if _CATALOG.search(text):
            catalog += 1
    videos = int((media or {}).get("video") or 0)
    # A lone @handle can be the brand's own ("follow @glowco"): it only counts next to another sign.
    strong = [h for h in hits if h != "creator @handle in the ad"]
    if len(hits) >= 2 or (strong and (first_person or videos)):
        return True, "; ".join(hits[:3])
    if first_person and videos:
        return True, "first-person copy on video ads (talking-head)"
    if not hits and not first_person and catalog >= max(1, len(texts) // 2 + (len(texts) % 2)):
        return False, "catalog / promo copy only (discounts, shop-now), no creator voice"
    return None, "inconclusive"


UGC_SYSTEM = (
    "You label Meta ads. Given up to 3 ad texts from one brand, answer whether the brand runs creator-style (UGC) "
    "ads: first-person testimonials, a creator talking to camera, reviews, unboxings, 'I tried' stories, creator "
    "handles or partnership tags. Studio product shots with promo copy (discounts, 'shop now', features list) are "
    "NOT UGC. Return JSON {\"ugc\": true|false, \"reason\": \"<max 12 words>\"}."
)


def samples_hash(samples: List[str]) -> str:
    return hashlib.sha1(json.dumps([s.strip().lower() for s in samples or []]).encode()).hexdigest()[:16]


def classify_ugc(samples: List[str], media: Optional[Dict[str, int]] = None,
                 llm: Optional[Callable] = None, use_llm: bool = True) -> Tuple[Optional[bool], str, str]:
    """(ugc_style, reason, source) where source is heuristic | llm | none."""
    verdict, reason = ugc_heuristic(samples, media)
    if verdict is not None:
        return verdict, reason, "heuristic"
    if not use_llm or not [s for s in samples or [] if s and s.strip()]:
        return None, reason, "none"
    if llm is None:
        from services.pitch_writer import gemini_json as llm
    payload = {"ads": [s[:400] for s in samples[:MAX_SAMPLES]], "video_ads": int((media or {}).get("video") or 0)}
    got = llm(UGC_SYSTEM, json.dumps(payload), max_tokens=120) or {}
    if isinstance(got.get("ugc"), bool):
        return got["ugc"], ("AI: " + str(got.get("reason") or "").strip())[:200], "llm"
    return None, "inconclusive (AI unavailable)", "none"


# ----- building + saving signals --------------------------------------------------------------------------------

def _today() -> date:
    return datetime.now(timezone.utc).date()


def summarize(cards: List[Dict[str, Any]], *, checked_on: Optional[date] = None,
              results_count: Optional[int] = None, page_id: Optional[str] = None,
              query: Optional[str] = None, source: str = "page") -> Dict[str, Any]:
    """Signals for one brand from the live cards we saw. Cards in the Ad Library search are active ads, so
    ads_last_seen is the day we saw them; ads_first_seen is the oldest 'Started running on'."""
    checked_on = checked_on or _today()
    counted = sum(int(c.get("copies") or 1) for c in cards)
    if results_count and results_count > counted:
        counted = results_count
    starts = [c["started"] for c in cards if c.get("started")]
    media: Dict[str, int] = {}
    for c in cards:
        media[c.get("media") or "image"] = media.get(c.get("media") or "image", 0) + 1
    samples: List[str] = []
    for c in sorted(cards, key=lambda c: c.get("started") or date.min, reverse=True):
        body = (c.get("body") or "").strip()
        if body and body[:SAMPLE_CHARS] not in samples:
            samples.append(body[:SAMPLE_CHARS])
        if len(samples) >= MAX_SAMPLES:
            break
    page_id = page_id or next((page_id_from_url(c.get("advertiser_page")) for c in cards
                               if page_id_from_url(c.get("advertiser_page"))), None)
    return {
        "active_ad_count": counted,
        "ads_first_seen": min(starts) if starts else None,
        "ads_last_seen": checked_on if cards else None,
        "ad_library_url": ad_library_url(page_id=page_id, query=query),
        "page_id": page_id,
        "ad_samples": samples,
        "media_types": media,
        "signal_source": source,
    }


def load(conn, brand_id: int) -> Optional[Dict[str, Any]]:
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM brand_ad_signals WHERE brand_id = %s", (brand_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def save(conn, brand_id: int, signals: Dict[str, Any], *, use_llm: bool = False,
         llm: Optional[Callable] = None, commit: bool = True) -> Dict[str, Any]:
    """Upsert one brand's signals. UGC style is re-classified only when the samples changed (cache)."""
    ensure_table(conn)
    prev = load(conn, brand_id) or {}
    samples = list(signals.get("ad_samples") or prev.get("ad_samples") or [])[:MAX_SAMPLES]
    media = signals.get("media_types") or prev.get("media_types") or {}
    digest = samples_hash(samples)
    if prev.get("ugc_samples_hash") == digest and prev.get("ugc_style") is not None:
        ugc, reason, source = prev["ugc_style"], prev.get("ugc_style_reason"), prev.get("ugc_style_source")
    else:
        ugc, reason, source = classify_ugc(samples, media, llm=llm, use_llm=use_llm)
    row = {
        "active_ad_count": signals.get("active_ad_count"),
        "ads_first_seen": signals.get("ads_first_seen"),
        "ads_last_seen": signals.get("ads_last_seen"),
        "ad_library_url": signals.get("ad_library_url") or prev.get("ad_library_url"),
        "page_id": signals.get("page_id") or prev.get("page_id"),
        "ugc_style": ugc,
        "ugc_style_reason": reason,
        "ugc_style_source": source,
        "ugc_samples_hash": digest,
        "ad_samples": json.dumps(samples),
        "media_types": json.dumps(media),
        "signal_source": signals.get("signal_source") or prev.get("signal_source"),
    }
    notes_only = signals.get("signal_source") == "notes"
    # A notes-only seed never wipes counts a real Ad Library check found, and is not an Ad Library check.
    if notes_only and prev.get("signal_source") not in (None, "notes"):
        for key in ("active_ad_count", "ads_first_seen", "ads_last_seen", "signal_source"):
            row[key] = prev.get(key)
    row["ad_checked_at"] = None if notes_only else datetime.now(timezone.utc)
    cols = list(row)
    cur = conn.cursor()
    cur.execute(
        f"""INSERT INTO brand_ad_signals (brand_id, {', '.join(cols)}, ugc_checked_at)
            VALUES (%s, {', '.join('%s' for _ in cols)}, NOW())
            ON CONFLICT (brand_id) DO UPDATE SET
              {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != 'ad_checked_at')},
              ad_checked_at = COALESCE(EXCLUDED.ad_checked_at, brand_ad_signals.ad_checked_at),
              ugc_checked_at = CASE WHEN brand_ad_signals.ugc_samples_hash IS DISTINCT FROM EXCLUDED.ugc_samples_hash
                                      OR brand_ad_signals.ugc_style IS NULL
                                    THEN NOW() ELSE brand_ad_signals.ugc_checked_at END""",
        [brand_id] + [row[c] for c in cols],
    )
    if commit:
        conn.commit()
    return {**row, "ad_samples": samples, "media_types": media, "brand_id": brand_id}


def from_scrape_record(record: Dict[str, Any], checked_on: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Signals for a brand the crawler just found: the cards it saw for this brand's domain while searching.
    Keyword search shows a subset of the brand's ads, so the count is a floor until the backfill opens the page."""
    domain = (urlparse(record.get("website") or "").hostname or "").lower().removeprefix("www.")
    cards = cards_for_domain(normalize_cards(record.get("ad_cards") or []), domain) if domain else []
    sample = (record.get("ad_creative_sample") or "").strip()
    if not cards and not sample:
        return None
    if not cards:
        return {"ad_samples": [ad_body(sample)[:SAMPLE_CHARS]], "signal_source": "keyword_search",
                "active_ad_count": 1, "ads_last_seen": checked_on or _today(),
                "ad_library_url": ad_library_url(page_id=page_id_from_url(record.get("facebook_page")),
                                                 query=record.get("brand_name")),
                "page_id": page_id_from_url(record.get("facebook_page"))}
    return summarize(cards, checked_on=checked_on, page_id=page_id_from_url(record.get("facebook_page")),
                     query=record.get("brand_name"), source="keyword_search")


def save_from_scrape(conn, brand_id: int, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Hook for services.brand_db_writer: store the ad signal of a freshly inserted brand (heuristics only, the
    daily job sends inconclusive ones to the AI)."""
    signals = from_scrape_record(record)
    if not signals:
        return None
    return save(conn, brand_id, signals, use_llm=False, commit=False)


_NOTE_SAMPLE = re.compile(r"Ad sample:\s*(.+)$", re.I | re.S)


def sample_from_notes(notes: Optional[str]) -> Optional[str]:
    m = _NOTE_SAMPLE.search(notes or "")
    if not m:
        return None
    text = ad_body(m.group(1))
    return text[:SAMPLE_CHARS] or None


def seed_from_notes(conn, limit: int = 2000) -> int:
    """Meta brands with no signal row yet: keep the ad sample from their outreach note (no counts, no dates).
    Runs anywhere, no browser. Returns rows written."""
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT b.id, b.brand_name, b.notes FROM pr_brands b
           LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
           WHERE b.source = 'meta_ads_library' AND s.brand_id IS NULL AND b.notes ILIKE '%%Ad sample:%%'
           ORDER BY b.id LIMIT %s""", (int(limit),))
    n = 0
    for row in cur.fetchall():
        sample = sample_from_notes(row.get("notes"))
        if not sample:
            continue
        save(conn, int(row["id"]), {"ad_samples": [sample], "signal_source": "notes",
                                    "ad_library_url": ad_library_url(query=row.get("brand_name"))},
             use_llm=False, commit=False)
        n += 1
    conn.commit()
    return n


def classify_pending(conn, limit: int = 40, llm: Optional[Callable] = None) -> int:
    """Brands whose samples the heuristics couldn't call: ask the AI (cached by samples hash). Returns calls made."""
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT brand_id, ad_samples, media_types FROM brand_ad_signals
           WHERE ugc_style IS NULL AND jsonb_array_length(ad_samples) > 0
             AND (ugc_style_source IS DISTINCT FROM 'llm_failed' OR ugc_checked_at < NOW() - INTERVAL '7 days')
           ORDER BY active_ad_count DESC NULLS LAST, brand_id LIMIT %s""", (int(limit),))
    rows = cur.fetchall()
    for row in rows:
        samples = row["ad_samples"] if isinstance(row["ad_samples"], list) else json.loads(row["ad_samples"] or "[]")
        media = row["media_types"] if isinstance(row["media_types"], dict) else json.loads(row["media_types"] or "{}")
        ugc, reason, source = classify_ugc(samples, media, llm=llm, use_llm=True)
        cur.execute(
            """UPDATE brand_ad_signals SET ugc_style = %s, ugc_style_reason = %s, ugc_style_source = %s,
                      ugc_checked_at = NOW() WHERE brand_id = %s""",
            (ugc, reason, source if ugc is not None else "llm_failed", row["brand_id"]))
        conn.commit()
    return len(rows)


def stale_brands(conn, older_than_days: int = 7, limit: int = 200, brand_ids: Optional[List[int]] = None
                 ) -> List[Dict[str, Any]]:
    """Meta brands for the backfill: never checked against the Ad Library, or checked longest ago."""
    ensure_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    where = "b.id = ANY(%s)" if brand_ids else "b.source = 'meta_ads_library' AND b.status IN ('published', 'draft')"
    params: List[Any] = [list(brand_ids)] if brand_ids else []
    cur.execute(
        f"""SELECT b.id, b.brand_name, b.website, s.page_id, s.ad_checked_at, s.signal_source
            FROM pr_brands b LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
            WHERE {where}
              AND (s.brand_id IS NULL OR s.signal_source IS DISTINCT FROM 'page'
                   OR s.ad_checked_at < NOW() - make_interval(days => %s))
            ORDER BY (b.status = 'published') DESC, s.ad_checked_at NULLS FIRST, b.id
            LIMIT %s""", params + [int(older_than_days), int(limit)])
    return [dict(r) for r in cur.fetchall()]


def no_live_ads(brand_id: int, query: Optional[str], page_id: Optional[str] = None,
                checked_on: Optional[date] = None) -> Dict[str, Any]:
    """What to store when the Ad Library shows nothing live for the brand (count 0, keeps the old first-seen)."""
    return {"active_ad_count": 0, "ads_first_seen": None, "ads_last_seen": None,
            "ad_library_url": ad_library_url(page_id=page_id, query=query), "page_id": page_id,
            "ad_samples": [], "signal_source": "page" if page_id else "keyword_search"}
