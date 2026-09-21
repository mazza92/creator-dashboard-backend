"""Turn messy scanner briefs into a consistent creator-facing listing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PLACEHOLDER_BRAND_RE = re.compile(
    r"(?i)^(unknown(\s+brand)?|n/?a|none|tbd|brand|ugc(\s+creators?)?|"
    r"welcome(\s+ugc)?(\s+creators?)?|content\s+creators?|hiring|"
    r"campaign|poster|client)$"
)
_WELCOME_NOISE_RE = re.compile(r"(?i)\s*[-–:|]\s*welcome!?\s*$")
_HEADLINE_TAIL_RE = re.compile(
    r"(?i)\s*[-–:|]\s*("
    r"affiliate(s| program( overview)?)?|"
    r"what we'?(?:re|r) looking for|"
    r"why partner with\b.*|"
    r"program overview|"
    r"overview|"
    r"welcome!?|"
    r"what to expect|"
    r"about (us|the brand)|"
    r"how it works|"
    r"join\b.*|"
    r"ugc community|"
    r"community|"
    r"mega seeding|"
    r"seeding|"
    r"campaign"
    r")\s*$"
)
_CHROME_HEADLINE_RE = re.compile(
    r"(?i)^(what to expect|what we'?(?:re|r) looking for|about( us| the brand)?|"
    r"the (role|opportunity|brief)|how it works|overview|requirements|"
    r"deliverables|who we are)$"
)
_SLOGAN_RE = re.compile(
    r"(?i)\b(wants to|what to expect|builds on activities|already love|"
    r"new kid on the block|we'?re on a mission|daily habit)\b"
)
_JOB_TITLE_RE = re.compile(
    r"(?i)\b(spokesperson|presenter|videographer|on-camera|looking for|"
    r"needed|ugc ad|content creator)\b"
)
_APPLY_RE = re.compile(r"(?i)\n*apply here:\s*\S+")
_PAY_LINE_RE = re.compile(r"(?i)\n*pay:\s*.+")
_INLINE_PAY_RE = re.compile(
    r"(?i)\$[\d,]+(?:\s*[-–—]\s*\$?[\d,]+)?(?:\s*[kK])?"
    r"(?:\s*·\s*)?(?:\s*paid(?:\s*\+?\s*gift)?)?"
)
_PAID_GIFT_RE = re.compile(r"(?i)\bpaid\s*\+?\s*gift\b|\bpaid\+gift\b")
_TRAILING_PAY_RE = re.compile(
    r"(?i)(?:^|\s)\$[\d,]+(?:\s*[-–—]\s*\$?[\d,]+)?(?:\s*[kK])?(?:\s*·\s*paid)?\s*$"
)
_PAY_RANGE_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*([kK])?(?:\s*[-–—]\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([kK])?)?"
)
_LABELED_PAY_RE = re.compile(
    r"(?i)(?:hourly(?:\s+rate)?|fixed[- ]price|budget|compensation|paying|rate|pay(?:\s*range)?)"
    r"\s*[:=]?\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([kK])?"
    r"(?:\s*[-–—to]+\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([kK])?)?"
)
_USD_WORD_PAY_RE = re.compile(
    r"(?i)(?:(?:usd|us\$)\s*([\d,]+(?:\.\d+)?)\s*([kK])?"
    r"(?:\s*[-–—to]+\s*(?:usd|us\$)?\s*([\d,]+(?:\.\d+)?)\s*([kK])?)?"
    r"|([\d,]+(?:\.\d+)?)\s*([kK])?(?:\s*[-–—to]+\s*([\d,]+(?:\.\d+)?)\s*([kK])?)?\s*(?:usd|dollars?)\b)"
)
_HOURLY_HINT_RE = re.compile(r"(?i)(?:/h(?:r|our)s?|\bper hour\b|\bhourly\b)")
_FEATURING_RE = re.compile(
    r"(?i)\b(?:featuring|for|with)\s+"
    r"([A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*){0,2})\s+"
    r"(?:products?|pet\s+food|food|treats?|brand)\b"
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def is_placeholder_brand(name: Optional[str]) -> bool:
    raw = re.sub(r"\s+", " ", str(name or "")).strip()
    if not raw or len(raw) < 2:
        return True
    compact = re.sub(r"[^a-z]+", " ", raw.lower()).strip()
    if _PLACEHOLDER_BRAND_RE.match(compact):
        return True
    if compact.startswith("welcome ") and "ugc" in compact:
        return True
    return False


def looks_like_slogan(text: Optional[str]) -> bool:
    """True for marketing catchphrases, not ordinary 2-sentence briefs."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return False
    return bool(_SLOGAN_RE.search(raw) or _CHROME_HEADLINE_RE.match(raw))


def looks_like_title_sentence(text: Optional[str]) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if looks_like_slogan(raw):
        return True
    words = raw.split()
    return bool(raw.endswith((".", "!", "?")) and len(words) > 10)


def looks_like_job_title(text: Optional[str]) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if looks_like_slogan(raw):
        return True
    words = raw.split()
    if len(words) >= 8:
        return True
    if "/" in raw and len(raw) > 36:
        return True
    if _JOB_TITLE_RE.search(raw) and len(words) >= 4:
        return True
    return False


def short_brand_from_text(text: Optional[str]) -> Optional[str]:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    match = re.match(
        r"^([A-Z0-9][A-Za-z0-9.&'’-]{1,28})(?:\s+(?:wants|is|helps|makes|will)\b)",
        raw,
    )
    if match:
        name = match.group(1).strip(" -–|")
        if not is_placeholder_brand(name) and not looks_like_job_title(name):
            return name
    return None


def shorten_job_headline(text: Optional[str], brand: Optional[str] = None) -> str:
    raw = clean_headline(text, brand)
    if not raw or looks_like_slogan(raw) or _CHROME_HEADLINE_RE.match(raw):
        return ""
    if "/" in raw:
        left = raw.split("/", 1)[0].strip(" -–|")
        if 3 <= len(left) <= 52:
            raw = left
    raw = re.sub(r"(?i)\s+for\s+.{12,}$", "", raw).strip(" -–|")
    words = raw.split()
    if len(words) > 6:
        raw = " ".join(words[:6])
    return raw[:52]


def _title_case_headline(text: str) -> str:
    bits = []
    for word in text.split():
        if word.isupper() and len(word) <= 4:
            bits.append(word)
        elif word[:1].isdigit() or word.lower() in {"ugc", "grwm", "pr", "tiktok", "reels"}:
            key = word.lower()
            bits.append({"tiktok": "TikTok", "ugc": "UGC", "grwm": "GRWM", "reels": "Reels"}.get(key, word))
        else:
            bits.append(word[:1].upper() + word[1:] if word.islower() else word)
    return " ".join(bits)


def clean_headline(text: Optional[str], brand: Optional[str] = None) -> str:
    raw = _WELCOME_NOISE_RE.sub("", str(text or "")).strip()
    raw = _HEADLINE_TAIL_RE.sub("", raw).strip()
    raw = re.sub(r"(?i)^ugc\s+", "", raw)
    raw = re.sub(r"(?i)\bcontent creation\b", "UGC", raw)
    raw = re.sub(r"(?i)\s+[-–]\s+welcome!?$", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" -–|:.")
    if brand:
        raw = re.sub(rf"(?i)^{re.escape(brand)}\s*[-–:|]\s*", "", raw).strip(" -–|:")
        if raw.lower() == brand.lower():
            raw = ""
    if is_placeholder_brand(raw) or _CHROME_HEADLINE_RE.match(raw) or looks_like_slogan(raw):
        return ""
    if brand and raw.lower() == str(brand).lower():
        return ""
    if len(raw) > 52:
        raw = raw[:49].rsplit(" ", 1)[0].rstrip()
    return _title_case_headline(raw) if raw else ""


def extract_named_brand(*chunks: Optional[str]) -> Optional[str]:
    blob = " ".join(str(part or "") for part in chunks)
    match = _FEATURING_RE.search(blob)
    if not match:
        return None
    name = re.sub(r"\s+", " ", match.group(1)).strip(" .,")
    if is_placeholder_brand(name) or len(name) < 3:
        return None
    return name


def _parse_money(num: str, suffix: Optional[str]) -> Optional[str]:
    raw = (num or "").replace(",", "").strip()
    if not raw:
        return None
    if (suffix or "").lower() == "k":
        try:
            val = float(raw)
        except ValueError:
            return f"${raw}K"
        as_int = int(val * 1000) if val < 1000 else int(val)
        if as_int >= 1000 and as_int % 1000 == 0 and val < 1000:
            return f"${as_int:,}"
        return f"${raw.rstrip('0').rstrip('.') if '.' in raw else raw}K"
    try:
        if "." in raw:
            val = float(raw)
            return f"${val:,.0f}" if val >= 100 else f"${val:g}"
        return f"${int(raw):,}"
    except ValueError:
        return f"${num}"


def pay_has_amount(label: Optional[str]) -> bool:
    return bool(re.search(r"\$\s*\d", str(label or "")))


def prefer_amount_pay(*labels: Optional[str]) -> Optional[str]:
    """Keep a real $ amount over a generic 'Paid' flag."""
    cleaned = [re.sub(r"\s+", " ", str(part or "")).strip() for part in labels]
    cleaned = [part for part in cleaned if part]
    for part in cleaned:
        if pay_has_amount(part):
            return part
    for part in cleaned:
        if part.lower() not in {"paid", "n/a", "na", "tbd", "none"}:
            return part
    for part in cleaned:
        if part.lower() == "paid":
            return "Paid"
    return None


def _pay_from_match(match: re.Match) -> tuple[Optional[str], Optional[str]]:
    groups = match.groups()
    for i in range(0, len(groups), 4):
        chunk = groups[i:i + 4]
        if not chunk or not chunk[0]:
            continue
        low = _parse_money(chunk[0], chunk[1] if len(chunk) > 1 else None)
        high = None
        if len(chunk) > 2 and chunk[2]:
            high = _parse_money(chunk[2], chunk[3] if len(chunk) > 3 else None)
        return low, high
    return None, None


def _best_pay_from_matches(matches) -> Optional[str]:
    best = None
    for match in matches:
        low, high = _pay_from_match(match)
        if low and high and low != high:
            label = f"{low}–{high}"
            if best is None or "–" not in (best or ""):
                best = label
        elif low and best is None:
            best = low
    return best


def format_pay_label(pr_value_usd=None, *texts: Optional[str]) -> Optional[str]:
    blob = " ".join(str(part or "") for part in texts)
    best = _best_pay_from_matches(_PAY_RANGE_RE.finditer(blob))
    if not best:
        best = _best_pay_from_matches(_LABELED_PAY_RE.finditer(blob))
    if not best:
        best = _best_pay_from_matches(_USD_WORD_PAY_RE.finditer(blob))
    if best and _HOURLY_HINT_RE.search(blob) and not re.search(r"(?i)/hr", best):
        best = f"{best}/hr"
    if best:
        return best
    try:
        amount = int(float(str(pr_value_usd).replace(",", "")))
    except (TypeError, ValueError):
        amount = None
    if amount:
        label = f"${amount:,}"
        if _HOURLY_HINT_RE.search(blob):
            label = f"{label}/hr"
        return label
    if re.search(r"(?i)\bpaid\b", blob):
        return "Paid"
    return None


def clean_listing_text(text: Optional[str]) -> str:
    desc = _EMAIL_RE.sub("", str(text or ""))
    desc = _APPLY_RE.sub("", desc)
    desc = _PAY_LINE_RE.sub("", desc)
    desc = re.sub(r"(?i)\n*requirements:\s*", "\n", desc)
    desc = re.sub(r"[ \t]+\n", "\n", desc)
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    desc = _TRAILING_PAY_RE.sub("", desc).strip(" \n·-")
    return desc


def _strip_repeated_brand(text: str, brand: Optional[str]) -> str:
    out = text or ""
    if brand:
        out = re.sub(rf"(?i)\b{re.escape(brand)}\b", " ", out)
        short = re.sub(r"(?i)\b(the|co\.?|inc\.?|llc)\b", " ", brand).strip()
        if short and len(short) >= 3 and short.lower() != brand.lower():
            out = re.sub(rf"(?i)\b{re.escape(short)}\b", " ", out)
    return re.sub(r"\s+", " ", out).strip(" ·-|,")


def heuristic_summary(text: Optional[str], brand: Optional[str] = None) -> str:
    desc = re.sub(r"\s+", " ", clean_listing_text(text)).strip()
    desc = _INLINE_PAY_RE.sub(" ", desc)
    desc = _PAID_GIFT_RE.sub(" ", desc)
    desc = _TRAILING_PAY_RE.sub("", desc)
    desc = _strip_repeated_brand(desc, brand)
    desc = re.sub(r"\s+", " ", desc).strip(" ·-")
    if not desc:
        return ""
    parts = [part.strip() for part in _SENTENCE_RE.split(desc) if part.strip()]
    kept = []
    for part in parts:
        if re.match(r"^[a-z]", part):
            continue
        if re.match(r"(?i)^(join|apply|click|sign up|learn more|read more)\b", part):
            continue
        if looks_like_slogan(part) or _CHROME_HEADLINE_RE.match(part):
            continue
        kept.append(part)
        if len(kept) == 2:
            break
    kept = [part for part in kept if len(part) >= 24] or kept[:1]
    out = " ".join(kept).strip()
    if len(out) > 220:
        out = out[:217].rsplit(" ", 1)[0].rstrip() + "…"
    return out


def heuristic_deliverable(text: Optional[str]) -> str:
    raw = str(text or "")
    bits = []
    length = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*(?:seconds?|secs?|s)\b", raw, re.I)
    if length:
        bits.append(f"{length.group(1)}–{length.group(2)}s")
    if re.search(r"9\s*[:x]\s*16|vertical", raw, re.I):
        bits.append("9:16")
    if re.search(r"(?i)get[- ]ready[- ]with[- ]me|\bgrwm\b", raw):
        bits.append("GRWM")
    if re.search(r"(?i)\bshot on (?:your )?phone\b|\biphone\b|\bphone video\b", raw):
        bits.append("phone video")
    platform = None
    if re.search(r"(?i)\btiktok\b", raw):
        platform = "TikTok"
    elif re.search(r"(?i)\breels?\b|instagram", raw):
        platform = "Reels"
    elif re.search(r"(?i)\byoutube\b", raw):
        platform = "YouTube"
    if platform:
        bits.append(platform)
    if not bits:
        return ""
    # Keep order: length, format, style, capture, platform
    return ", ".join(bits)


def listing_source_hash(card: Dict[str, Any]) -> str:
    raw = "|".join([
        str(card.get("brand_name") or ""),
        str(card.get("product_name") or ""),
        str(card.get("campaign_description") or card.get("blurb") or "")[:900],
        str(card.get("pr_value_usd") or card.get("pay_label") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def structure_gig_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """Fill headline / summary / pay so every card shares the same skeleton."""
    out = dict(card or {})
    source = out.get("raw_listing") or out.get("campaign_description") or out.get("blurb") or ""
    out["raw_listing"] = source
    raw_brand = (out.get("poster_name") or out.get("brand_name") or out.get("name") or "").strip()
    product = (out.get("product_name") or "").strip()
    unknown = is_placeholder_brand(raw_brand)
    if looks_like_slogan(raw_brand) or looks_like_title_sentence(raw_brand) or looks_like_job_title(raw_brand):
        if not product:
            product = raw_brand
        unknown = True
    brand = None if unknown else raw_brand
    if not brand:
        brand = short_brand_from_text(raw_brand) or extract_named_brand(product, source, raw_brand)
        unknown = not brand
    headline = shorten_job_headline(product, brand)
    if headline and brand and headline.lower() == brand.lower():
        headline = ""
    if not headline and not brand:
        headline = shorten_job_headline(heuristic_summary(source, brand)[:72], brand)
    if not headline and not brand:
        headline = "Paid UGC offer"
    summary = heuristic_summary(source, brand)
    if not summary:
        summary = (headline or brand or "Paid UGC offer").rstrip(".") + "."
    deliverable = heuristic_deliverable(f"{product} {source}")
    pay = prefer_amount_pay(
        format_pay_label(out.get("pr_value_usd"), out.get("pay_label"), source, product),
        out.get("pay_label"),
    )
    out["poster_name"] = raw_brand
    out["brand_unknown"] = bool(unknown)
    out["brand_name"] = brand or ""
    out["name"] = brand or headline
    out["headline"] = headline or brand or "Paid UGC offer"
    out["summary"] = summary
    out["deliverable"] = deliverable
    out["blurb"] = summary
    out["pay_label"] = pay
    out["listing_src"] = listing_source_hash({
        "brand_name": raw_brand,
        "product_name": product,
        "campaign_description": source,
        "pr_value_usd": out.get("pr_value_usd"),
        "pay_label": out.get("pay_label"),
    })
    return out


def _compact_digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def rewrite_is_grounded(rewrite: Dict[str, Any], source: str) -> bool:
    """Reject invented pay amounts or brands that never appear in the listing."""
    blob = str(source or "").lower()
    blob_digits = _compact_digits(blob)
    pay = str(rewrite.get("pay") or "")
    for chunk in re.findall(r"\d[\d,]*", pay):
        digits = chunk.replace(",", "")
        if not digits or digits == "16":
            continue
        if digits not in blob_digits and not re.search(rf"\b{re.escape(digits)}k\b", blob):
            return False
    brand = str(rewrite.get("brand") or "").strip()
    if brand and brand.lower() not in blob and not is_placeholder_brand(brand):
        # Allow minor punctuation differences
        compact = re.sub(r"[^a-z0-9]+", "", brand.lower())
        hay = re.sub(r"[^a-z0-9]+", "", blob)
        if compact and compact not in hay:
            return False
    return True


def apply_llm_rewrite(card: Dict[str, Any], rewrite: Dict[str, Any]) -> Dict[str, Any]:
    out = card if isinstance(card, dict) else {}
    source = " ".join([
        str(out.get("poster_name") or ""),
        str(out.get("brand_name") or ""),
        str(out.get("product_name") or ""),
        str(out.get("raw_listing") or out.get("campaign_description") or ""),
        str(out.get("pay_label") or ""),
    ])
    if not rewrite_is_grounded(rewrite, source):
        return out
    brand = str(rewrite.get("brand") or "").strip()
    if is_placeholder_brand(brand) or looks_like_slogan(brand) or looks_like_title_sentence(brand) or looks_like_job_title(brand):
        brand = short_brand_from_text(brand) or ""
    if not brand:
        brand = short_brand_from_text(
            " ".join([str(rewrite.get("brand") or ""), str(out.get("poster_name") or ""), source])
        ) or (out.get("brand_name") if not looks_like_slogan(out.get("brand_name")) else "")
    headline = shorten_job_headline(rewrite.get("headline"), brand)
    if not headline:
        headline = shorten_job_headline(out.get("product_name"), brand) or out.get("headline")
    if headline and (_CHROME_HEADLINE_RE.match(str(headline)) or looks_like_slogan(headline)):
        headline = ""
    summary = re.sub(r"\s+", " ", str(rewrite.get("summary") or "")).strip()
    summary = _EMAIL_RE.sub("", summary)
    summary = re.sub(r"(?i)\bapply here\b.*", "", summary).strip()
    summary_parts = [part.strip() for part in _SENTENCE_RE.split(summary) if part.strip()]
    summary_parts = [
        part for part in summary_parts
        if not looks_like_slogan(part) and not _CHROME_HEADLINE_RE.match(part)
    ]
    summary = " ".join(summary_parts[:2]).strip() or heuristic_summary(source, brand)
    if len(summary) > 220:
        summary = summary[:217].rsplit(" ", 1)[0].rstrip() + "…"
    deliverable = re.sub(r"\s+", " ", str(rewrite.get("deliverable") or "")).strip()[:80]
    pay = prefer_amount_pay(str(rewrite.get("pay") or "").strip(), out.get("pay_label"), format_pay_label(out.get("pr_value_usd"), source))
    if brand:
        out["brand_name"] = brand
        out["brand_unknown"] = False
        out["name"] = brand
    elif headline:
        out["name"] = headline
        out["brand_unknown"] = True
        out["brand_name"] = ""
    display = headline or brand or "Paid UGC offer"
    if looks_like_slogan(display) or _CHROME_HEADLINE_RE.match(str(display)):
        display = brand or headline or "Paid UGC offer"
    out["headline"] = display
    if not out.get("name"):
        out["name"] = display
    if summary:
        out["summary"] = summary
        out["blurb"] = summary
    if deliverable:
        out["deliverable"] = deliverable
    if pay:
        out["pay_label"] = pay
    return out


_LISTING_SYS = """You rewrite paid UGC job listings for creators.
Return JSON only: {"listings":[{"id":"","brand":"","headline":"","summary":"","deliverable":"","pay":""}]}
Rules:
- Use only facts in each listing. Never invent a brand, rate, platform, city, or deliverable.
- brand: the company the content is FOR. Empty if unknown or it is a job-board poster (Unknown brand, Welcome UGC Creators, Freelancer, AspireIQ, LinkedIn).
- headline: 3–6 words for the job itself (e.g. "Cat litter UGC ad", "Senior travel spokesperson"). Never a slogan, never a mission statement, never page chrome ("What To Expect", "Affiliate Program Overview", "What We're Looking For").
- Never use a full sentence as brand or headline. If the listing starts with "UNRULY wants to…", brand is UNRULY and headline is the content type.
- If the buyer field is a long Upwork/Freelancer job title, that is the headline, not the brand.
- summary: 1–2 complete sentences, capital letter first. No URLs, emails, Apply here, Welcome, dollar amounts, or paid+gift (pay is a separate field). Do not start mid-sentence (and/for/to).
- deliverable: compact phrase if present (length, aspect, style, platform). Else empty.
- pay: if a $ amount or range is in the listing, pay MUST be that amount (e.g. $150–$300, $25/hr, $500). Never replace a known amount with Paid. Use Paid only when the listing is paid but has no number.
- If a name like Redbarn appears as the product brand, that is `brand`, not the poster."""


def _parse_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).rstrip("`").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def llm_rewrite_listings(cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One Gemini pass for a drop of cards. Empty dict on skip/failure."""
    if not cards:
        return {}
    try:
        from services.polly import get_gemini_key, get_polly_model, llm_disabled
    except Exception:
        return {}
    if llm_disabled():
        return {}
    api_key = get_gemini_key()
    if not api_key:
        return {}
    payload_rows = []
    for card in cards:
        payload_rows.append({
            "id": str(card.get("id") or ""),
            "posted_as": card.get("product_name") or card.get("headline") or "",
            "buyer": card.get("brand_name") or "",
            "source": card.get("source_label") or card.get("source_platform") or "",
            "location": card.get("location") or "",
            "pay": card.get("pay_label") or "",
            "text": clean_listing_text(
                card.get("raw_listing") or card.get("campaign_description") or card.get("blurb") or ""
            )[:1200],
        })
    user_prompt = (
        "Rewrite these listings. Keep facts. JSON only.\n"
        + json.dumps({"listings": payload_rows}, ensure_ascii=True)
    )
    body = {
        "systemInstruction": {"parts": [{"text": _LISTING_SYS}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 700,
            "responseMimeType": "application/json",
        },
    }
    try:
        import requests
        model = get_polly_model() or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        resp = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            timeout=8,
        )
        if resp.status_code >= 400:
            print(f"[Polly] gig listing LLM HTTP {resp.status_code}")
            return {}
        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        parsed = _parse_json_object(text)
        rows = parsed.get("listings") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            return {}
        out = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("id") or "").strip()
            if key:
                out[key] = row
        return out
    except Exception as err:
        print(f"[Polly] gig listing LLM skipped: {err}")
        return {}


_BRIEF_COL_READY = False
_MEMO: Dict[str, Dict[str, Any]] = {}


def _db():
    from opportunities_routes import get_db_connection
    from psycopg2.extras import RealDictCursor
    return get_db_connection(), RealDictCursor


def _ensure_brief_column(conn, cursor) -> bool:
    global _BRIEF_COL_READY
    if _BRIEF_COL_READY:
        return True
    try:
        prev = getattr(conn, "autocommit", False)
        conn.autocommit = True
        try:
            cursor.execute(
                "ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS listing_brief JSONB"
            )
        finally:
            try:
                conn.autocommit = prev
            except Exception:
                pass
        _BRIEF_COL_READY = True
        return True
    except Exception as err:
        print(f"[Polly] listing_brief column skipped: {err}")
        _BRIEF_COL_READY = False
        return False


def ensure_listing_brief_column(conn, cursor) -> bool:
    return _ensure_brief_column(conn, cursor)


def load_listing_briefs(ids: List[Any]) -> Dict[Any, Dict[str, Any]]:
    clean_ids = []
    for raw in ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return {}
    try:
        conn, cursor_factory = _db()
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            if not _ensure_brief_column(conn, cursor):
                return {}
            cursor.execute(
                "SELECT id, listing_brief FROM opportunities WHERE id = ANY(%s)",
                (clean_ids,),
            )
            out = {}
            for row in cursor.fetchall() or []:
                brief = row.get("listing_brief")
                if isinstance(brief, str):
                    try:
                        brief = json.loads(brief)
                    except Exception:
                        brief = None
                if isinstance(brief, dict):
                    out[row["id"]] = brief
            return out
        finally:
            cursor.close()
            conn.close()
    except Exception as err:
        global _BRIEF_COL_READY
        _BRIEF_COL_READY = False
        print(f"[Polly] listing_brief load skipped: {err}")
        return {}


def save_listing_brief(opp_id, brief: Dict[str, Any]) -> None:
    src = str((brief or {}).get("src") or "")
    if src:
        _MEMO[src] = dict(brief)
    try:
        kid = int(opp_id)
    except (TypeError, ValueError):
        return
    try:
        conn, cursor_factory = _db()
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            if not _ensure_brief_column(conn, cursor):
                return
            cursor.execute(
                "UPDATE opportunities SET listing_brief = %s WHERE id = %s",
                (json.dumps(brief), kid),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception as err:
        print(f"[Polly] listing_brief save skipped: {err}")


def _looks_messy(card: Dict[str, Any]) -> bool:
    summary = str(card.get("summary") or "").strip()
    headline = str(card.get("headline") or "").strip()
    brand = str(card.get("brand_name") or "").strip()
    product = str(card.get("product_name") or "").strip()
    if summary[:1].islower():
        return True
    if re.search(r"(?i)\$|\bpaid\s*\+|\bpaid\+gift\b", summary):
        return True
    if re.search(
        r"(?i)affiliate program|what we.?re looking for|why partner|welcome!",
        f"{headline} {product}",
    ):
        return True
    if looks_like_slogan(headline) or _CHROME_HEADLINE_RE.match(headline):
        return True
    if looks_like_slogan(brand) or looks_like_job_title(brand):
        return True
    if len(summary) < 40:
        return True
    return False


def _needs_llm_pass(card: Dict[str, Any]) -> bool:
    poster = card.get("poster_name") or ""
    raw = card.get("raw_listing") or ""
    if card.get("brand_unknown") or is_placeholder_brand(poster):
        return True
    if card.get("is_sourced") and _looks_messy(card):
        return True
    if len(raw) > 260:
        return True
    if re.search(r"(?i)\bwelcome!?\b", f"{poster} {card.get('product_name') or ''}"):
        return True
    return False


def polish_gig_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cached Gemini rewrite on messy cards. Heuristic stays if LLM misses."""
    structured = [card if card.get("headline") else structure_gig_card(card) for card in cards]
    ids = [c.get("id") for c in structured]
    cached = load_listing_briefs(ids)
    missing = []
    for card in structured:
        src = card.get("listing_src")
        memo = _MEMO.get(src) if src else None
        if memo:
            apply_llm_rewrite(card, memo)
            continue
        if card.get("listing_ready"):
            continue
        try:
            kid = int(card.get("id"))
        except (TypeError, ValueError):
            kid = None
        hit = cached.get(kid) if kid is not None else None
        if hit and hit.get("src") == card.get("listing_src"):
            apply_llm_rewrite(card, hit)
            if src:
                _MEMO[src] = hit
            continue
        if _needs_llm_pass(card):
            missing.append(card)
    if missing:
        print(f"[Polly] gig listing rewrite n={len(missing)}")
        rewrites = llm_rewrite_listings(missing)
        for card in missing:
            row = rewrites.get(str(card.get("id") or ""))
            if not row:
                continue
            apply_llm_rewrite(card, row)
            save_listing_brief(card.get("id"), {
                "id": str(card.get("id") or ""),
                "brand": card.get("brand_name") or "",
                "headline": card.get("headline") or "",
                "summary": card.get("summary") or "",
                "deliverable": card.get("deliverable") or "",
                "pay": card.get("pay_label") or "",
                "src": card.get("listing_src"),
            })
    return structured
