"""Paid UGC board: every live paid gig in one browsable list, Pro unlocks the full brief."""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.polly_gigs import (
    gig_card_from_opp,
    gig_dedupe_key,
    gig_fits_creator,
    is_paid_listing,
    newest_published,
    published_stamp,
)

FREE_PREVIEW = 3
PAGE_SIZE = 24
NEW_DAYS = 3
HIGH_PAY_USD = 500
VIEWS = ("all", "for_you", "new", "high_pay")

_PUBLIC_FIELDS = (
    "id", "brand_name", "brand_unknown", "headline", "summary", "deliverable",
    "pay_label", "pr_value_usd", "location", "category", "logo", "website",
    "source_label", "source_platform", "apply_mode", "posted_at", "already_applied",
)
_LOCKED_FIELDS = (
    "id", "pay_label", "pr_value_usd", "deliverable", "category", "location",
    "source_label", "source_platform", "posted_at",
)


def _pay_usd(card: Dict[str, Any]) -> float:
    try:
        return float(card.get("pr_value_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _age_days(card: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    stamp = published_stamp(card)
    if not stamp:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now.timestamp() - stamp) / 86400)


def is_new_gig(card: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    age = _age_days(card, now)
    return age is not None and age <= NEW_DAYS


def board_cards(rows: List[Dict[str, Any]], followers: int = 0) -> List[Dict[str, Any]]:
    """Paid, size-appropriate, one card per brief (Craigslist reposts collapse)."""
    out: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_fp = set()
    for row in newest_published(rows):
        if row.get("id") in seen_ids:
            continue
        if not is_paid_listing(row) or not gig_fits_creator(row, followers):
            continue
        card = gig_card_from_opp(row)
        if not card.get("id") or not (card.get("brand_name") or card.get("headline")):
            continue
        fp = gig_dedupe_key(card)
        if fp[0] and fp in seen_fp:
            continue
        seen_ids.add(row.get("id"))
        if fp[0]:
            seen_fp.add(fp)
        card["is_match"] = bool(row.get("is_matched"))
        out.append(card)
    return out


def filter_view(cards: List[Dict[str, Any]], view: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    if view == "for_you":
        return [c for c in cards if c.get("is_match")]
    if view == "new":
        return [c for c in cards if is_new_gig(c, now)]
    if view == "high_pay":
        return sorted(
            [c for c in cards if _pay_usd(c) >= HIGH_PAY_USD],
            key=lambda c: -_pay_usd(c),
        )
    return cards


def rank_cards(cards: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Open before applied; niche matches posted this week lead, then newest."""
    def key(card):
        fresh_match = bool(card.get("is_match")) and (_age_days(card, now) or 99) <= 7
        return (bool(card.get("already_applied")), not fresh_match, -published_stamp(card))
    return sorted(cards, key=key)


def board_stats(cards: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    pays = [p for p in (_pay_usd(c) for c in cards) if p > 0]
    return {
        "total": len(cards),
        "new_this_week": sum(1 for c in cards if (_age_days(c, now) or 99) <= 7),
        "for_you": sum(1 for c in cards if c.get("is_match")),
        "median_pay_usd": int(statistics.median(pays)) if pays else None,
        "open_pay_usd": int(sum(pays)),
    }


def public_card(card: Dict[str, Any], locked: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Never ship apply links in the list: the apply endpoint hands them out and logs it."""
    fields = _LOCKED_FIELDS if locked else _PUBLIC_FIELDS
    out = {k: card.get(k) for k in fields}
    if not locked and not greeting_brand(card):
        out["brand_unknown"] = True
    out["locked"] = locked
    out["is_new"] = is_new_gig(card, now)
    out["is_match"] = bool(card.get("is_match"))
    return out


def page_of(cards: List[Dict[str, Any]], offset: int = 0, limit: int = PAGE_SIZE) -> List[Dict[str, Any]]:
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or PAGE_SIZE), 60))
    return cards[offset:offset + limit]


def preview_ids(ranked_all: List[Dict[str, Any]], is_pro: bool) -> Optional[set]:
    """Free users see the same top briefs whichever filter they pick. None means all open."""
    if is_pro:
        return None
    return {c.get("id") for c in ranked_all[:FREE_PREVIEW]}


def _followers_text(count) -> str:
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return str(n) if n > 0 else ""


_TITLE_WORDS = re.compile(
    r"(?i)\b(minutes?|seconds?|videos?|creators?|ugc|needed|wanted|seeking|looking|hiring|"
    r"paid|raw|unedited|content|campaign|program|talking|camera|reels?|tiktoks?|"
    r"site|review|job|gig|ads?|actors?|models?|female|male|ambassadors?|growth|editor|"
    r"specialist|promos?|demo|host|casting|management|marketing|assignment|influencers?|"
    r"facebook|instagram|youtube|motivated|creative|wanted|urgent|part[- ]time)\b"
)


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def greeting_brand(gig: Dict[str, Any]) -> str:
    """Brand name safe to greet. Scanned rows often carry the gig title in brand_name."""
    gig = gig or {}
    if gig.get("brand_unknown"):
        return ""
    name = str(gig.get("brand_name") or "").strip()
    if not name or re.fullmatch(r"(?i)unknown brand|paid ugc offer", name):
        return ""
    if _norm(name) == _norm(gig.get("headline")) and len(name.split()) > 3:
        return ""
    name = re.split(r"\s*[:|–—]\s*|\s+-\s+", name, maxsplit=1)[0].strip()
    if not name or not name[0].isupper() and not name[0].isdigit():
        return ""
    if re.search(r"\d", name) or len(name.split()) > 5 or len(name) > 32:
        return ""
    if _TITLE_WORDS.search(name):
        return ""
    return name


def creator_niche(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = raw.strip("[]")
    if isinstance(raw, (list, tuple)):
        raw = next((str(n) for n in raw if str(n or "").strip()), "")
    text = re.sub(r"[\"'\[\]]", "", str(raw or ""))
    return text.split(",")[0].strip().lower()


def draft_application(creator: Dict[str, Any], gig: Dict[str, Any], kit_url: Optional[str] = None) -> Dict[str, str]:
    """Short, factual application. Only facts we hold: no invented results or rates."""
    creator = creator or {}
    gig = gig or {}
    first = str(creator.get("first_name") or "").strip().split(" ")[0]
    handle = str(creator.get("username") or "").strip().lstrip("@")
    sign = first or (f"@{handle}" if handle else "")
    niche = creator_niche(creator.get("niche"))
    followers = _followers_text(creator.get("followers_count"))
    brand = greeting_brand(gig)
    headline = str(gig.get("headline") or "").strip()
    source = str(gig.get("source_label") or "").strip()

    role = f"a {niche} UGC creator" if niche else "a UGC creator"
    intro = f"I'm {first}, {role}" if first else f"I'm {role}"
    if handle:
        intro += f" (@{handle}"
        intro += f", {followers} followers)" if followers else ")"
    intro += "."

    where = f" on {source}" if source else ""
    fit = f"I saw your brief{where} and it's a great fit for the content I make."

    lines = [f"Hi {brand} team," if brand else "Hi there,", "", intro, fit]
    if kit_url:
        lines += ["", f"Portfolio, past work and rates: {kit_url}"]
    lines += [
        "",
        "I can start this week and keep revisions quick. Happy to share a concept before filming.",
        "",
        "Thanks,",
    ]
    if sign:
        lines.append(sign)
    subject = f"UGC application: {headline or brand or 'your brief'}"
    if brand and headline and brand.lower() not in headline.lower():
        subject = f"UGC application: {brand}, {headline}"
    if handle:
        subject += f" (@{handle})"
    return {"subject": subject[:140], "body": "\n".join(lines).strip()}
