"""Live Gemini eval for Polly's orchestrator: does the model pick the right tool and brand?

Skipped unless POLLY_LIVE_EVAL=1 and GEMINI_API_KEY are set. Tools are stubbed, so only
the model's first decision is graded; nothing touches the database.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.polly_brain as brain  # noqa: E402
from services.polly_turn_state import TurnState  # noqa: E402

LIVE = os.getenv("POLLY_LIVE_EVAL") == "1" and bool(os.getenv("GEMINI_API_KEY"))

DRAFT_HISTORY = [
    {"role": "user", "content": "pitch BALLBOYZ"},
    {"role": "assistant", "content": "Here's your BALLBOYZ pitch.",
     "pitch": {"brand_name": "BALLBOYZ", "brand_id": 9, "body": "Hi BALLBOYZ team..."}},
]
CARDS_HISTORY = [
    {"role": "user", "content": "find me brands"},
    {"role": "assistant", "content": "Here are three that fit.",
     "brands": [{"id": 4, "name": "NordVPN"}, {"id": 47, "name": "Secretlab"}, {"id": 9, "name": "BALLBOYZ"}]},
]

BRAND_IDS = {"4": "nordvpn", "9": "ballboyz", "47": "secretlab"}

# (user message, history, notes, expected first tool, expected brand argument or None)
CASES = [
    ("do you have Gucci?", [], {}, "search_directory", "gucci"),
    ("is Rare Beauty on here", [], {}, "search_directory", "rare beauty"),
    ("find me brands to pitch", [], {}, "suggest_brands", None),
    ("any paid gigs?", [], {}, "find_paid_gigs", None),
    ("yes Secretlab", CARDS_HISTORY, {}, "draft_pitch", "secretlab"),
    ("pitch the second one", CARDS_HISTORY, {}, "draft_pitch", "secretlab"),
    ("I sent it", DRAFT_HISTORY, {"pending_pitch": {"id": 9, "name": "BALLBOYZ"}}, "log_pitch_sent", None),
    ("done, emailed them", DRAFT_HISTORY, {"pending_pitch": {"id": 9, "name": "BALLBOYZ"}}, "log_pitch_sent", None),
    ("BALLBOYZ replied, they want to send me product!", [], {}, "log_brand_update", "ballboyz"),
    ("NordVPN said no thanks", [], {}, "log_brand_update", "nordvpn"),
    ("Lyon, France", DRAFT_HISTORY, {"pending_pitch": {"id": 9, "name": "BALLBOYZ"}}, "save_profile_facts", None),
    ("what should I charge for a UGC video?", [], {}, "get_coaching_facts", None),
    ("how is my media kit looking?", [], {}, "get_coaching_facts", None),
    ("write a follow up to BALLBOYZ", [], {}, "draft_pitch", "ballboyz"),
]


def _state(text, history, notes):
    return TurnState(
        creator_id=7, creator={"id": 7}, first="Ava", scrape={"primary_niche": "tech"},
        kit={"published": True}, profile_context="Primary niche: tech, gaming",
        coach_moves=[], balance={"remaining": 2, "limit": 3, "is_unlimited": False},
        notes=dict(notes), messages=history + [{"role": "user", "content": text}],
        stored_messages=history, suggested=[], tracker_ctx={}, early_fu={}, career=None,
        user_text=text, chip={}, conn=MagicMock(closed=0),
    )


class _Stop(Exception):
    pass


@unittest.skipUnless(LIVE, "set POLLY_LIVE_EVAL=1 and GEMINI_API_KEY to run")
class PollyLiveEval(unittest.TestCase):
    def test_first_tool_choice(self):
        misses = []
        for text, history, notes, want_tool, want_brand in CASES:
            picked = {}

            def fake_run_tool(state, name, args):
                picked.update(name=name, args=args or {})
                raise _Stop()

            state = _state(text, history, notes)
            with patch.object(brain, "run_tool", fake_run_tool), \
                    patch.object(brain.polly, "stream_sink", return_value=None):
                try:
                    brain.run_turn(state)
                except _Stop:
                    pass
            got_tool = picked.get("name")
            args = picked.get("args") or {}
            got_brand = str(args.get("brand_name") or args.get("name") or "").lower()
            got_brand += " " + BRAND_IDS.get(str(args.get("brand_id")), "")
            ok = got_tool == want_tool and (want_brand is None or want_brand in got_brand)
            print(f"{'OK ' if ok else 'MISS'} {text!r}: {got_tool} {args}")
            if not ok:
                misses.append((text, want_tool, want_brand, got_tool, args))
        accuracy = 1 - len(misses) / len(CASES)
        print(f"Polly tool accuracy: {accuracy:.0%}")
        self.assertGreaterEqual(accuracy, 0.85, misses)


if __name__ == "__main__":
    unittest.main()
