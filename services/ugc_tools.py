"""Free UGC tools on newcollab.co/tools: hook, script and pitch email generators.

Public and unauthenticated, so: short validated inputs, a per-IP hourly limit and a global daily cap
(both counted in ugc_tool_runs), and a template fallback so the tool still answers when Gemini is
unavailable or the cap is hit. Output never invents stats, follower counts or results.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Callable, Dict, List, Optional

TOOLS = ("hooks", "script", "pitch")
PLATFORMS = {"tiktok": "TikTok", "instagram-reels": "Instagram Reels", "youtube-shorts": "YouTube Shorts",
             "amazon": "Amazon product video", "any": "TikTok and Reels"}
PER_IP_PER_HOUR = 12
DAILY_AI_CAP = 2000

_RULES = (
    "Never invent statistics, results, follower counts, prices, awards or medical claims. Never say a professional "
    "(vet, doctor, dermatologist, trainer) recommended it, and never promise health, weight or medical outcomes "
    "(no 'cures', 'fixes acne', 'goodbye indigestion'). No hashtags. "
    "Plain, natural English a creator would actually say on camera. Keep the product name exactly as given."
)

SYSTEMS = {
    "hooks": (
        "You write opening hooks (the first 3 seconds) for UGC videos that brands run as ads. "
        "Return JSON {\"hooks\": [10 strings]}. Each hook is one spoken line under 14 words, specific to the "
        "product and audience, and the 10 use different angles (problem, curiosity, comparison, POV, objection, "
        "result shown first, list, etc.). " + _RULES
    ),
    "script": (
        "You write a 30-second UGC ad script for a creator to film on a phone. Return JSON "
        "{\"beats\": [{\"time\": \"0-3s\", \"beat\": \"Hook\", \"line\": \"what the creator says\", "
        "\"shot\": \"what the camera shows\"}, ...5 beats: Hook, Problem, Product + demo, Proof, Call to action], "
        "\"alt_hooks\": [3 strings]}. Proof must be an honest personal observation phrased as something to fill in "
        "from real use (e.g. 'After [X days], [what you noticed]'), never an invented result. " + _RULES
    ),
    "pitch": (
        "You write a short cold pitch email from a UGC creator to a brand. Return JSON {\"subject\": \"...\", "
        "\"body\": \"...\"}. Body under 110 words: 'Hi [first name],', one line on who the creator is (their "
        "niche), ONE concrete video idea for the product with the opening hook in quotes, the line "
        "'Samples and rates: [portfolio link]', a short question offering three hook options, and "
        "'[your name]'. Keep the bracketed placeholders exactly. Do not claim the creator already uses the product. "
        + _RULES
    ),
}


def clean(value: Any, limit: int) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or ""))
    text = re.sub(r"[<>{}]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_input(tool: str, data: Dict[str, Any]) -> Dict[str, str]:
    platform = str(data.get("platform") or "any").strip().lower()
    out = {
        "product": clean(data.get("product"), 80),
        "niche": clean(data.get("niche"), 40).lower(),
        "audience": clean(data.get("audience"), 80),
        "benefit": clean(data.get("benefit"), 120),
        "brand": clean(data.get("brand"), 60),
        "platform": platform if platform in PLATFORMS else "any",
    }
    if not out["product"]:
        raise ValueError("Tell us the product (for example: vitamin C serum).")
    return out


# ------------------------------------------------------------------ template fallback
_HOOK_TEMPLATES = [
    "I did not expect {product} to make this much difference.",
    "Stop scrolling if {audience_lc} and nothing has worked yet.",
    "3 things I wish I knew before buying {product_a}.",
    "POV: you finally found {product_a} that actually fits your routine.",
    "I thought {product} was overhyped. Here is what happened.",
    "If {audience_lc}, you need to see this.",
    "Watch this before you buy another {niche_or} product.",
    "Day 7 of using {product} and I have notes.",
    "You are using {niche_or} products wrong. Try this instead.",
    "The {product} I keep reaching for, and why.",
    "Rating {product} honestly after a week.",
    "This is the {product} everyone keeps asking me about.",
]


def _ctx(inp: Dict[str, str]) -> Dict[str, str]:
    product = inp["product"]
    audience = inp["audience"] or (f"you love {inp['niche']}" if inp["niche"] else "you are picky about what you buy")
    return {
        "product": product,
        "product_a": product if product.lower().startswith(("a ", "an ", "the ")) else f"a {product}",
        "audience_lc": audience[0].lower() + audience[1:] if audience else "",
        "niche_or": inp["niche"] or "new",
    }


def template_output(tool: str, inp: Dict[str, str], rng: Optional[random.Random] = None) -> Dict[str, Any]:
    rng = rng or random.Random(f"{tool}:{inp['product']}:{inp['niche']}")
    ctx = _ctx(inp)
    hooks = [t.format(**ctx) for t in rng.sample(_HOOK_TEMPLATES, len(_HOOK_TEMPLATES))]
    if tool == "hooks":
        return {"hooks": hooks[:10]}
    benefit = inp["benefit"] or "[the main thing it does for you]"
    if tool == "script":
        return {
            "beats": [
                {"time": "0-3s", "beat": "Hook", "line": hooks[0], "shot": "Your face to camera, product in hand."},
                {"time": "3-8s", "beat": "Problem", "line": f"[The problem in your words, e.g. what was annoying before {ctx['product']}].", "shot": "Show the problem: the before, the mess, the frustration."},
                {"time": "8-20s", "beat": "Product + demo", "line": f"So I tried {ctx['product']}. It {benefit}.", "shot": "Close-ups: packaging, texture or detail, the product in use."},
                {"time": "20-26s", "beat": "Proof", "line": "After [X days], [what you honestly noticed].", "shot": "The result in the same light as the problem shot."},
                {"time": "26-30s", "beat": "Call to action", "line": "It's linked here if you want to try it.", "shot": "Product next to your face, point to the link area."},
            ],
            "alt_hooks": hooks[1:4],
        }
    brand = inp["brand"] or "[brand]"
    return {
        "subject": f"UGC idea for {ctx['product']}",
        "body": (
            f"Hi [first name],\n\nI'm a UGC creator who films {inp['niche'] or '[your niche]'} content. "
            f"I'd love to make a 20-second {PLATFORMS[inp['platform']]} video for {brand}'s {ctx['product']}, "
            f"opening with: \"{hooks[0]}\"\n\nSamples and rates: [portfolio link]\n\n"
            "Would you like three hook options for your next ad test?\n\n[your name]"
        ),
    }


# ------------------------------------------------------------------ AI
def _valid(tool: str, out: Dict[str, Any]) -> bool:
    if tool == "hooks":
        hooks = out.get("hooks")
        return isinstance(hooks, list) and len([h for h in hooks if isinstance(h, str) and h.strip()]) >= 6
    if tool == "script":
        beats = out.get("beats")
        return isinstance(beats, list) and len(beats) >= 4 and all(isinstance(b, dict) and b.get("line") for b in beats)
    return bool(str(out.get("subject") or "").strip()) and "[portfolio link]" in str(out.get("body") or "")


def gemini_creative(system: str, user: str, max_tokens: int = 900) -> Dict[str, Any]:
    try:
        from services.polly import get_gemini_key, get_polly_model, llm_disabled
        import requests
    except Exception:
        return {}
    if llm_disabled() or not get_gemini_key():
        return {}
    model = get_polly_model() or "gemini-2.5-flash"
    config = {"temperature": 0.9, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"}
    if "2.5" in model:
        config["thinkingConfig"] = {"thinkingBudget": 0}
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": config},
            headers={"Content-Type": "application/json", "x-goog-api-key": get_gemini_key()}, timeout=15)
        if resp.status_code >= 400:
            print(f"[ugc_tools] Gemini HTTP {resp.status_code}")
            return {}
        data = resp.json()
        try:
            from services.gemini_usage import record_gemini_usage
            record_gemini_usage("ugc_tools", model, data)
        except Exception:
            pass
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip() or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        print(f"[ugc_tools] Gemini failed: {exc}")
        return {}


def generate(tool: str, inp: Dict[str, str], use_ai: bool = True, llm: Callable = gemini_creative) -> Dict[str, Any]:
    if tool not in TOOLS:
        raise ValueError("unknown tool")
    if use_ai:
        brief = {k: v for k, v in inp.items() if v}
        brief["platform"] = PLATFORMS[inp["platform"]]
        out = llm(SYSTEMS[tool], json.dumps(brief)) or {}
        if _valid(tool, out):
            if tool == "hooks":
                out = {"hooks": [clean(h, 160) for h in out["hooks"] if isinstance(h, str) and h.strip()][:10]}
            if tool == "script":
                # The proof must come from the creator's real use, never from the model.
                for b in out["beats"]:
                    if "proof" in str(b.get("beat", "")).lower() and "[" not in str(b.get("line", "")):
                        b["line"] = "After [X days], [what you honestly noticed]."
            return {**out, "source": "ai"}
    return {**template_output(tool, inp), "source": "template"}


# ------------------------------------------------------------------ limits
def allowed(cursor, ip_hash: str) -> Dict[str, bool]:
    cursor.execute("""SELECT
                        (SELECT COUNT(*) FROM ugc_tool_runs WHERE ip_hash = %s AND created_at > NOW() - INTERVAL '1 hour'),
                        (SELECT COUNT(*) FROM ugc_tool_runs WHERE source = 'ai' AND created_at > NOW() - INTERVAL '1 day')""",
                   (ip_hash,))
    row = cursor.fetchone()
    per_ip, daily_ai = (row[0], row[1]) if not isinstance(row, dict) else tuple(row.values())
    return {"ok": int(per_ip) < PER_IP_PER_HOUR, "ai": int(daily_ai) < DAILY_AI_CAP}


def record(cursor, tool: str, ip_hash: str, inp: Dict[str, str], source: str) -> None:
    cursor.execute("INSERT INTO ugc_tool_runs (tool, ip_hash, niche, platform, source) VALUES (%s, %s, %s, %s, %s)",
                   (tool, ip_hash, inp.get("niche") or None, inp.get("platform") or None, source))
