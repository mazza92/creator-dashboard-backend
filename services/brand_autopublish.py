"""Auto-publish Meta-ads brands the scanner (Hermes, scripts/crawl_meta_ads.py) adds as drafts.

Runs first in the daily hotlist cron. For each draft, newest first, within a time budget:
1. inbox:   verify the contact email with Hunter, or find the brand's best inbox when there's none
            (contact_repair's Hunter daily cap)
2. logo:    find a logo when it's missing (on-page logo, then logo.dev / unavatar)
3. profile: AI profile from the brand's site when the category isn't checked or the description is thin
4. gate:    run the quality gate (active ads, category, verified inbox, safety, real store)
Published only when the gate passes AND it has a real logo and a real description. Everything else stays a draft
and is retried on later runs (at most MAX_TRIES times; an admin reject is never published).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

MIN_DESCRIPTION = 40
MAX_TRIES = 6  # the daily cron fires twice: about 3 days of retries
LOW_RES_LOGO = ("google.com/s2/favicons",)
UNVERIFIED = ("", "unverified", "unknown")
SENDABLE = ("valid", "catch-all")

_COLS_DDL = "ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS autopublish_tries INTEGER DEFAULT 0"


def has_logo(url: Optional[str]) -> bool:
    url = (url or "").strip()
    return bool(url) and not any(bad in url for bad in LOW_RES_LOGO)


def has_description(text: Optional[str]) -> bool:
    return len((text or "").strip()) >= MIN_DESCRIPTION


def publish_ready(gate: Dict[str, Any], brand: Dict[str, Any]) -> List[str]:
    """Why it can't be published yet ([] = publish)."""
    missing = []
    if gate.get("override") == "rejected":
        return ["admin rejected"]
    if not gate.get("pass"):
        missing += gate.get("reasons") or ["quality gate"]
    if not has_logo(brand.get("logo_url")):
        missing.append("logo: missing")
    if not has_description(brand.get("description")):
        missing.append("description: missing or too short")
    return missing


def _drafts(conn, limit: int) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"""SELECT id, brand_name, website, contact_email, email_status, logo_url, description, category,
                           hero_product, to_jsonb(b) ->> 'category_checked_at' AS category_checked_at
                    FROM pr_brands b
                    WHERE source = 'meta_ads_library' AND status = 'draft'
                      AND NULLIF(TRIM(website), '') IS NOT NULL
                      AND COALESCE(autopublish_tries, 0) < {MAX_TRIES}
                    ORDER BY created_at DESC LIMIT %s""", (limit,))
    return [dict(r) for r in cur.fetchall()]


def _verify_inbox(conn, brand: Dict[str, Any]) -> Optional[str]:
    """Verify the scanner's email; when there's none (or it isn't sendable), ask Hunter for the brand's best inbox.
    Both stay inside contact_repair's Hunter daily cap. Returns the new status when something changed."""
    from services.contact_repair import _save_status, _swap, find_replacement, hunter_verify
    email = (brand.get("contact_email") or "").strip()
    status = str(brand.get("email_status") or "").strip().lower()
    changed = None
    if email and status in UNVERIFIED:
        fresh = hunter_verify(conn, email)
        if fresh is None:
            return None  # no Hunter budget left today: try again next run
        _save_status(conn, int(brand["id"]), fresh)
        brand["email_status"] = status = changed = fresh
    if email and status in SENDABLE:
        return changed
    found = find_replacement(conn, brand, [email] if email else [])
    if not found:
        return changed
    _swap(conn, int(brand["id"]), email or None, found["email"], found["status"], "autopublish", None)
    brand["contact_email"], brand["email_status"] = found["email"], found["status"]
    return found["status"]


def _find_logo(conn, brand: Dict[str, Any]) -> Optional[str]:
    if has_logo(brand.get("logo_url")):
        return None
    from services.brand_image_scraper import scrape_brand_images
    logo = (scrape_brand_images(brand["website"], logo_only=False) or {}).get("logo_url")
    if has_logo(logo):
        conn.cursor().execute("UPDATE pr_brands SET logo_url = %s, updated_at = NOW() WHERE id = %s",
                              (logo, int(brand["id"])))
        conn.commit()
        brand["logo_url"] = logo
        return logo
    return None


def _profile(conn, brand: Dict[str, Any]) -> bool:
    if brand.get("category_checked_at") and has_description(brand.get("description")):
        return False
    from services import brand_profile_ai as bp
    cur = conn.cursor()
    bp.ensure_profile_columns(cur)
    res = bp.enrich(conn, brand)
    updates = res.get("updates") or {}
    if not res.get("profile"):
        return False
    bp.write_updates(cur, int(brand["id"]), updates)
    conn.commit()
    brand.update(updates)
    return True


def _publish(conn, brand_id: int) -> None:
    conn.cursor().execute("""UPDATE pr_brands SET status = 'published', updated_at = NOW()
                             WHERE id = %s AND status = 'draft'""", (brand_id,))
    conn.commit()


def _tried(conn, brand_id: int) -> None:
    conn.cursor().execute("UPDATE pr_brands SET autopublish_tries = COALESCE(autopublish_tries, 0) + 1 WHERE id = %s",
                          (brand_id,))
    conn.commit()


def run(conn, *, limit: int = 60, profiles: int = 25, budget: float = 120.0,
        clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    from services.brand_quality_gate import evaluate
    from services.db_ddl import ensure_columns

    ensure_columns(conn.cursor(), _COLS_DDL)
    conn.commit()
    start = clock()
    out: Dict[str, Any] = {"drafts": 0, "published": [], "verified": 0, "logos": 0, "profiled": 0,
                           "waiting": {}, "stopped_early": False}
    for brand in _drafts(conn, limit):
        if clock() - start > budget:
            out["stopped_early"] = True
            break
        out["drafts"] += 1
        bid = int(brand["id"])
        for name, step in (("verified", lambda: _verify_inbox(conn, brand)),
                           ("logos", lambda: _find_logo(conn, brand)),
                           ("profiled", lambda: out["profiled"] < profiles and _profile(conn, brand))):
            try:
                if step():
                    out[name] += 1
            except Exception as err:
                print(f"[autopublish] {name} failed for brand {bid}: {err}")
                conn.rollback()
        try:
            gate = evaluate(conn, bid)  # reads the fresh row, stores the result
        except Exception as err:
            print(f"[autopublish] gate failed for brand {bid}: {err}")
            conn.rollback()
            continue
        missing = publish_ready(gate, brand)
        if missing:
            _tried(conn, bid)
            for reason in missing:
                key = reason.split(":")[0]
                out["waiting"][key] = out["waiting"].get(key, 0) + 1
        else:
            _publish(conn, bid)
            out["published"].append({"id": bid, "brand_name": brand.get("brand_name")})
    out["published_count"] = len(out["published"])
    return out
