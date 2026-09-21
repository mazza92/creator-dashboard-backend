"""Clean Hermes scanner gigs before they hit Opportunities / Polly."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from services.gig_listing import (
    apply_llm_rewrite,
    clean_listing_text,
    ensure_listing_brief_column,
    format_pay_label,
    heuristic_summary,
    listing_source_hash,
    llm_rewrite_listings,
    pay_has_amount,
    prefer_amount_pay,
    structure_gig_card,
)
from services.polly_gigs import public_brand_site

_GIFTED_RE = re.compile(r"(?i)\bgift(ed)?\b|\bseeding\b|\bfree\s*product\b|\bpr\s*package\b|\bunpaid\b")


def _pay_bits(raw: Dict[str, Any]) -> str:
    bits = []
    display = str(raw.get("compensation_display") or "").strip()
    if display:
        bits.append(display)
    lo = raw.get("compensation_min_usd")
    hi = raw.get("compensation_max_usd")
    if lo and hi:
        bits.append(f"${lo}–${hi}")
    elif lo:
        bits.append(f"${lo}+")
    elif hi:
        bits.append(f"${hi}")
    if raw.get("compensation_type"):
        bits.append(str(raw["compensation_type"]))
    return " · ".join(bits)


def _pr_value_from_pay(pay_label: Optional[str], existing=None):
    if _GIFTED_RE.search(str(pay_label or "")):
        return None
    nums = re.findall(r"\d[\d,]*", str(pay_label or ""))
    if nums:
        try:
            return int(nums[-1].replace(",", ""))
        except ValueError:
            pass
    try:
        return int(existing) if existing is not None else None
    except (TypeError, ValueError):
        return None


def _source_blob(raw: Dict[str, Any], include_pay: bool = True) -> str:
    deliverable = (raw.get("deliverable_summary") or raw.get("campaign_description") or "").strip()
    title = (raw.get("title") or raw.get("product_name") or "").strip()
    extras = []
    if deliverable:
        extras.append(deliverable)
    elif title:
        extras.append(title)
    if include_pay:
        pay = _pay_bits(raw)
        if pay:
            extras.append(pay)
    req = (raw.get("other_requirements") or "").strip()
    if req:
        extras.append(req)
    return "\n\n".join(extras)


def _source_text(raw: Dict[str, Any]) -> str:
    return clean_listing_text(_source_blob(raw, include_pay=False))


def _is_pay_paragraph(text: str) -> bool:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(t) > 140:
        return False
    if t.lower() in {"paid", "gifted", "gifted product", "unpaid"}:
        return True
    if re.match(r"(?i)^pay:\s*", t):
        return True
    if pay_has_amount(t):
        return True
    if re.search(r"(?i)\b(video|tiktok|content|create|shoot|review|campaign for)\b", t) and not pay_has_amount(t):
        return False
    if _GIFTED_RE.search(t):
        return True
    if re.match(r"(?i)^(paid|gifted)\b", t) and len(t.split()) <= 8:
        return True
    return False


def _split_body_and_pay(text: str) -> tuple[str, List[str]]:
    blob = str(text or "").strip()
    parts = [p.strip() for p in re.split(r"\n\s*\n", blob) if p.strip()]
    if len(parts) <= 1:
        mashed = parts[0] if parts else blob
        cut = re.search(
            r"(?i)^(.*?)\s+((?:Pay:\s*)?(?:Gifted PR.*|Gifted product|Paid (?:campaign|partnership|UGC|Editing).*|\$[\d,].*))$",
            mashed,
        )
        if cut:
            parts = [cut.group(1).strip(), cut.group(2).strip()]
        elif " · " in mashed:
            parts = [p.strip() for p in mashed.split(" · ") if p.strip()]
    body = []
    pay_parts = []
    for part in parts:
        if _is_pay_paragraph(part):
            pay_parts.append(re.sub(r"(?i)^pay:\s*", "", part).strip())
        else:
            body.append(part)
    descr = "\n\n".join(body).strip()
    descr = re.sub(
        r"(?i)(?:\s*[·,]?\s*(?:gifted product|gifted pr(?:\s*\([^)]+\))?|gifted|paid campaign|paid partnership|paid ugc|paid))+$",
        "",
        descr,
    ).strip(" ·-")
    return descr, pay_parts


def listing_brief_from_card(card: Dict[str, Any], raw_text: str, original: Optional[str] = None) -> Dict[str, Any]:
    orig = (original or card.get("listing_original") or raw_text or "")[:2500]
    return {
        "id": str(card.get("id") or ""),
        "brand": card.get("brand_name") or "",
        "headline": card.get("headline") or "",
        "summary": card.get("summary") or "",
        "deliverable": card.get("deliverable") or "",
        "pay": card.get("pay_label") or "",
        "src": card.get("listing_src") or listing_source_hash(card),
        "raw": orig,
        "original": orig,
    }


def clean_scanner_gig(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic clean of one Hermes payload. No LLM. Safe for ingest."""
    raw = raw or {}
    title = (raw.get("title") or raw.get("product_name") or "").strip()
    buyer = (raw.get("buyer_name") or raw.get("brand_name") or "").strip() or "Unknown brand"
    apply_url = (raw.get("apply_url") or raw.get("source_url") or "").strip()
    original = _source_blob(raw, include_pay=True)
    source_text = clean_listing_text(_source_blob(raw, include_pay=False))
    source_text = re.sub(r"https?://\S+", "", source_text).strip()
    source_text, pay_parts = _split_body_and_pay(source_text)
    pay_hint = _pay_bits(raw)
    if pay_parts:
        pay_hint = " · ".join([pay_hint] + pay_parts).strip(" ·")
    gifted = bool(_GIFTED_RE.search(f"{pay_hint} {original} {title}"))
    pay_label = "Gifted product" if gifted else format_pay_label(
        raw.get("pr_value_usd") or raw.get("compensation_max_usd") or raw.get("compensation_min_usd"),
        pay_hint,
        original,
        source_text,
        title,
    )
    card = structure_gig_card({
        "brand_name": buyer,
        "product_name": title,
        "campaign_description": source_text,
        "raw_listing": source_text,
        "pay_label": pay_label,
        "pr_value_usd": None if gifted else (raw.get("pr_value_usd") or raw.get("compensation_max_usd") or raw.get("compensation_min_usd")),
        "source_platform": raw.get("source_platform"),
        "category": raw.get("category") or raw.get("brand_category"),
        "listing_original": original,
    })
    summary = card.get("summary") or heuristic_summary(source_text, card.get("brand_name"))
    deliverable = card.get("deliverable") or ""
    description = summary
    if deliverable and deliverable.lower() not in (summary or "").lower():
        description = f"{deliverable}. {summary}".strip()
    website = public_brand_site(raw.get("brand_website") or raw.get("website"))
    pay_label = prefer_amount_pay(card.get("pay_label"), pay_label)
    if gifted:
        pay_label = "Gifted product"
    card["pay_label"] = pay_label
    return {
        "brand_name": (card.get("brand_name") or card.get("name") or buyer)[:255],
        "product_name": (card.get("headline") or title)[:255],
        "campaign_description": description or title,
        "brand_website": website,
        "pr_value_usd": None if gifted else _pr_value_from_pay(
            pay_label,
            raw.get("pr_value_usd") or raw.get("compensation_max_usd") or raw.get("compensation_min_usd"),
        ),
        "pay_label": pay_label,
        "gifted": gifted,
        "listing_brief": listing_brief_from_card(card, original, original),
        "apply_url": apply_url,
        "title": title,
        "buyer": buyer,
    }


def clean_opportunity_row(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic clean of an existing opportunities row."""
    brief = opp.get("listing_brief")
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except Exception:
            brief = None
    raw_text = ""
    original = ""
    if isinstance(brief, dict):
        original = str(brief.get("original") or "")
        raw_text = original or str(brief.get("raw") or "")
    if not raw_text:
        raw_text = str(opp.get("campaign_description") or "")
    pay_match = re.search(r"(?i)Pay:\s*(.+)", raw_text)
    payload = {
        "title": opp.get("product_name"),
        "buyer_name": opp.get("brand_name"),
        "campaign_description": raw_text,
        "brand_website": opp.get("brand_website"),
        "pr_value_usd": opp.get("pr_value_usd"),
        "source_platform": None,
        "category": opp.get("brand_category"),
    }
    if pay_match:
        payload["compensation_display"] = pay_match.group(1).strip()
    elif isinstance(brief, dict) and brief.get("pay"):
        payload["compensation_display"] = str(brief.get("pay"))
    notes = opp.get("additional_notes") or ""
    match = re.search(r"\[scanner:([^:\]]+):", notes)
    if match:
        payload["source_platform"] = match.group(1)
    cleaned = clean_scanner_gig(payload)
    cleaned["id"] = opp.get("id")
    brief_out = cleaned.setdefault("listing_brief", {})
    brief_out["id"] = str(opp.get("id") or "")
    if original:
        brief_out["original"] = original[:2500]
        brief_out["raw"] = original[:2500]
    return cleaned


def apply_clean_to_cursor(cursor, opp_id, cleaned: Dict[str, Any]) -> None:
    cursor.execute(
        """
        UPDATE opportunities
        SET brand_name = %s,
            product_name = %s,
            campaign_description = %s,
            brand_website = %s,
            pr_value_usd = %s,
            listing_brief = %s
        WHERE id = %s
        """,
        (
            cleaned.get("brand_name"),
            cleaned.get("product_name"),
            cleaned.get("campaign_description"),
            cleaned.get("brand_website"),
            cleaned.get("pr_value_usd"),
            json.dumps(cleaned.get("listing_brief") or {}),
            opp_id,
        ),
    )


def enrich_opportunity_ids(ids: List[Any], use_llm: bool = True) -> Dict[str, int]:
    """Load rows, heuristic-clean, optional Gemini rewrite, persist."""
    from opportunities_routes import get_db_connection
    from psycopg2.extras import RealDictCursor

    clean_ids = []
    for raw in ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return {"enriched": 0, "llm": 0}
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    llm_n = 0
    try:
        ensure_listing_brief_column(conn, cursor)
        cursor.execute(
            """
            SELECT id, brand_name, brand_website, brand_category, product_name,
                   campaign_description, pr_value_usd, additional_notes, listing_brief
            FROM opportunities
            WHERE id = ANY(%s)
            """,
            (clean_ids,),
        )
        rows = cursor.fetchall() or []
        cards = []
        cleaned_by_id = {}
        for opp in rows:
            cleaned = clean_opportunity_row(opp)
            cleaned_by_id[opp["id"]] = cleaned
            card = structure_gig_card({
                "id": opp["id"],
                "brand_name": cleaned["brand_name"],
                "product_name": cleaned["product_name"],
                "campaign_description": (cleaned.get("listing_brief") or {}).get("raw") or cleaned["campaign_description"],
                "pay_label": cleaned.get("pay_label"),
                "pr_value_usd": cleaned.get("pr_value_usd"),
            })
            cards.append(card)
        rewrites = llm_rewrite_listings(cards) if use_llm else {}
        for card in cards:
            row = rewrites.get(str(card.get("id") or ""))
            if row:
                apply_llm_rewrite(card, row)
                llm_n += 1
            cleaned = cleaned_by_id.get(card.get("id")) or {}
            cleaned["brand_name"] = card.get("brand_name") or cleaned.get("brand_name")
            cleaned["product_name"] = card.get("headline") or cleaned.get("product_name")
            summary = card.get("summary") or cleaned.get("campaign_description")
            deliverable = card.get("deliverable") or ""
            if deliverable and deliverable.lower() not in (summary or "").lower():
                summary = f"{deliverable}. {summary}".strip()
            cleaned["campaign_description"] = summary
            cleaned["pay_label"] = card.get("pay_label") or cleaned.get("pay_label")
            orig = (cleaned.get("listing_brief") or {}).get("original") or (
                (cleaned.get("listing_brief") or {}).get("raw") or ""
            )
            cleaned["listing_brief"] = listing_brief_from_card(card, orig, orig)
            apply_clean_to_cursor(cursor, card.get("id"), cleaned)
        conn.commit()
        return {"enriched": len(rows), "llm": llm_n}
    except Exception as err:
        conn.rollback()
        print(f"[opps] enrich failed: {err}")
        return {"enriched": 0, "llm": 0, "error": str(err)}
    finally:
        cursor.close()
        conn.close()


def restore_opportunity_row(opp: Dict[str, Any]) -> Dict[str, Any]:
    """Put pay back onto a cleaned scanner row without inventing rates."""
    brief = opp.get("listing_brief")
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except Exception:
            brief = None
    if not isinstance(brief, dict):
        brief = {}
    original = str(brief.get("original") or brief.get("raw") or opp.get("campaign_description") or "")
    body, pay_parts = _split_body_and_pay(original)
    if not body:
        extra_body, extra_pay = _split_body_and_pay(str(opp.get("campaign_description") or ""))
        body = extra_body
        pay_parts = pay_parts or extra_pay
    pay_label = prefer_amount_pay(
        brief.get("pay"),
        format_pay_label(opp.get("pr_value_usd"), original, *pay_parts),
        *pay_parts,
    )
    gifted = bool(_GIFTED_RE.search(f"{pay_label} {original}"))
    if gifted and not pay_has_amount(pay_label):
        pay_label = "Gifted product"
    pr_value = None if gifted else _pr_value_from_pay(pay_label, opp.get("pr_value_usd"))
    brief["pay"] = pay_label or brief.get("pay") or ""
    brief["raw"] = original[:2500]
    brief["original"] = original[:2500]
    if opp.get("id") is not None:
        brief["id"] = str(opp.get("id"))
    return {
        "brand_name": opp.get("brand_name"),
        "product_name": opp.get("product_name"),
        "campaign_description": body or original,
        "brand_website": opp.get("brand_website"),
        "pr_value_usd": pr_value,
        "pay_label": pay_label,
        "listing_brief": brief,
        "gifted": gifted,
    }


def restore_opportunity_ids(ids: Optional[List[Any]] = None, status: str = "live") -> Dict[str, int]:
    """Restore pay fields for scanner listings already cleaned in the DB."""
    from opportunities_routes import get_db_connection
    from psycopg2.extras import RealDictCursor

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_listing_brief_column(conn, cursor)
        if ids:
            clean_ids = []
            for raw in ids:
                try:
                    clean_ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
            if not clean_ids:
                return {"restored": 0, "with_amount": 0}
            cursor.execute(
                """
                SELECT id, brand_name, brand_website, brand_category, product_name,
                       campaign_description, pr_value_usd, additional_notes, listing_brief
                FROM opportunities
                WHERE id = ANY(%s)
                """,
                (clean_ids,),
            )
        else:
            cursor.execute(
                """
                SELECT id, brand_name, brand_website, brand_category, product_name,
                       campaign_description, pr_value_usd, additional_notes, listing_brief
                FROM opportunities
                WHERE additional_notes ILIKE %s
                  AND status = %s
                ORDER BY created_at DESC
                """,
                ("%[scanner:%", status),
            )
        rows = cursor.fetchall() or []
        with_amount = 0
        for opp in rows:
            restored = restore_opportunity_row(opp)
            if pay_has_amount(restored.get("pay_label")):
                with_amount += 1
            apply_clean_to_cursor(cursor, opp["id"], restored)
        conn.commit()
        return {"restored": len(rows), "with_amount": with_amount}
    except Exception as err:
        conn.rollback()
        print(f"[opps] restore failed: {err}")
        return {"restored": 0, "with_amount": 0, "error": str(err)}
    finally:
        cursor.close()
        conn.close()
