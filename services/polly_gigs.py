"""Polly paid-UGC aggregator — live scanner gigs, not gifted Directory pitches."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_GIFTED_PAY = re.compile(r"(?i)\bgifted|\bunpaid|\bfree\s+product|\bpr\s+package\b")

SOURCE_LABELS = {
    "aspireiq": "AspireIQ",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "twitter": "X",
    "x": "X",
    "craigslist": "Craigslist",
}


def source_platform_label(platform: Optional[str]) -> Optional[str]:
    raw = str(platform or "").strip().lower()
    if not raw:
        return None
    if raw in SOURCE_LABELS:
        return SOURCE_LABELS[raw]
    return raw.replace("_", " ").title()


def is_paid_listing(card: Optional[Dict[str, Any]] = None) -> bool:
    """Skip gifted/unpaid briefs. Sourced UGC defaults to paid unless labelled otherwise."""
    card = card or {}
    pay = str(card.get("pay_label") or "")
    if _GIFTED_PAY.search(pay):
        return False
    if card.get("pr_value_usd"):
        return True
    if re.search(r"\$|\bpaid\b", pay, re.I):
        return True
    if card.get("is_sourced"):
        return True
    return False


_BOARD_HOSTS = (
    "craigslist.org",
    "craigslist.com",
    "aspireiq.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
)


def _url_host(url: Optional[str]) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        from urllib.parse import urlparse
        host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
        return host
    except Exception:
        return ""


def public_brand_site(url: Optional[str]) -> Optional[str]:
    raw = str(url or "").strip() or None
    if not raw:
        return None
    host = _url_host(raw)
    if not host:
        return None
    if any(host == board or host.endswith("." + board) for board in _BOARD_HOSTS):
        return None
    if not re.match(r"^https?://", raw, re.I):
        return "https://" + raw
    return raw


def _clean_gig_blurb(text: Optional[str]) -> str:
    desc = _EMAIL_RE.sub("", str(text or ""))
    desc = re.sub(r"(?i)\n*Apply here:\s*\S+", "", desc)
    desc = re.sub(r"(?i)\n*Pay:\s*", "\n", desc)
    desc = re.sub(r"[ \t]+\n", "\n", desc)
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    if len(desc) > 900:
        desc = desc[:900].rsplit(" ", 1)[0].rstrip() + "…"
    return desc


_CL_AREAS = {
    "newyork": "New York",
    "sfbay": "SF Bay Area",
    "washingtondc": "Washington DC",
    "lasvegas": "Las Vegas",
    "orangecounty": "Orange County",
    "losangeles": "Los Angeles",
}


def listing_place(*urls: Optional[str]) -> Optional[str]:
    for url in urls:
        match = re.search(r"/view/d/([a-z0-9-]+)", str(url or ""), re.I)
        if match:
            slug = re.split(
                r"-(?:earn|paid|live|ugc|content|monthly)\b",
                match.group(1),
                maxsplit=1,
                flags=re.I,
            )[0]
            place = slug.replace("-", " ").strip()
            if 2 < len(place) < 40:
                return _CL_AREAS.get(place.lower().replace(" ", ""), place.title())
    for url in urls:
        host = _url_host(url)
        if host.endswith(".craigslist.org"):
            city = host.split(".")[0]
            if city and city not in {"www", "craigslist"}:
                return _CL_AREAS.get(city, city.replace("-", " ").title())
    return None


def gig_card_from_opp(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Compact Polly card. Never put emails in the blurb."""
    desc = _clean_gig_blurb(opp.get("campaign_description") or opp.get("blurb"))
    source = opp.get("source_platform")
    apply_mode = opp.get("apply_mode") or ("url" if opp.get("external_apply_url") else "kit")
    apply_email = None
    if apply_mode == "email":
        apply_email = (opp.get("apply_email") or "").strip() or None
    website = public_brand_site(opp.get("brand_website") or opp.get("website"))
    regions = opp.get("shipping_regions") or []
    location = None
    if isinstance(regions, list) and regions:
        location = " / ".join(str(part).strip() for part in regions[:2] if part)
    location = location or listing_place(
        opp.get("external_apply_url"),
        opp.get("brand_website"),
    )
    return {
        "id": opp.get("id"),
        "name": opp.get("brand_name") or opp.get("name"),
        "brand_name": opp.get("brand_name") or opp.get("name"),
        "logo": opp.get("brand_logo_url") or opp.get("logo"),
        "category": opp.get("display_niche") or opp.get("brand_category") or opp.get("category"),
        "pay_label": opp.get("pay_label"),
        "blurb": desc,
        "product_name": opp.get("product_name"),
        "website": website,
        "location": location,
        "fit_score": opp.get("fit_score"),
        "source_platform": source,
        "source_label": source_platform_label(source),
        "is_sourced": bool(opp.get("is_sourced")),
        "apply_mode": apply_mode,
        "external_apply_url": opp.get("external_apply_url"),
        "apply_email": apply_email,
        "already_applied": bool(opp.get("already_applied")),
    }


def _as_gig_id(raw) -> Optional[int]:
    try:
        gid = int(raw)
    except (TypeError, ValueError):
        return None
    return gid or None


def shown_gig_ids(notes: Optional[Dict] = None) -> List[int]:
    ids = []
    for raw in (notes or {}).get("shown_gig_ids") or []:
        gid = _as_gig_id(raw)
        if gid and gid not in ids:
            ids.append(gid)
    return ids


def gig_ids_from_history(history: Optional[List[Dict]] = None) -> List[int]:
    ids = []
    seen = set()
    for msg in history or []:
        for gig in msg.get("gigs") or []:
            gid = _as_gig_id((gig or {}).get("id"))
            if gid and gid not in seen:
                seen.add(gid)
                ids.append(gid)
    return ids


def mark_shown_gigs(notes: Optional[Dict], gigs: Optional[List[Dict]] = None) -> Dict[str, Any]:
    out = dict(notes or {})
    seen = shown_gig_ids(out)
    for gig in gigs or []:
        try:
            gid = int((gig or {}).get("id"))
        except (TypeError, ValueError):
            continue
        if gid and gid not in seen:
            seen.append(gid)
    out["shown_gig_ids"] = seen[-80:]
    return out


def _norm_gig_text(value: Optional[str]) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]", " ", text)
    text = re.sub(r"\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?", " ", text)
    text = re.sub(r"\b\d+\s*k\b", " ", text)
    text = re.sub(r"\bstreaming\b", "stream", text)
    text = re.sub(r"\blive[\s-]*stream\b", "live stream", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_words(text: str, other: str) -> str:
    keep = text
    for word in (other or "").split():
        if len(word) < 3:
            continue
        keep = re.sub(rf"\b{re.escape(word)}\b", " ", keep)
    return re.sub(r"\s+", " ", keep).strip()


def gig_dedupe_key(card: Optional[Dict[str, Any]] = None) -> tuple:
    """Same brand + same brief, even if Craigslist posted it in 3 cities."""
    card = card or {}
    brand = _norm_gig_text(card.get("brand_name") or card.get("name"))
    source = str(card.get("source_platform") or card.get("source_label") or "").strip().lower()
    title = _strip_words(_norm_gig_text(card.get("product_name")), brand)
    if len(title) < 10:
        blurb = _strip_words(
            _norm_gig_text(card.get("blurb") or card.get("campaign_description")),
            brand,
        )
        title = " ".join(blurb.split()[:8])
    return (brand, source, title)


def gig_fingerprints_from_history(history: Optional[List[Dict]] = None) -> set:
    keys = set()
    for msg in history or []:
        for gig in msg.get("gigs") or []:
            key = gig_dedupe_key(gig)
            if key and key[0]:
                keys.add(key)
    return keys


def extra_tokens_from_profile(scrape: Optional[Dict] = None, notes: Optional[Dict] = None) -> List[str]:
    from opportunities_routes import _tokenize_niche_blob
    from services.polly_discovery import stated_niches

    tokens = set()
    scrape = scrape or {}
    notes = notes or {}
    tokens |= _tokenize_niche_blob(scrape.get("primary_niche"))
    tokens |= _tokenize_niche_blob(scrape.get("niches"))
    tokens |= _tokenize_niche_blob(notes.get("niche"))
    tokens |= _tokenize_niche_blob(stated_niches(notes))
    return list(tokens)


def _scanner_fallback_cards(creator_id) -> List[Dict[str, Any]]:
    """Live piggyback gigs only — skips Directory gifted-PR rows."""
    from opportunities_routes import (
        _opportunity_card,
        _tokenize_niche_blob,
        get_db_connection,
    )
    from psycopg2.extras import RealDictCursor

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT niche, creator_niches
            FROM creators
            WHERE id = %s
            """,
            (creator_id,),
        )
        creator = cursor.fetchone() or {}
        tokens = _tokenize_niche_blob((creator or {}).get("creator_niches"))
        tokens |= _tokenize_niche_blob((creator or {}).get("niche"))
        cursor.execute(
            """
            SELECT
                id, brand_name, brand_category, brand_logo_url, brand_website,
                product_name, campaign_description,
                pr_value_usd, creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes,
                spots_total, spots_filled, closes_at,
                created_at
            FROM opportunities
            WHERE status = 'live'
              AND additional_notes ILIKE %s
              AND (closes_at IS NULL OR closes_at > NOW())
            ORDER BY created_at DESC
            LIMIT 80
            """,
            ("%[scanner:%",),
        )
        rows = cursor.fetchall() or []
        cursor.execute(
            """
            SELECT opportunity_id FROM opportunity_applications
            WHERE creator_id = %s
            """,
            (creator_id,),
        )
        applied_ids = {row["opportunity_id"] for row in cursor.fetchall()}
    finally:
        cursor.close()
        conn.close()
    cards = []
    for opp in rows:
        serialized, _is_match = _opportunity_card(opp, tokens, applied_ids)
        cards.append(serialized)
    return cards


def list_polly_gigs(
    creator_id,
    scrape: Optional[Dict] = None,
    notes: Optional[Dict] = None,
    limit: int = 3,
    exclude_ids: Optional[List[int]] = None,
    history: Optional[List[Dict]] = None,
) -> List[Dict[str, Any]]:
    """Top live paid UGC briefs for Polly. Piggyback sources first, no gifted PR mix."""
    if not creator_id:
        return []
    ranked: List[Dict[str, Any]] = []
    try:
        from opportunities_routes import fetch_live_opportunity_cards
        data = fetch_live_opportunity_cards(
            creator_id,
            extra_tokens=extra_tokens_from_profile(scrape, notes),
        )
        if data:
            ranked = list(data.get("matched") or []) + list(data.get("others") or [])
    except Exception as err:
        print(f"[Polly] gigs fetch failed: {err}")
        ranked = []
    sourced_in_pool = [c for c in ranked if c.get("is_sourced")]
    if not sourced_in_pool:
        try:
            ranked = _scanner_fallback_cards(creator_id) + ranked
        except Exception as err:
            print(f"[Polly] gigs scanner fallback failed: {err}")
    paid = [c for c in ranked if is_paid_listing(c)]
    sourced = [c for c in paid if c.get("is_sourced")]
    native = [c for c in paid if not c.get("is_sourced")]
    open_first = [c for c in sourced + native if not c.get("already_applied")]
    pool = open_first or (sourced + native)
    skip = set(exclude_ids or [])
    skip |= set(shown_gig_ids(notes))
    skip |= set(gig_ids_from_history(history))
    seen_fp = gig_fingerprints_from_history(history)
    print(
        f"[Polly] gigs pool={len(ranked)} paid={len(paid)} "
        f"sourced={len(sourced)} open={len(open_first)} skip={len(skip)}"
    )
    cards = []
    seen = set()
    for opp in pool:
        key = opp.get("id")
        try:
            kid = int(key)
        except (TypeError, ValueError):
            kid = None
        if key in seen or kid in skip or key in skip:
            continue
        seen.add(key)
        card = gig_card_from_opp(opp)
        fp = gig_dedupe_key(card)
        if fp in seen_fp:
            continue
        if card.get("id") and card.get("name"):
            if fp[0]:
                seen_fp.add(fp)
            cards.append(card)
        if len(cards) >= limit:
            break
    return cards


_MORE_GIGS_RE = re.compile(
    r"(?i)^(find more|more|another|next|show more|next one|another one|"
    r"more (gigs?|offers?|paid|ugc)|find more (gigs?|offers?)|"
    r"find more offers)[\s!.]*$"
    r"|\b(find more|more offers|more gigs|next drop)\b"
)


_GIG_DROP_RE = re.compile(
    r"(?i)apply here|paid ugc gig|same idea as indeed|via aspireiq"
)


def last_assistant_had_gigs(history: Optional[List[Dict]] = None) -> bool:
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        if msg.get("gigs"):
            return True
        return bool(_GIG_DROP_RE.search(str(msg.get("content") or "")))
    return False


def wants_more_gigs(
    text: str,
    history: Optional[List[Dict]] = None,
    notes: Optional[Dict] = None,
) -> bool:
    from services.polly import is_more_brands_turn

    raw = (text or "").strip()
    notes = notes or {}
    already = (
        last_assistant_had_gigs(history)
        or notes.get("saw_gigs")
        or notes.get("wanted_gigs")
        or bool(shown_gig_ids(notes))
        or bool(gig_ids_from_history(history))
    )
    if not already:
        return False
    if re.search(r"\bbrands?\b", raw, re.I) and not re.search(r"\b(gig|offer|ugc|paid)\b", raw, re.I):
        return False
    if _MORE_GIGS_RE.search(raw):
        return True
    return is_more_brands_turn(raw)
