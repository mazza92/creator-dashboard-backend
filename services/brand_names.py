"""Brand names as people say them: no legal suffixes ("Recharge Co., Ltd." -> "Recharge")."""
from __future__ import annotations

import re
from typing import Any, Dict

_SUFFIX = re.compile(
    r"(?:[\s,]+(?:co\.?,?\s*ltd\.?|co\.?|ltd\.?|limited|inc\.?|incorporated|llc\.?|l\.l\.c\.|corp\.?|corporation|"
    r"gmbh|s\.?a\.?s?\.?|s\.?r\.?l\.?|pty\.?\s*ltd\.?|plc|b\.?v\.?|ag|oy|ab))+\.?\s*$",
    re.I,
)
_REGION = re.compile(r"\s*\((?:us|usa|uk|eu|ca|au|global|official)\)\s*$", re.I)


def clean_brand_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        return ""
    out = _REGION.sub("", name).strip()
    stripped = _SUFFIX.sub("", out)
    if stripped != out:
        out = stripped.strip(" ,.-")
    return out or name


def clean_names_in_pitch(pitch: Dict[str, Any], raw_name: Any) -> Dict[str, Any]:
    """Use the clean name in the pitch too ('Hi Recharge Co., Ltd. team' -> 'Hi Recharge team')."""
    raw = str(raw_name or "").strip()
    clean = clean_brand_name(raw)
    if not pitch or not raw or clean == raw:
        return pitch
    out = dict(pitch)
    for key in ("subject", "body"):
        if isinstance(out.get(key), str):
            out[key] = out[key].replace(raw, clean)
    if out.get("brand_name") == raw:
        out["brand_name"] = clean
    return out
