"""Which brands a TikTok post shows gifting or paying a small creator. Pure rules, no I/O."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

MAX_CREATOR_FOLLOWERS = 50_000
WINDOW_DAYS = 90
WINDOW = timedelta(days=WINDOW_DAYS)

# Shops, marketplaces, apps and agencies: tagged a lot, but not the brand doing the gifting.
RETAILERS = {
    "yesstyle", "yesstyleinfluencers", "stylevana", "oliveyoung", "oliveyoungglobal", "sephora", "ulta",
    "ultabeauty", "amazon", "amazoninfluencer", "target", "walmart", "costco", "aldi", "boots", "superdrug",
    "cultbeauty", "lookfantastic", "iherb", "temu", "aliexpress", "nordstrom", "macys", "revolve", "asos",
    "tiktok", "tiktokshop", "tiktokcreators", "capcut", "shopltk", "ltk", "liketoknowit", "rewardstyle",
    "chimeable", "aspire", "grin", "upfluence", "influenster", "mavrck", "trybe", "collabstr", "newcollab",
    "dermstore", "sokoglam", "beautylish", "spacenk", "selfridges", "kohls", "walgreens", "cvs",
    "pickple", "gopicky", "picky", "billo", "insense", "joinbrands", "statusphere", "popularpays",
}
_COUNTRY_TAIL = ("usa", "us", "uk", "kr", "korea", "au", "ca", "eu", "official", "global", "store", "shop")
GENERIC = {
    "fyp", "foryou", "foryoupage", "viral", "trending", "skincare", "skincareroutine", "makeup", "beauty",
    "haircare", "fashion", "fitness", "ugc", "ugccreator", "ugccreators", "pr", "prpackage", "prunboxing",
    "gifted", "giftedby", "ad", "sponsored", "brandpartner", "unboxing", "haul", "smallcreator",
    "microinfluencer", "tiktokmademebuyit", "creator", "influencer", "review", "grwm", "routine", "kbeauty",
    "koreanskincare", "selfcare", "wellness", "small", "micro", "content", "brand", "brands", "partner",
    "ambassador", "tiktokpartner", "giveaway", "new", "my", "the", "this",
}
_SUFFIX_TOKENS = {"official", "global", "us", "usa", "uk", "au", "ca", "eu", "store", "shop", "hq", "inc",
                  "co", "brand", "team", "world", "international", "intl", "america", "na"}
_TAG_SUFFIX = re.compile(r"^(?P<brand>[a-z0-9]{3,40}?)(?:partner|partners|ambassador|ambassadors|creator|"
                         r"creators|ugc|pr|gifted|ad|collab|crew|squad|fam)$")

_GIFT = re.compile(r"(?i)#gifted\b|#gift(ed)?by|#pr(package|box|haul|unboxing|gift|list)?\b|\bpr (package|box|haul|"
                   r"unboxing|mail|list)\b|\bgifted\b|\bsent me\b|\bthank(s| you) .{0,40}\bfor (sending|gifting)\b|"
                   r"\bsent (by|from)\b|\bpr\b.{0,20}\b(from|by)\b|\bgifted by\b")
_PAID = re.compile(r"(?i)#ad\b|#sponsored\b|#paidpartnership\b|\bpaid partnership\b|#\w+partner\b|"
                   r"\bsponsored by\b|\bin partnership with\b|#\w+ambassador\b")
_AFFILIATE = re.compile(r"(?i)#tiktokshop\b|\btiktok shop\b|\borange cart\b|\bdiscount code\b|\bmy code\b|"
                        r"\b(influencer|promo|affiliate) code\b|\bcode [A-Z0-9]{4,}\b|#affiliate\b|\baffiliate\b")


def brand_key(raw: Any) -> str:
    """'@AXIS-Y Store US' / 'beautyofjoseon_official' -> 'axisy' / 'beautyofjoseon'."""
    text = str(raw or "").strip().lstrip("@").lower()
    parts = [p for p in re.split(r"[\s._\-]+", text) if p]
    while len(parts) > 1 and parts[-1] in _SUFFIX_TOKENS:
        parts.pop()
    return re.sub(r"[^a-z0-9]", "", "".join(parts))


def key_variants(key: str) -> List[str]:
    """'garnierusa' -> ['garnierusa', 'garnier']: run-together country/store tails, tried against the directory."""
    out = [key]
    cur = key
    for _ in range(2):
        tail = next((t for t in _COUNTRY_TAIL if cur.endswith(t) and len(cur) - len(t) >= 4), None)
        if not tail:
            break
        cur = cur[: -len(tail)]
        out.append(cur)
    return out


_NAME_TAIL = re.compile(r"(?i)(?:[\s._-]+(?:us|usa|uk|store|shop|official|global|international|korea|kr))+$")


def display_name(raw: Any) -> str:
    """'AXIS-Y Store US' -> 'AXIS-Y', 'UNOVE.global' -> 'UNOVE', 'GARNIER' -> 'Garnier'."""
    name = re.sub(r"\(.*?\)", "", str(raw or "")).strip()
    name = re.split(r"\s+[|–—-]\s+", name)[0].strip()
    name = _NAME_TAIL.sub("", name).strip(" ._-")
    if name.isupper() and len(re.sub(r"[^A-Z]", "", name)) > 5 and " " not in name:
        name = name.title()
    return name or str(raw or "").strip()


def _blocked(key: str, creator: str = "") -> bool:
    if not key or len(key) < 3:
        return True
    if key in RETAILERS or key in GENERIC:
        return True
    return bool(creator) and key == brand_key(creator)


def keep_video(video: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Small creator, posted inside WINDOW, English (or unknown language)."""
    now = now or datetime.now(timezone.utc)
    creator = video.get("creator") or {}
    followers = int(creator.get("followers") or 0)
    if followers <= 0 or followers > MAX_CREATOR_FOLLOWERS:
        return False
    when = video.get("created_at")
    if not isinstance(when, datetime) or now - when > WINDOW:
        return False
    lang = (video.get("language") or "").lower()
    return lang in ("", "en", "un")


def signal_types(video: Dict[str, Any], key: str = "") -> List[str]:
    text = " ".join([video.get("caption") or ""] + [f"#{h}" for h in video.get("hashtags") or []])
    out = []
    if _GIFT.search(text):
        out.append("gifted")
    paid = bool(_PAID.search(text))
    if key and any(h in (f"{key}partner", f"{key}ambassador") for h in video.get("hashtags") or []):
        paid = True
    if paid or (video.get("is_ad") and not out):
        out.append("paid")
    if _AFFILIATE.search(text):
        out.append("affiliate")
    return out


def candidates(video: Dict[str, Any]) -> List[Dict[str, str]]:
    """Brand candidates in one post: @mentions first, then branded hashtags (#olaypartner -> olay)."""
    creator = (video.get("creator") or {}).get("handle") or ""
    names = list(video.get("mention_names") or [])
    replied = {brand_key(m) for m in re.findall(r"(?i)replying to @([\w.\- ]{2,40}?)(?=[\s,.!?:]|$)",
                                                video.get("caption") or "")}
    out: Dict[str, Dict[str, str]] = {}
    for i, handle in enumerate(video.get("mentions") or []):
        key = brand_key(handle)
        name = names[i] if i < len(names) else handle
        if _blocked(key, creator) or key in replied or brand_key(name) in replied:
            continue
        out.setdefault(key, {"key": key, "handle": handle, "name": str(name).strip() or handle, "via": "mention"})
    for tag in video.get("hashtags") or []:
        m = _TAG_SUFFIX.match(tag)
        if not m:
            continue
        key = m.group("brand")
        if _blocked(key, creator) or key in out:
            continue
        out[key] = {"key": key, "handle": "", "name": key, "via": "hashtag"}
    return list(out.values())


def brand_signals(video: Dict[str, Any], directory_keys: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """[{key, handle, name, types}] for brands this post shows working with the creator.

    Hashtag-only candidates need a directory match or a mention of the same brand, so
    '#glowpartner'-style community tags don't become brands."""
    known = set(directory_keys or ())
    out = []
    for cand in candidates(video):
        if cand["via"] == "hashtag" and not any(k in known for k in key_variants(cand["key"])):
            continue
        types = signal_types(video, cand["key"])
        if types:
            out.append({**cand, "types": types})
    return out


NICHE_WORDS: Dict[str, List[str]] = {
    "skincare": ["skincare", "skin care", "serum", "moisturizer", "moisturiser", "cleanser", "spf", "sunscreen",
                 "toner", "retinol", "kbeauty", "k-beauty", "koreanskincare", "glass skin"],
    "makeup": ["makeup", "mascara", "lipstick", "lip gloss", "foundation", "concealer", "blush", "eyeshadow"],
    "haircare": ["haircare", "hair care", "shampoo", "conditioner", "hair oil", "hair mask", "curls"],
    "nails": ["nails", "nail polish", "press on", "press-on", "manicure"],
    "fragrance": ["fragrance", "perfume", "parfum", "cologne", "body mist"],
    "fashion": ["fashion", "outfit", "ootd", "dress", "clothing", "try on", "try-on", "jewelry", "jewellery"],
    "fitness": ["fitness", "gym", "workout", "activewear", "leggings", "protein", "pilates"],
    "food": ["food", "snack", "recipe", "coffee", "matcha", "drink", "tea", "chocolate"],
    "wellness": ["wellness", "supplement", "vitamins", "gut health", "collagen", "self care", "selfcare"],
    "home": ["home decor", "homedecor", "cleaning", "kitchen", "candle", "bedding", "organization"],
    "pet": ["dog", "cat", "puppy", "pet"],
    "baby": ["baby", "toddler", "mom life", "momlife", "postpartum", "kids"],
    "tech": ["tech", "techtok", "techgirl", "gadget", "gadgets", "phone case", "headphones", "earbuds", "desk setup",
             "desksetup", "gaming setup", "pcbuild", "pc build", "keyboard", "laptop", "iphone", "android",
             "monitor", "smart home", "charger", "speaker", "camera", "tablet", "smartwatch"],
}
NICHE_ALIASES = {"beauty": "makeup", "cosmetics": "makeup", "hair": "haircare", "skin": "skincare",
                 "k-beauty": "skincare", "kbeauty": "skincare", "perfume": "fragrance", "gym": "fitness",
                 "health": "wellness", "supplements": "wellness", "mom": "baby", "parenting": "baby",
                 "gaming": "tech", "jewelry": "fashion", "jewellery": "fashion", "activewear": "fitness",
                 "pets": "pet", "drinks": "food", "foodie": "food", "lifestyle": "home"}


def radar_niche(raw: Any) -> str:
    """Creator niche text ('Beauty & Skincare', 'tech, gaming') -> one radar niche key, or ''."""
    text = str(raw or "").lower()
    for key in NICHE_WORDS:
        if re.search(rf"\b{re.escape(key)}\b", text):
            return key
    for alias, key in NICHE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return key
    return ""


_NICHE_RES = {n: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")s?\b")
              for n, words in NICHE_WORDS.items()}


def video_niches(video: Dict[str, Any]) -> List[str]:
    """Niches a post is about: whole words in the caption, or hashtags that start with a niche word
    (#techtok, #skincareroutine), so 'carpet' isn't pet and 'team' isn't tea."""
    caption = (video.get("caption") or "").lower()
    tags = [str(h).lower() for h in video.get("hashtags") or []]
    out = []
    for n, words in NICHE_WORDS.items():
        joined = [w.replace(" ", "") for w in words]
        if _NICHE_RES[n].search(caption) or any(t.startswith(w) for t in tags for w in joined):
            out.append(n)
    return out


def post_niches(video: Dict[str, Any], query_niche: str) -> List[str]:
    """Niches to file a post's brands under. A search for one niche often returns posts about another
    (a 'tech pr' search finding a skincare haul), so the post's own words win when they disagree."""
    own = video_niches(video)
    if not video.get("query"):
        return own or ["general"]
    if own and query_niche not in own:
        return own
    return list(dict.fromkeys([query_niche] + own))
