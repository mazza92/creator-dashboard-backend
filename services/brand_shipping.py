"""Where a brand ships, from pr_brands.regions, and whether that covers a creator's country.

regions is free text today ("US", "Worldwide", "EU", "AU", "United Kingdom" ...). Empty means we
don't know, which is never treated as a no: those brands rank lower and say "Shipping not confirmed".
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

WORLDWIDE = {"worldwide", "global", "international", "ww", "all countries"}

_COUNTRY = {
    "us": "US", "usa": "US", "united states": "US", "united states of america": "US", "america": "US",
    "uk": "GB", "gb": "GB", "united kingdom": "GB", "great britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "ca": "CA", "canada": "CA",
    "au": "AU", "australia": "AU",
    "nz": "NZ", "new zealand": "NZ",
    "ie": "IE", "ireland": "IE",
    "fr": "FR", "france": "FR", "de": "DE", "germany": "DE", "it": "IT", "italy": "IT",
    "es": "ES", "spain": "ES", "pt": "PT", "portugal": "PT", "nl": "NL", "netherlands": "NL",
    "be": "BE", "belgium": "BE", "fi": "FI", "finland": "FI", "se": "SE", "sweden": "SE",
    "dk": "DK", "denmark": "DK", "at": "AT", "austria": "AT", "pl": "PL", "poland": "PL",
    "ro": "RO", "romania": "RO", "gr": "GR", "greece": "GR", "cz": "CZ", "czech republic": "CZ",
    "czechia": "CZ", "sk": "SK", "slovakia": "SK", "hu": "HU", "hungary": "HU", "bg": "BG",
    "bulgaria": "BG", "hr": "HR", "croatia": "HR", "si": "SI", "slovenia": "SI", "lt": "LT",
    "lithuania": "LT", "lv": "LV", "latvia": "LV", "ee": "EE", "estonia": "EE", "lu": "LU",
    "luxembourg": "LU", "mt": "MT", "malta": "MT", "cy": "CY", "cyprus": "CY",
    "no": "NO", "norway": "NO", "ch": "CH", "switzerland": "CH", "al": "AL", "albania": "AL",
    "mx": "MX", "mexico": "MX", "br": "BR", "brazil": "BR", "in": "IN", "india": "IN",
    "sg": "SG", "singapore": "SG", "ae": "AE", "uae": "AE", "united arab emirates": "AE",
    "za": "ZA", "south africa": "ZA", "ph": "PH", "philippines": "PH", "jp": "JP", "japan": "JP",
    "kr": "KR", "south korea": "KR", "korea": "KR",
}
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
      "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
_GROUPS = {
    "eu": EU, "european union": EU,
    "europe": EU | {"GB", "NO", "CH", "AL"},
    "north america": {"US", "CA", "MX"},
    "asia": {"SG", "JP", "KR", "IN", "PH", "AE"},
    "oceania": {"AU", "NZ"}, "anz": {"AU", "NZ"},
}
_NAMES = {"US": "the US", "GB": "the UK", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand",
          "IE": "Ireland", "AE": "the UAE", "NL": "the Netherlands", "PH": "the Philippines"}


def country_code(value: Any) -> Optional[str]:
    """'Australia', 'AU', 'Lyon, France' -> ISO-ish code. None when unknown."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower().strip(".")
    if not text:
        return None
    if text in _COUNTRY:
        return _COUNTRY[text]
    last = text.split(",")[-1].strip()
    return _COUNTRY.get(last)


def country_name(code: Optional[str]) -> str:
    if not code:
        return ""
    if code in _NAMES:
        return _NAMES[code]
    for name, c in _COUNTRY.items():
        if c == code and len(name) > 3:
            return name.title()
    return code


def ships_to(regions: Any) -> Any:
    """'worldwide', a set of country codes, or None (unknown)."""
    items = regions if isinstance(regions, (list, tuple, set)) else []
    codes: set = set()
    for raw in items:
        key = re.sub(r"\s+", " ", str(raw or "")).strip().lower()
        if not key or key in ("{}", "[]"):
            continue
        if key in WORLDWIDE:
            return "worldwide"
        if key in _GROUPS:
            codes |= _GROUPS[key]
            continue
        code = country_code(key)
        if code:
            codes.add(code)
    return codes or None


def ships_to_country(regions: Any, country: Optional[str]) -> Optional[bool]:
    """True / False, or None when either side is unknown."""
    code = country_code(country) if country and len(str(country)) > 2 else (str(country).upper() if country else None)
    dest = ships_to(regions)
    if not code or dest is None:
        return None
    return dest == "worldwide" or code in dest


def creator_country(creator: Optional[Dict] = None, notes: Optional[Dict] = None,
                    scrape: Optional[Dict] = None) -> Optional[str]:
    creator, notes = creator or {}, notes or {}
    addr = creator.get("shipping_address") if isinstance(creator.get("shipping_address"), dict) else {}
    for val in (addr.get("country"), creator.get("country"), notes.get("location")):
        code = country_code(val)
        if code:
            return code
    return None


def load_regions(conn, brand_ids: Iterable[Any]) -> Dict[int, Any]:
    ids = sorted({int(b) for b in brand_ids if str(b or "").isdigit()})
    if not conn or not ids:
        return {}
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, regions FROM pr_brands WHERE id = ANY(%s)", (ids,))
        return {int(r[0]): r[1] for r in cur.fetchall()}
    except Exception as err:
        print(f"[shipping] regions lookup skipped: {err}")
        conn.rollback()
        return {}


def apply_shipping(brands: List[Dict[str, Any]], country: Optional[str],
                   regions: Dict[int, Any]) -> Dict[str, Any]:
    """Drop brands that don't ship to the creator, label unknown ones, confirmed ones first.

    {"brands": [...], "ships_to": "Australia", "confirmed": n, "unconfirmed": n, "dropped": n}"""
    if not country:
        return {"brands": brands, "ships_to": "", "confirmed": 0, "unconfirmed": 0, "dropped": 0}
    confirmed, unknown, dropped = [], [], 0
    for b in brands:
        bid = b.get("id") or b.get("brand_id")
        ok = ships_to_country(regions.get(int(bid)) if str(bid or "").isdigit() else b.get("regions"), country)
        if ok is False:
            dropped += 1
        elif ok is None:
            unknown.append({**b, "shipping_note": "Shipping not confirmed"})
        else:
            confirmed.append({**b, "ships_to_you": True})
    return {"brands": confirmed + unknown, "ships_to": country_name(country), "confirmed": len(confirmed),
            "unconfirmed": len(unknown), "dropped": dropped}
