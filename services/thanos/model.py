"""Thanos scoring: the chance that a brand replies to THIS creator, from what Newcollab has seen.

Pure functions only (no DB), so every number here is unit-tested.

Why shrinkage (Nov 2026 data, 90 days): 8.5k pitches, ~105 brand replies from 52 brands, 613 kit opens from 236
brands, and only 1 brand pitched by 15+ creators. A brand's own record is almost always too thin to trust alone,
so each estimate leans on a wider group and moves toward the brand's real rate as its evidence grows:

    global rate -> category x creator-size rate -> brand rate -> brand x creator-size rate

"Success" counts what a creator wants from a pitch: a positive reply or question (1), a roster pick (1), a
"no thanks" reply (0.5: the inbox is read), and a kit open with no reply yet (0.25: the brand looked).

On top of that rate, evidence-based multipliers (reach, inbox type, gifting proof, fit, load this fortnight)
give the final reply chance. Thompson sampling then picks from the uncertainty so unknown brands get tried and
learned from instead of the same 26 proven brands being pitched forever.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

# --- outcome weights ------------------------------------------------------------------------------------------
W_POSITIVE = 1.0     # interested / question
W_SELECTED = 1.0     # picked from a gifted roster
W_REJECTED = 0.5     # replied "no": the inbox is read
W_KIT_OPEN = 0.25    # opened the creator's kit, no reply recorded

# --- shrinkage strengths (in "pitches worth" of the wider group) ----------------------------------------------
GLOBAL_PRIOR_RATE = 0.02
GLOBAL_PRIOR_N = 200
CATEGORY_STRENGTH = 150
BRAND_STRENGTH = 40
BRAND_BAND_STRENGTH = 15

# --- creator size bands ---------------------------------------------------------------------------------------
BANDS = ((1_000, "nano"), (10_000, "micro"), (50_000, "mid"), (250_000, "macro"))
TOP_BAND = "mega"

# --- reach ---------------------------------------------------------------------------------------------------
IN_REACH = 1.25      # a brand asking for 2,000 followers is in reach of a creator with 1,600
STRETCH = 5.0        # up to 5x their size is worth a try

# --- cold: enough evidence that the inbox doesn't answer ------------------------------------------------------
COLD_MIN_SENDS = 15
COLD_MAX_RATE = 0.03

# --- reply-chance bands shown to creators, relative to the platform's own rate ---------------------------------
HIGH_X = 1.5          # 1.5x the typical brand
MEDIUM_X = 0.75
NEW_BELOW_SENDS = 3

FATIGUE_HALF = 8      # 8 creators pitching a brand in 14 days halves its chance for the next one

_CREATOR_INBOX = re.compile(r"^(pr|press|media|influencers?|creators?|collabs?|collaborations?|partnerships?|"
                            r"partners?|marketing|social|brand|brands|ambassadors?|affiliates?|ugc|community)"
                            r"([._-].*)?$")
_SERVICE_INBOX = re.compile(r"^(support|customerservice|customer[._-]?service|customercare|customer[._-]?care|"
                            r"care|help|service|cs|orders?|returns|shipping|billing|noreply|no-reply)([._-].*)?$")
_GENERIC_INBOX = re.compile(r"^(info|hello|hi|hey|contact|contactus|contact[._-]?us|enquiries|inquiries|enquiry|"
                            r"inquiry|team|mail|office|general|admin|sales)([._-].*)?$")
_COMPANY_NAME = re.compile(r"(?i)(\b(co\.?,?\s*ltd|trading co|holdings?|group plc|gmbh|power station|"
                           r"shopping cent(re|er)|shopping mall|outlet village)\b|\blimited\.?\s*$)")


@dataclass
class Evidence:
    """What Newcollab has seen for one brand (or one brand x size band, or one category x band)."""
    sends: float = 0.0          # distinct creators who pitched
    positive: float = 0.0       # distinct creators with an interested / question reply
    rejected: float = 0.0       # distinct creators told "no"
    selected: float = 0.0       # distinct creators picked from a roster
    kit_opens: float = 0.0      # distinct creators whose kit the brand opened (and no reply yet)
    bounces: float = 0.0
    recent_load: float = 0.0    # distinct creators who pitched in the last 14 days

    def success(self) -> float:
        return (W_POSITIVE * self.positive + W_SELECTED * self.selected + W_REJECTED * self.rejected
                + W_KIT_OPEN * self.kit_opens)

    def trials(self) -> float:
        # A roster pick is a success with no pitch row behind it; count it as a trial too so rates stay <= 1.
        return max(self.sends + self.selected, self.success())

    def add(self, other: "Evidence") -> "Evidence":
        return Evidence(*(getattr(self, f) + getattr(other, f) for f in self.__dataclass_fields__))


@dataclass
class Facts:
    """Per-brand facts from the directory row."""
    min_followers: Optional[int] = None
    email: str = ""
    name: str = ""
    gifting_proof: bool = False     # confirmed gifting or creator-style (UGC) ads running
    recruiting: bool = False        # live roster / open PR list taking creators now
    verified_contact: bool = False
    fit: Optional[float] = None     # 0-100 profile fit (services.fit_score_calculator)
    lane: str = "core"              # core | related | wide
    region_ok: Optional[bool] = None
    too_big: bool = False           # household name that ignores small creators


@dataclass
class Score:
    p: float                        # estimated reply chance 0..1
    rank_value: float               # what the list is ordered by (p, or a Thompson draw)
    band: str                       # high | medium | low | new
    confidence: float               # pitches-worth of evidence behind p
    cold: bool = False
    reasons: Dict[str, float] = field(default_factory=dict)


def size_band(followers: Optional[int]) -> str:
    try:
        n = int(followers or 0)
    except (TypeError, ValueError):
        n = 0
    for upper, name in BANDS:
        if n < upper:
            return name
    return TOP_BAND


def shrink(success: float, trials: float, prior_rate: float, strength: float) -> float:
    """Beta-binomial posterior mean: the group's rate until the brand's own trials outweigh `strength`."""
    trials = max(float(trials), 0.0)
    success = min(max(float(success), 0.0), trials) if trials else 0.0
    return (success + prior_rate * strength) / (trials + strength)


def global_rate(global_ev: Evidence) -> float:
    return shrink(global_ev.success(), global_ev.trials(), GLOBAL_PRIOR_RATE, GLOBAL_PRIOR_N)


def base_rate(global_ev: Evidence, category_band: Optional[Evidence], brand: Optional[Evidence],
              brand_band: Optional[Evidence]) -> tuple:
    """(rate, confidence): global -> category x band -> brand -> brand x band."""
    g = shrink(global_ev.success(), global_ev.trials(), GLOBAL_PRIOR_RATE, GLOBAL_PRIOR_N)
    cb = category_band or Evidence()
    c = shrink(cb.success(), cb.trials(), g, CATEGORY_STRENGTH)
    b = brand or Evidence()
    rate = shrink(b.success(), b.trials(), c, BRAND_STRENGTH)
    confidence = b.trials()
    bb = brand_band or Evidence()
    if bb.trials():
        rate = shrink(bb.success(), bb.trials(), rate, BRAND_BAND_STRENGTH)
    return rate, confidence


def is_cold(brand: Optional[Evidence]) -> bool:
    """15+ creators pitched, under 3% heard anything back, never picked anyone. Kit opens don't rescue it."""
    if not brand or brand.selected > 0 or brand.sends < COLD_MIN_SENDS:
        return False
    replies = brand.positive + brand.rejected
    return replies / max(brand.sends, 1.0) < COLD_MAX_RATE


def inbox_rank(email: str) -> int:
    """0 = PR / partnerships inbox, 1 = a named person or other, 2 = catch-all or customer service.
    Nov 2026, 30 days of sends: info@/hello@ 0.4% replies vs about 1.2% for PR inboxes and named people."""
    local = str(email or "").split("@")[0].strip().lower()
    if _CREATOR_INBOX.match(local):
        return 0
    if _SERVICE_INBOX.match(local) or _GENERIC_INBOX.match(local):
        return 2
    return 1


def looks_like_company(name: str) -> bool:
    return bool(_COMPANY_NAME.search(str(name or "")))


def reach_tier(min_followers: Optional[int], followers: Optional[int]) -> int:
    """0 in reach, 1 stretch, 2 out of reach. Unknown creator size counts as in reach."""
    try:
        need = int(min_followers or 0)
        have = int(followers or 0)
    except (TypeError, ValueError):
        return 0
    if have <= 0 or need <= have * IN_REACH:
        return 0
    return 1 if need <= have * STRETCH else 2


REACH_MULT = {0: 1.0, 1: 0.6, 2: 0.25}
INBOX_MULT = {0: 1.25, 1: 1.0, 2: 0.4}
LANE_MULT = {"core": 1.0, "related": 0.8, "wide": 0.55}


def multipliers(facts: Facts, followers: Optional[int], ev: Optional[Evidence]) -> Dict[str, float]:
    out = {
        "reach": REACH_MULT[reach_tier(facts.min_followers, followers)],
        "inbox": INBOX_MULT[inbox_rank(facts.email)] if facts.email else 1.0,
        "lane": LANE_MULT.get(facts.lane, 0.55),
    }
    if facts.recruiting:
        out["recruiting"] = 2.0
    if facts.gifting_proof:
        out["gifting"] = 1.4
    if facts.verified_contact:
        out["verified"] = 1.1
    if looks_like_company(facts.name):
        out["company"] = 0.5
    if facts.too_big:
        out["too_big"] = 0.3
    if facts.region_ok is False:
        out["region"] = 0.5
    if facts.fit is not None:
        fit = min(max(float(facts.fit), 0.0), 100.0)
        out["fit"] = 0.6 + 0.8 * fit / 100.0          # fit 50 -> 1.0, 100 -> 1.4, 0 -> 0.6
    load = (ev.recent_load if ev else 0.0) or 0.0
    if load:
        out["fatigue"] = 1.0 / (1.0 + load / FATIGUE_HALF)
    return out


def band_for(p: float, confidence: float, baseline: float = GLOBAL_PRIOR_RATE) -> str:
    high = baseline * HIGH_X
    if confidence < NEW_BELOW_SENDS and p < high:
        return "new"
    if p >= high:
        return "high"
    return "medium" if p >= baseline * MEDIUM_X else "low"


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))


def thompson(p: float, confidence: float, seed: str) -> float:
    """A draw from Beta(mean p, strength = evidence + brand prior). Seeded, so a list is stable for a day."""
    strength = max(confidence, 0.0) + BRAND_STRENGTH
    a = max(p * strength, 1e-3)
    b = max((1.0 - p) * strength, 1e-3)
    return _rng(seed).betavariate(a, b)


def score(facts: Facts, followers: Optional[int], global_ev: Evidence, category_band: Optional[Evidence],
          brand: Optional[Evidence], brand_band: Optional[Evidence], explore_seed: Optional[str] = None) -> Score:
    rate, confidence = base_rate(global_ev, category_band, brand, brand_band)
    mults = multipliers(facts, followers, brand)
    p = rate
    for m in mults.values():
        p *= m
    p = min(max(p, 0.0), 0.95)
    rank_value = p
    if explore_seed:
        # Draw on the base rate's uncertainty, then apply the same multipliers.
        draw = thompson(rate, confidence, explore_seed)
        rank_value = min(draw * math.prod(mults.values()), 0.95)
    return Score(p=p, rank_value=rank_value, band=band_for(p, confidence, global_rate(global_ev)),
                 confidence=confidence,
                 cold=is_cold(brand), reasons=mults)
