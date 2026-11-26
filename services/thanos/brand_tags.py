"""Thanos brand tags: every niche a brand genuinely fits, decided once per brand by Gemini.

A brand has one directory category, and that is too coarse for matching: luggage and travel accessories sit under
fashion or lifestyle, so on Nov 26 2026 the directory had 5 "travel" brands for travel creators while 21+ brands
with an inbox sell travel gear. Each brand gets `pr_brands.match_niches` (TEXT[]): the creator niches whose
creators would naturally feature its products. Thanos matches a creator's niches against the category OR these
tags (services.thanos.engine.candidates_sql).

Rules the tags follow:
- Strict: a niche only when the product is a natural, central fit for that niche's content. A skincare brand is
  skincare + beauty, never travel because it sells minis. Lifestyle is not a catch-all.
- The brand's own category is always kept (tags only ever add precise niches, they never drop the category).
- Only canonical niches (brand_categories.CANONICAL_CATEGORIES), at most 3.

Backfill and new brands: run_batch() from the cron (/api/polly/cron/brand-tags), BATCH brands per run, oldest
untagged first. Never raises; a brand Gemini can't tag keeps its category alone and is retried after a day.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from brand_categories import CANONICAL_CATEGORIES, normalize_category

NICHES = [c for c in CANONICAL_CATEGORIES if c != "other"]
PER_CALL = 20
BATCH = 80
MAX_TAGS = 3

SCHEMA_SQL = """
    ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS match_niches TEXT[];
    ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS match_niches_at TIMESTAMPTZ;
    CREATE INDEX IF NOT EXISTS pr_brands_match_niches_idx ON pr_brands USING GIN (match_niches);
"""

SYSTEM = f"""You tag brands for a platform that matches UGC creators with brands to pitch.
For each brand, list the creator niches whose creators would NATURALLY feature this brand's products as a central
part of their content. Allowed niches (use these exact words): {", ".join(NICHES)}.

Rules:
- Be strict. Only niches where the product clearly belongs in that niche's content. 1 to 3 niches.
- Luggage, suitcases, travel pillows, packing cubes, travel insurance/apps: travel (plus fashion only for
  fashion-led bags). Skincare: skincare, beauty. Makeup: beauty. Supplements: supplements, wellness.
- Never add a niche just because the product could be used there (sunscreen is not travel, a phone case is not
  travel, coffee is not fitness). Lifestyle only for genuinely general lifestyle brands (candles, stationery,
  home-and-living gifts), never as a catch-all.
- If unsure, return only the niche of the brand's main product.

Return JSON: {{"brands": [{{"id": <id>, "niches": ["..."]}}]}} with every brand id you were given."""


def ensure_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()


def clean_tags(raw: Any, category: Optional[str]) -> List[str]:
    """Canonical niches only, the brand's own category first, at most MAX_TAGS."""
    out: List[str] = []
    own = normalize_category(category) if category else None
    if own in NICHES:
        out.append(own)
    for item in raw if isinstance(raw, list) else []:
        tag = normalize_category(str(item or "").strip().lower())
        if tag in NICHES and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS] if own not in NICHES else out[:MAX_TAGS + 1]


def _brief(row: Dict[str, Any]) -> Dict[str, Any]:
    text = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]  # noqa: E731
    return {
        "id": int(row["id"]),
        "name": text(row.get("brand_name"), 80),
        "category": text(row.get("category"), 30),
        "product": text(row.get("hero_product"), 80),
        "about": text(row.get("description"), 280),
        "website": text(row.get("website"), 80),
    }


def tag_rows(rows: List[Dict[str, Any]], ask: Optional[Callable] = None) -> Dict[int, List[str]]:
    """{brand id: niches} for the rows Gemini answered. Rows it skipped are left out (retried later)."""
    if not rows:
        return {}
    if ask is None:
        from services.brand_pipeline import _gemini_json

        ask = lambda system, user: _gemini_json(system, user, max_tokens=1600, thinking_budget=0)  # noqa: E731
    by_id = {int(r["id"]): r for r in rows}
    got = ask(SYSTEM, json.dumps({"brands": [_brief(r) for r in rows]})) or {}
    out: Dict[int, List[str]] = {}
    for item in got.get("brands") or []:
        try:
            bid = int(item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if bid in by_id:
            out[bid] = clean_tags(item.get("niches"), by_id[bid].get("category"))
    return out


def run_batch(conn, limit: int = BATCH, ask: Optional[Callable] = None) -> Dict[str, Any]:
    """Tag the next `limit` untagged published brands (or ones whose last try failed a day ago)."""
    from psycopg2.extras import RealDictCursor

    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT id, brand_name, category, hero_product, description, website
           FROM pr_brands
           WHERE COALESCE(status, 'published') = 'published'
             AND match_niches IS NULL
             AND (match_niches_at IS NULL OR match_niches_at < NOW() - INTERVAL '1 day')
           ORDER BY id
           LIMIT %s""",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    tagged = failed = 0
    for i in range(0, len(rows), PER_CALL):
        chunk = rows[i:i + PER_CALL]
        tags = tag_rows(chunk, ask=ask)
        for row in chunk:
            bid = int(row["id"])
            if bid in tags and tags[bid]:
                cur.execute("UPDATE pr_brands SET match_niches = %s, match_niches_at = NOW() WHERE id = %s",
                            (tags[bid], bid))
                tagged += 1
            else:
                # No answer: try again tomorrow; until then the category alone matches.
                cur.execute("UPDATE pr_brands SET match_niches_at = NOW() WHERE id = %s", (bid,))
                failed += 1
        conn.commit()
    cur.execute("SELECT COUNT(*) AS n FROM pr_brands WHERE COALESCE(status, 'published') = 'published' "
                "AND match_niches IS NULL")
    left = int((cur.fetchone() or {}).get("n") or 0)
    if tagged:
        try:
            from services.thanos import signals_changed
            signals_changed()
        except Exception:  # noqa: BLE001
            pass
    return {"tagged": tagged, "failed": failed, "left": left}
