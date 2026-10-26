from datetime import datetime, timedelta, timezone

from services import insight_agent as agent

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def test_plan_uses_model_queries_and_cleans_them():
    write = lambda prompt, opts: {"queries": ["#Desk Setup Tour", "desk setup tour", "honest earbuds review", "x" * 90,
                                              "gadget unboxing", "phone case haul"], "label": "tech creators"}
    out = agent.plan("Hooks that work in tech", "tech", "Belkin", write=write)
    assert out["ai"] and out["queries"] == ["desk setup tour", "honest earbuds review", "gadget unboxing", "phone case haul"]
    assert out["label"] == "tech creators"


def test_plan_falls_back_to_bank_when_model_fails():
    def boom(prompt, opts):
        raise ValueError("no key")
    out = agent.plan("q", "tech", write=boom, fallback=["tech review", "tech haul", "tech review"])
    assert not out["ai"] and out["queries"] == ["tech review", "tech haul"]


def test_clean_answer_keeps_only_pool_videos_and_handles():
    raw = {"headline": "What works in tech", "summary": "Unboxings win — **3x** views.",
           "sections": [{"kind": "hooks", "title": "Hooks", "intro": "Openers",
                         "items": [{"title": "“the camera sees an unboxing”", "text": "POV contrast",
                                    "videos": ["1", "999"], "handle": ""},
                                   {"title": "@mikeunboxingtech", "text": "Reviews", "videos": ["2"]},
                                   {"title": "@ghost", "text": "Not in pool", "handle": "ghost"}]},
                        {"kind": "weird", "title": "Other", "items": [{"title": "x", "text": "y"}]},
                        {"kind": "creators", "title": "Empty", "items": []}],
           "shortlist": "Start with **@mike**.", "followups": [{"label": "More", "prompt": "Find more"}, {"label": "no prompt"}]}
    out = agent.clean_answer(raw, {"1", "2"}, {"mikeunboxingtech"})
    hooks = out["sections"][0]
    assert hooks["items"][0]["videos"] == ["1"]
    assert hooks["items"][1]["handle"] == "mikeunboxingtech"
    assert hooks["items"][2]["handle"] == ""
    assert out["sections"][1]["kind"] == "takeaways" and len(out["sections"]) == 2
    assert "—" not in out["summary"] and len(out["followups"]) == 1


def test_clean_answer_rejects_empty():
    assert agent.clean_answer({"summary": "hi", "sections": []}, set(), set()) is None
    assert agent.clean_answer("nope", set(), set()) is None


def test_synthesize_sends_pool_and_validates():
    seen = {}
    def write(prompt, opts):
        seen["prompt"] = prompt
        return {"summary": "Ok", "sections": [{"kind": "creators", "title": "C", "items": [{"title": "@a", "videos": ["v1"]}]}]}
    vids = [{"tiktok_id": f"v{i}", "creator_handle": "a", "creator_followers": 900, "views": 100 - i, "likes": 3,
             "created_at": NOW - timedelta(days=2), "caption": "my desk setup tour"} for i in range(3)]
    out = agent.synthesize("q", "tech", "Belkin", {"videos": vids, "creators": {"a": {"handle": "a", "followers": 900}}},
                           write=write, now=NOW)
    assert "v0 | @a | 900" in seen["prompt"] and "my desk setup tour" in seen["prompt"]
    assert out["sections"][0]["items"][0]["handle"] == "a"


def test_synthesize_needs_videos_and_survives_model_errors():
    assert agent.synthesize("q", "tech", "", {"videos": []}) is None
    def boom(prompt, opts):
        raise RuntimeError("down")
    vids = [{"tiktok_id": str(i), "creator_handle": "a"} for i in range(3)]
    assert agent.synthesize("q", "tech", "", {"videos": vids, "creators": {}}, write=boom) is None
