"""Where a creator lives: one ISO country code instead of broad regions.

Creators pick a country at onboarding (or in Settings). We store:
- creators.country        ISO-3166 alpha-2 code of a country we serve, or 'OTHER'
- creators.country_other  free text when they picked "My country isn't listed"
- creators.regions        derived from the country (regions_for), so every matcher that still reads regions
                          keeps working. Same JSON-string format as before: '["UK"]'.
- users.country           the English name ("United Kingdom"), as the old CountryDropdown stored it. Login/IP
                          region gates read this column, so it is only written for countries we serve.

Country -> regions (the vocabulary creators.regions already holds and the one pr_brands.regions uses):
    US -> ["US"]          GB -> ["UK"]        CA -> ["Canada"]       AU -> ["Australia"]
    NZ -> ["NZ"]          IE, the other EU-27, NO, CH -> ["Europe"]  OTHER -> []
Why: brands write "US", "UK", "Canada", "Australia", "EU", "Worldwide". Ireland is not covered by a "UK"
shipping list, so it maps to Europe only (EU brands ship there). New Zealand is not Australia: "NZ" is a code
roster_fit / brand_shipping already understand, and "Australia" would make pitches say "shipping to Australia".
NO / CH are in Europe but not the EU, which is why the token is "Europe" (brand_shipping's europe group
includes them) rather than "EU". OTHER gets no regions: an empty list never matches a regional brand.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from services.db_ddl import ensure_columns

OTHER = "OTHER"
OTHER_MAX_LEN = 80
# Shown first in the picker; the rest are alphabetical by name.
PINNED = ("US", "GB", "CA", "AU")

EU_MEMBERS = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)

_NAMES = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "NZ": "New Zealand", "IE": "Ireland", "NO": "Norway", "CH": "Switzerland",
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "PL": "Poland",
    "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain",
    "SE": "Sweden",
}


def flag_for(code: Optional[str]) -> str:
    """'GB' -> the regional-indicator flag emoji. '' for anything that isn't two letters."""
    code = str(code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


# {code: (name, flag)} in picker order: pinned first, then alphabetical by name.
SERVED_COUNTRIES: Dict[str, Tuple[str, str]] = {
    code: (_NAMES[code], flag_for(code))
    for code in list(PINNED) + sorted((c for c in _NAMES if c not in PINNED), key=lambda c: _NAMES[c])
}

_REGIONS = {"US": ["US"], "GB": ["UK"], "CA": ["Canada"], "AU": ["Australia"], "NZ": ["NZ"]}
_REGIONS.update({code: ["Europe"] for code in EU_MEMBERS + ("NO", "CH")})

# Names and spellings seen on users.country (old CountryDropdown) and typed by people.
_ALIASES = {
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB",
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US", "america": "US",
    "republic of ireland": "IE", "the netherlands": "NL", "holland": "NL", "czech republic": "CZ",
    "deutschland": "DE", "espana": "ES", "españa": "ES", "sverige": "SE", "suisse": "CH",
    "schweiz": "CH", "norge": "NO", "osterreich": "AT", "österreich": "AT",
}
_BY_NAME = {name.lower(): code for code, (name, _flag) in SERVED_COUNTRIES.items()}
_BY_NAME.update(_ALIASES)


class CountryError(ValueError):
    """A country value we can't accept. str(err) is safe to show the user."""


def is_served(code: Optional[str]) -> bool:
    return str(code or "").upper() in SERVED_COUNTRIES


def normalize_code(value: Any) -> Optional[str]:
    """A served ISO code from a code, 'UK', or an English name ('United Kingdom'). 'OTHER' stays 'OTHER'.
    None for empty or anything we don't serve."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return None
    upper = text.upper()
    if upper == OTHER:
        return OTHER
    if upper == "UK":
        return "GB"
    if upper in SERVED_COUNTRIES:
        return upper
    return _BY_NAME.get(text.lower().strip("."))


def name_for(code: Optional[str]) -> str:
    entry = SERVED_COUNTRIES.get(str(code or "").upper())
    return entry[0] if entry else ""


def regions_for(code: Optional[str]) -> List[str]:
    """creators.regions tokens for a country code. [] for OTHER / unknown."""
    return list(_REGIONS.get(str(code or "").upper(), []))


def regions_json(code: Optional[str]) -> str:
    """regions_for(), in the JSON-string format creators.regions already holds."""
    return json.dumps(regions_for(code))


def clean_other(raw: Any) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    text = re.sub(r"[<>{}\[\]\\]", "", text)
    return text[:OTHER_MAX_LEN]


def validate(country: Any, country_other: Any = None, required: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """(code, other_text) for a request's country fields, or raise CountryError.

    (None, None) when no country was sent and it isn't required. A served code returns (code, None).
    'OTHER' needs the country name in country_other."""
    raw = str(country or "").strip()
    if not raw:
        if required:
            raise CountryError("Please choose your country")
        return None, None
    code = normalize_code(raw)
    if code == OTHER:
        other = clean_other(country_other)
        if not other:
            raise CountryError("Please tell us which country you're in")
        # They typed a country we do serve: take the real code.
        typed = normalize_code(other)
        if typed and typed != OTHER:
            return typed, None
        return OTHER, other
    if not code:
        raise CountryError(
            f"We don't support '{raw[:40]}' yet. Pick your country from the list, "
            "or choose \"My country isn't listed\"."
        )
    return code, None


def country_payload(code: Any, other: Any = None, legacy_name: Any = None) -> Optional[Dict[str, str]]:
    """{'code', 'name', 'flag'} for API responses. Falls back to a legacy users.country name; None if unknown."""
    norm = normalize_code(code)
    if norm == OTHER:
        return {"code": OTHER, "name": clean_other(other) or "Other", "flag": "🌐"}
    if not norm:
        norm = normalize_code(legacy_name)
        if norm == OTHER:
            norm = None
    if not norm:
        return None
    name, flag = SERVED_COUNTRIES[norm]
    return {"code": norm, "name": name, "flag": flag}


def response_fields(code: Any, other: Any = None, legacy_name: Any = None) -> Dict[str, Optional[str]]:
    """Flat fields for profile / search payloads: country_code, country_name, country_flag, country_other."""
    info = country_payload(code, other, legacy_name)
    return {
        "country_code": info["code"] if info else None,
        "country_name": info["name"] if info else None,
        "country_flag": info["flag"] if info else None,
        "country_other": (clean_other(other) or None) if (info and info["code"] == OTHER) else None,
    }


def display_country(value: Any) -> str:
    """For pitch copy ('shipping to …'): served code -> name, 'OTHER' -> '', anything else unchanged."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.upper() == OTHER:
        return ""
    if len(text) == 2 and text.upper() in SERVED_COUNTRIES:
        return name_for(text)
    return text


def _as_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x or "").strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x or "").strip()]
        except ValueError:
            pass
    return [p.strip() for p in text.split(",") if p.strip()]


_UNAMBIGUOUS_REGIONS = {"us": "US", "uk": "GB", "canada": "CA"}


def country_from_regions(regions: Any) -> Optional[str]:
    """Only a single unambiguous region names a country: ["US"], ["UK"], ["Canada"]."""
    items = _as_list(regions)
    if len(items) != 1:
        return None
    return _UNAMBIGUOUS_REGIONS.get(items[0].lower())


def country_matches(code: str, creator_country: Any, regions: Any = None, legacy_name: Any = None) -> bool:
    """Search filter: creators.country when set; otherwise users.country, then regions (the old signal)."""
    want = str(code or "").upper()
    have = normalize_code(creator_country)
    if have:
        return have == want
    legacy = normalize_code(legacy_name)
    if legacy and legacy != OTHER:
        return legacy == want
    from_regions = country_from_regions(regions)
    if from_regions:
        return from_regions == want
    # Last resort for creators who only ticked broad regions: does that region contain the country?
    return any(want in tokens_codes(r) for r in _as_list(regions))


def tokens_codes(region: Any) -> set:
    """Country codes a region token covers ('Europe' -> EU + GB/NO/CH, 'EU' -> EU-27, 'UK' -> {'GB'})."""
    low = str(region or "").strip().lower()
    if low in ("eu", "european union"):
        return set(EU_MEMBERS)
    if low == "europe":
        return set(EU_MEMBERS) | {"GB", "NO", "CH"}
    if low in ("anz", "oceania"):
        return {"AU", "NZ"}
    code = normalize_code(low)
    return {code} if code and code != OTHER else set()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_schema_ready = False

SCHEMA_DDL = """
    ALTER TABLE creators
      ADD COLUMN IF NOT EXISTS country VARCHAR(5),
      ADD COLUMN IF NOT EXISTS country_other TEXT
"""


def ensure_schema(cursor) -> None:
    """Idempotent, once per process. VARCHAR(5) so the 'OTHER' marker fits next to ISO alpha-2 codes."""
    global _schema_ready
    if _schema_ready:
        return
    ensure_columns(cursor, SCHEMA_DDL)
    _schema_ready = True


def save_country(cursor, user_id: Any, code: Optional[str], other: Optional[str] = None) -> bool:
    """Write a validated country for a user. True when a creators row was updated.

    users.country gets the English name for served countries (login/IP gates read it); for OTHER it is left
    alone so a creator outside our countries goes through the same region checks as before."""
    if not code:
        return False
    ensure_schema(cursor)
    if code != OTHER:
        cursor.execute("UPDATE users SET country = %s WHERE id = %s", (name_for(code), user_id))
    cursor.execute(
        "UPDATE creators SET country = %s, country_other = %s, regions = %s WHERE user_id = %s",
        (code, other if code == OTHER else None, regions_json(code), user_id),
    )
    return bool(getattr(cursor, "rowcount", 0) and cursor.rowcount > 0)



def legacy_names(code: str) -> List[str]:
    """Lower-cased users.country spellings that mean this code (for SQL fallbacks)."""
    code = str(code or "").upper()
    return sorted(name for name, c in _BY_NAME.items() if c == code) + [code.lower()]


def legacy_regions_json(code: str) -> Optional[str]:
    """The creators.regions value that unambiguously meant this country before country existed."""
    for token, c in _UNAMBIGUOUS_REGIONS.items():
        if c == str(code or "").upper():
            return json.dumps([{"us": "US", "uk": "UK", "canada": "Canada"}[token]])
    return None


# SQL for "creator is in country %(code)s": creators.country first, then users.country, then regions.
COUNTRY_FILTER_SQL = (
    "(UPPER(COALESCE(c.country, '')) = %s OR (NULLIF(c.country, '') IS NULL AND "
    "(LOWER(TRIM(COALESCE(u.country, ''))) = ANY(%s) OR c.regions::text = %s)))"
)


def country_filter_params(code: str) -> List[Any]:
    return [str(code).upper(), legacy_names(code), legacy_regions_json(code) or "__none__"]
