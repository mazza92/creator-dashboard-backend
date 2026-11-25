"""Thanos engine: the one ranking every Newcollab feature reads to choose brands for a creator.

    rank(conn, creator_id, surface="polly", n=6) -> Ranking

Steps, the same for every surface:
1. Profile: niches, size, country, scrape, what they already pitched (services.thanos.profile).
2. Candidates from the directory, level by level (the ladder) until `n` good brands are found:
     L0 their categories, within 5x their size          (lane core)
     L1 neighbouring categories, within 5x              (lane related)
     L2 their + neighbouring categories, any size       (lane related, out-of-reach brands score low)
     L3 any category with replies or roster picks       (lane wide)
     L4 any category, added to the directory in 14 days (lane wide)
     L5 brands they pitched 60+ days ago, no reply, no "no": a follow-up, not a first pitch
   Every level drops brands that can't be pitched: no inbox (or no form, per surface), bounced, not accepting,
   sensitive categories, brands that never fit them (parenting/CBD, optical, wigs, gender lanes), their Polly
   "never pitch" preferences, and brands proven cold.
3. Score: reply chance (services.thanos.model) from live signals (services.thanos.signals).
4. Order: Thompson draw on reply chance (exploration), then mix categories so one category can't fill a list.

`level` and `note` say how far down the ladder the list went, so Polly can be honest ("close to your niche").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.thanos import model as M
from services.thanos import signals as S
from services.thanos.profile import CreatorProfile, load_profile

# Who may be returned, per surface.
#   email: a deliverable inbox (pitching surfaces)
#   contact: an inbox, an application form, or an open roster (browsing surfaces)
SURFACES: Dict[str, Dict[str, Any]] = {
    "polly": {"require": "email", "include_saved": False},
    "first_matches": {"require": "email", "include_saved": False},
    "autopilot": {"require": "email", "include_saved": True},
    "plan": {"require": "email", "include_saved": True},
    "for_you": {"require": "contact", "include_saved": False},
    "hotlist": {"require": "email", "include_saved": False},
    "alternatives": {"require": "email", "include_saved": False, "explore": False},
    "checkin": {"require": "email", "include_saved": False},
    "email": {"require": "email", "include_saved": False},
    "search": {"require": "contact", "include_saved": True, "explore": False},
    # No creator (public AI-assistant connectors): any published brand open to creators; Polly pitches after signup.
    "public": {"require": "any", "include_saved": True, "explore": False},
}

MIN_FIT = 35            # fit floor on the first levels (For You's growth_match floor)
WIDE_MIN_FIT = 25       # fit floor once the ladder goes wide
PER_LEVEL = 300         # candidates read per level
FRESH_DAYS = 14

LEVEL_NOTES = {
    0: "",
    1: "close to your niche",
    2: "bigger brands, longer shot",
    3: "brands that answer creators",
    4: "new this week",
    5: "follow-up: pitched a while ago, no answer yet",
}

# Neighbouring categories for niches with few brands of their own (their own niche always comes first).
NEIGHBOURS = {
    "luxury": ["fashion", "jewelry", "beauty"],
    "travel": ["lifestyle", "luxury", "fashion"],
    "lifestyle": ["fashion", "home", "wellness"],
    "home": ["lifestyle", "food"],
    "jewelry": ["fashion", "luxury"],
    "fashion": ["jewelry", "activewear", "beauty"],
    "beauty": ["skincare", "haircare", "wellness"],
    "skincare": ["beauty", "haircare", "wellness"],
    "haircare": ["beauty", "skincare"],
    "wellness": ["supplements", "fitness", "food"],
    "supplements": ["wellness", "fitness", "food"],
    "fitness": ["activewear", "supplements", "wellness"],
    "activewear": ["fitness", "fashion"],
    "food": ["home", "wellness", "lifestyle"],
    "pet": ["home", "lifestyle"],
    "baby": ["home", "lifestyle", "wellness"],
    "tech": ["lifestyle", "home"],
    "gaming": ["tech"],
    "sustainable": ["home", "beauty", "fashion"],
}

_BAD_EMAIL_STATUSES = ("bounced", "invalid", "undeliverable", "risky")
_NONE = "__none__"  # placeholder for an empty SQL array


@dataclass
class Ranking:
    brands: List[Dict[str, Any]] = field(default_factory=list)
    level: int = 0                  # deepest ladder level used
    note: str = ""
    profile: Optional[CreatorProfile] = None
    considered: int = 0

    def ids(self) -> List[int]:
        return [int(b["id"]) for b in self.brands]


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _seed(creator_id: int, salt: str = "") -> str:
    return f"{creator_id}:{date.today().isoformat()}:{salt}"


def _contact_sql(require: str) -> str:
    email_ok = (
        "NULLIF(TRIM(b.contact_email), '') IS NOT NULL "
        f"AND COALESCE(LOWER(b.email_status), '') NOT IN {_BAD_EMAIL_STATUSES!r} "
        "AND COALESCE(b.email_bounce_count, 0) = 0"
    )
    if require == "email":
        return f"AND {email_ok} AND COALESCE(b.accepting_pr, TRUE)"
    if require == "any":
        return "AND COALESCE(b.accepting_pr, TRUE)"
    return (f"AND (({email_ok}) OR NULLIF(TRIM(b.application_form_url), '') IS NOT NULL "
            "OR COALESCE(roster_demand.is_open, 0) > 0)")


def candidates_sql(require: str, *, categories: bool, reach_cap: bool, only_ids: bool, fresh: bool) -> str:
    from services.roster_demand import ROSTER_DEMAND_JOIN, ROSTER_DEMAND_SELECT
    where = [
        "COALESCE(b.status, 'published') = 'published'",
        "NOT (b.id = ANY(%(skip)s))",
        "LOWER(COALESCE(b.category, '')) <> ALL(%(excluded)s)",
    ]
    if categories:
        where.append("LOWER(COALESCE(b.category, '')) = ANY(%(cats)s)")
    if reach_cap:
        where.append("(%(cap)s <= 0 OR b.min_followers IS NULL OR b.min_followers <= %(cap)s)")
    if only_ids:
        where.append("b.id = ANY(%(ids)s)")
    if fresh:
        where.append(f"COALESCE(b.discovered_at, b.created_at) > NOW() - INTERVAL '{FRESH_DAYS} days'")
    return f"""
        SELECT b.id, b.slug, b.brand_name, b.logo_url, b.description, b.category, b.min_followers,
               b.max_followers, b.website, b.application_form_url, b.has_application_form, b.hero_product,
               b.target_audience, b.niches, b.regions, b.ships_to, b.contact_email, b.verified_contact,
               b.gifting_confirmed_at, b.gifting_signal, b.price_point, b.micro_friendly, b.avg_product_value, b.response_rate,
               b.cover_image_url, b.collaboration_type, b.payment_offered,
               COALESCE(s.ugc_style, FALSE) AS ugc_style,
               {ROSTER_DEMAND_SELECT}
        FROM pr_brands b
        LEFT JOIN brand_ad_signals s ON s.brand_id = b.id
        {ROSTER_DEMAND_JOIN}
        WHERE {' AND '.join(where)}
          {_contact_sql(require)}
        ORDER BY COALESCE(roster_demand.is_open, 0) DESC,
                 (b.gifting_confirmed_at IS NOT NULL OR COALESCE(s.ugc_style, FALSE)) DESC,
                 md5(concat_ws(':', b.id::text, %(seed)s))
        LIMIT %(limit)s
    """


def _fetch(conn, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from psycopg2.extras import RealDictCursor
    from services.thanos.db import fetch
    try:
        return [dict(r) for r in fetch(conn, sql, params, cursor_factory=RealDictCursor) or []]
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] candidate query failed: {err}")
        return []


def neighbours(categories: Iterable[str]) -> List[str]:
    from services.thanos.niches import FOR_YOU_RELATED_NICHES
    own = [str(c).lower() for c in categories or []]
    out: List[str] = []
    for c in own:
        for n in NEIGHBOURS.get(c, []) + list(FOR_YOU_RELATED_NICHES.get(c, [])):
            if n not in own and n not in out:
                out.append(n)
    return out


def _blocked(brand: Dict[str, Any], prof: CreatorProfile) -> bool:
    """Brands that never fit this creator, whatever their reply record."""
    from services.thanos.niches import _for_you_should_skip_brand
    card = {**brand, "name": brand.get("brand_name")}
    try:
        if _for_you_should_skip_brand(card, prof.interests, prof.scrape or None):
            return True
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] fit rules skipped: {err}")
    try:
        from services.polly_prefs import brand_blocked
        if brand_blocked(card, prof.notes):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _fit(brand: Dict[str, Any], prof: CreatorProfile) -> Dict[str, Any]:
    if prof.creator_id <= 0:
        return {}  # anonymous niche ranking (public connectors): no profile to fit against
    try:
        from services.fit_score_calculator import score_brand_for_creator
        card = {**brand, "name": brand.get("brand_name")}
        return score_brand_for_creator(prof.fit_profile or {}, card, interest_niches=prof.interests) or {}
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] fit score skipped: {err}")
        return {}


def _region_ok(brand: Dict[str, Any], prof: CreatorProfile) -> Optional[bool]:
    if not prof.country:
        return None
    ships = str(brand.get("ships_to") or "").strip()
    regions = brand.get("regions")
    if not ships and not regions:
        return None
    try:
        from services.creator_country import tokens_codes
        tokens = [t for t in str(ships).replace(";", ",").split(",") if t.strip()] if ships else []
        if isinstance(regions, (list, tuple)):
            tokens += [str(r) for r in regions]
        elif isinstance(regions, str) and regions.strip():
            tokens += [t for t in regions.split(",") if t.strip()]
        if not tokens:
            return None
        if any(str(t).strip().lower() in ("worldwide", "global", "international", "all") for t in tokens):
            return True
        return any(prof.country in tokens_codes(t) for t in tokens)
    except Exception:  # noqa: BLE001
        return None


def _reason(brand: Dict[str, Any], score: M.Score, ev: M.Evidence, band_ev: M.Evidence, prof: CreatorProfile) -> str:
    if score.reasons.get("recruiting"):
        return "Recruiting creators right now"
    heard = int(band_ev.positive + band_ev.selected)
    if heard:
        return f"{heard} creator{'s' if heard != 1 else ''} your size heard back"
    heard = int(ev.positive + ev.selected)
    if heard:
        return f"{heard} creator{'s' if heard != 1 else ''} heard back"
    if ev.kit_opens:
        return f"Opened {int(ev.kit_opens)} creator kit{'s' if ev.kit_opens != 1 else ''}"
    if score.reasons.get("gifting"):
        return "Gifts creators and runs creator content"
    if M.reach_tier(brand.get("min_followers"), prof.followers) == 0 and prof.followers:
        return "Works with creators your size"
    return f"Fits your {brand.get('category') or 'niche'} content"


def _card(brand: Dict[str, Any], score: M.Score, fit: Dict[str, Any], level: int, reason: str) -> Dict[str, Any]:
    """A card every surface can read (For You / Polly / Autopilot shapes)."""
    name = brand.get("brand_name")
    email = (brand.get("contact_email") or "").strip()
    return {
        "id": int(brand["id"]),
        "slug": brand.get("slug"),
        "name": name,
        "brand_name": name,
        "logo": brand.get("logo_url"),
        "logo_url": brand.get("logo_url"),
        "cover_image_url": brand.get("cover_image_url"),
        "description": brand.get("description"),
        "category": brand.get("category"),
        "website": brand.get("website"),
        "hero_product": brand.get("hero_product"),
        "target_audience": brand.get("target_audience"),
        "min_followers": brand.get("min_followers"),
        "max_followers": brand.get("max_followers"),
        "micro_friendly": brand.get("micro_friendly"),
        "price_point": brand.get("price_point"),
        "avg_product_value": brand.get("avg_product_value"),
        "regions": brand.get("regions"),
        "brand_niches": brand.get("niches"),
        "application_form_url": brand.get("application_form_url"),
        "has_application_form": brand.get("has_application_form"),
        "has_email_contact": bool(email),
        "gifting_signal": brand.get("gifting_signal"),
        "gifting_confirmed": bool(brand.get("gifting_confirmed_at")),
        "ugc_style": bool(brand.get("ugc_style")),
        "response_rate": brand.get("response_rate"),
        "roster_hunger": brand.get("roster_hunger") or 0,
        "roster_is_open": brand.get("roster_is_open") or 0,
        "roster_fill_count": brand.get("roster_fill_count") or 0,
        "roster_fill_target": brand.get("roster_fill_target") or 0,
        "roster_slot_limit": brand.get("roster_slot_limit") or 0,
        "roster_spotlighted": brand.get("roster_spotlighted") or 0,
        "match_score": int(fit.get("overall_score") or 0) if fit else 0,
        "fit_tier": fit.get("tier") if fit else None,
        "reply_chance": round(score.p, 4),
        "reply_band": score.band,
        "reply_reason": reason,
        "thanos_level": level,
        "thanos_lane": "followup" if level == 5 else ("core" if level == 0 else ("related" if level < 3 else "wide")),
        "source": "thanos",
    }


def _mix(cards: List[Dict[str, Any]], max_share: float = 0.5) -> List[Dict[str, Any]]:
    """Keep the order, but no category takes more than half the first slots while others are waiting."""
    if len(cards) < 4:
        return cards
    out: List[Dict[str, Any]] = []
    waiting = list(cards)
    counts: Dict[str, int] = {}
    while waiting:
        limit = max(2, int((len(out) + 1) * max_share + 0.5))
        pick = next((c for c in waiting if counts.get(str(c.get("category") or "").lower(), 0) < limit), waiting[0])
        waiting.remove(pick)
        key = str(pick.get("category") or "").lower()
        counts[key] = counts.get(key, 0) + 1
        out.append(pick)
    return out


def rank(conn, creator_id: int, surface: str = "polly", n: int = 6, *,
         exclude_ids: Sequence[int] = (), notes: Optional[Dict] = None, scrape: Optional[Dict] = None,
         categories: Optional[Sequence[str]] = None, only_ids: Optional[Sequence[int]] = None,
         max_level: int = 5, explore: Optional[bool] = None, profile: Optional[CreatorProfile] = None) -> Ranking:
    """The brands this creator should see or pitch next, best first. Never raises; an empty Ranking only when
    the directory has nothing pitchable left for them at any level."""
    cfg = SURFACES.get(surface, SURFACES["polly"])
    explore = cfg.get("explore", True) if explore is None else explore
    try:
        prof = profile or load_profile(conn, creator_id, notes=notes, scrape=scrape)
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] profile failed creator={creator_id}: {err}")
        prof = CreatorProfile(creator_id=int(creator_id))
    stats = S.load(conn)
    out = Ranking(profile=prof)

    skip = {int(i) for i in exclude_ids or [] if str(i).isdigit()}
    if only_ids is None:  # scoring named brands never hides them
        skip |= set(prof.pitched_ids)
        if not cfg.get("include_saved"):
            skip |= prof.saved_ids
    core = [str(c).lower() for c in (categories or prof.categories or [])]
    near = neighbours(core)
    proven = [bid for bid, ev in stats.brand.items() if ev.positive + ev.selected > 0]
    cap = int(prof.followers * M.STRETCH) if prof.followers else 0
    seed = _seed(prof.creator_id, surface)

    ladder = [
        (0, dict(categories=bool(core), reach_cap=True, only_ids=False, fresh=False), core),
        (1, dict(categories=True, reach_cap=True, only_ids=False, fresh=False), near),
        (2, dict(categories=True, reach_cap=False, only_ids=False, fresh=False), core + near),
        (3, dict(categories=False, reach_cap=False, only_ids=True, fresh=False), None),
        (4, dict(categories=False, reach_cap=False, only_ids=False, fresh=True), None),
    ]
    if only_ids is not None:
        ladder = [(0, dict(categories=False, reach_cap=False, only_ids=True, fresh=False), None)]

    picked: Dict[int, Dict[str, Any]] = {}
    for level, shape, cats in ladder:
        if level > max_level or len(picked) >= n:
            break
        if shape["categories"] and not cats:
            continue
        ids = list(only_ids) if only_ids is not None else (proven if level == 3 else [])
        if shape["only_ids"] and not ids:
            continue
        rows = _fetch(conn, candidates_sql(cfg["require"], **shape), {
            "skip": list(skip | set(picked)) or [0], "excluded": prof.excluded_categories or [_NONE],
            "cats": cats or [_NONE], "cap": cap, "ids": [int(i) for i in ids] or [0],
            "seed": seed, "limit": PER_LEVEL,
        })
        out.considered += len(rows)
        lane = "core" if level == 0 else ("related" if level in (1, 2) else "wide")
        floor = MIN_FIT if level <= 2 else WIDE_MIN_FIT
        scored = []
        for brand in rows:
            bid = int(brand["id"])
            if bid in picked or _blocked(brand, prof):
                continue
            ev = stats.for_brand(bid)
            if M.is_cold(ev) and surface != "search":
                continue
            fit = _fit(brand, prof)
            fit_score = fit.get("overall_score") if fit else None
            if fit.get("deal_breaker") and only_ids is None:
                continue
            if fit_score is not None and fit_score < floor and only_ids is None:
                continue
            try:
                from services.audience_fit import audience_mismatch, too_big_to_reply
                card = {**brand, "name": brand.get("brand_name")}
                if audience_mismatch(card, prof.scrape or None, prof.interests):
                    continue
                too_big = too_big_to_reply(card, {**(prof.scrape or {}), "followers_count": prof.followers})
            except Exception:  # noqa: BLE001
                too_big = False
            band_ev = stats.for_brand_band(bid, prof.band)
            facts = M.Facts(
                min_followers=brand.get("min_followers"), email=brand.get("contact_email") or "",
                name=brand.get("brand_name") or "",
                gifting_proof=bool(brand.get("gifting_confirmed_at") or brand.get("ugc_style")),
                recruiting=bool(brand.get("roster_is_open")) and (
                    not prof.roster_categories
                    or str(brand.get("category") or "").lower() in prof.roster_categories),
                verified_contact=bool(brand.get("verified_contact")), fit=fit_score, lane=lane,
                region_ok=_region_ok(brand, prof), too_big=too_big)
            sc = M.score(facts, prof.followers, stats.total,
                         stats.for_category_band(brand.get("category"), prof.band), ev, band_ev,
                         explore_seed=f"{seed}:{bid}" if explore else None)
            scored.append((sc.rank_value, _card(brand, sc, fit, level, _reason(brand, sc, ev, band_ev, prof))))
        scored.sort(key=lambda x: -x[0])
        for _, card in scored:
            if len(picked) >= n:
                break
            picked[card["id"]] = card
            out.level = max(out.level, level)

    if len(picked) < n and max_level >= 5 and prof.repitch_ids and only_ids is None:
        rows = _fetch(conn, candidates_sql(cfg["require"], categories=False, reach_cap=False, only_ids=True,
                                           fresh=False), {
            "skip": list(set(picked)) or [0], "excluded": prof.excluded_categories or [_NONE], "cats": [_NONE],
            "cap": 0, "ids": list(prof.repitch_ids), "seed": seed, "limit": n,
        })
        for brand in rows:
            if len(picked) >= n:
                break
            bid = int(brand["id"])
            ev = stats.for_brand(bid)
            sc = M.score(M.Facts(email=brand.get("contact_email") or "", name=brand.get("brand_name") or "",
                                 lane="wide"), prof.followers, stats.total,
                         stats.for_category_band(brand.get("category"), prof.band), ev, None)
            picked[bid] = _card(brand, sc, {}, 5, "Pitched a while ago, worth a follow-up")
            out.level = 5

    out.brands = _mix(list(picked.values()))[:n]
    out.note = LEVEL_NOTES.get(out.level, "")
    return out


def rank_for_niche(conn, category: str, n: int = 5, country: str = "", surface: str = "public") -> Ranking:
    """No creator account (public AI-assistant connectors): the same ranking for a niche, reply evidence first."""
    cat = str(category or "").strip().lower()
    from services.thanos.niches import excluded_categories_for
    prof = CreatorProfile(creator_id=0, interests=[cat], categories=[cat] if cat else [],
                          excluded_categories=excluded_categories_for([cat]), country=str(country or "").upper())
    return rank(conn, 0, surface=surface, n=n, profile=prof, max_level=1, explore=False)


def score_brands(conn, creator_id: int, brand_ids: Sequence[int], surface: str = "search",
                 notes: Optional[Dict] = None) -> Ranking:
    """Score specific brands (a brand the creator named, saved targets) without the ladder."""
    return rank(conn, creator_id, surface=surface, n=max(len(brand_ids), 1), only_ids=list(brand_ids),
                notes=notes, explore=False)


def ranking_digest(ranking: Ranking) -> str:
    """Stable fingerprint of a ranking (for caches)."""
    return hashlib.sha1(",".join(str(i) for i in ranking.ids()).encode()).hexdigest()[:12]
