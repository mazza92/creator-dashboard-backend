from services import competitors as comp


def test_parse_json_from_grounded_text():
    assert comp.parse_json('Sure:\n```json\n{"competitors": [{"name": "Topicals"}]}\n```') == {"competitors": [{"name": "Topicals"}]}
    assert comp.parse_json('noise {"a": 1} trailing') == {"a": 1}


def test_propose_drops_self_dupes_and_falls_back_to_plain():
    seen = []
    def write(prompt, opts):
        seen.append(opts["grounded"])
        if opts["grounded"]:
            raise RuntimeError("grounding unsupported")
        return {"product": "serum", "competitors": [{"name": "GlowCo"}, {"name": "Topicals", "why": "serums"},
                                                    {"name": "topicals"}, {"name": "x"}, "junk"]}
    out = comp.propose({"brand_name": "GlowCo", "website": "glowco.com"}, ["Topicals"], write=write)
    assert seen == [True, False]
    assert [c["name"] for c in out["candidates"]] == ["Topicals"] and out["product"] == "serum"


def test_propose_without_model_returns_nothing():
    def down(prompt, opts):
        raise RuntimeError("down")
    assert comp.propose({"brand_name": "GlowCo"}, [], write=down)["candidates"] == []


def test_check_is_strict():
    m = {"name": "Rare", "captions": ["rare steak"]}
    ok = lambda r: comp.check({"brand_name": "GlowCo"}, "serum", {"name": "Rare"}, m, write=lambda p, o: r)["ok"]
    assert not ok({"same_product": True, "videos_about_brand": False, "confidence": 0.99})
    assert not ok({"same_product": False, "videos_about_brand": True, "confidence": 0.99})
    assert not ok({"same_product": True, "videos_about_brand": True, "confidence": 0.5})
    assert ok({"same_product": True, "videos_about_brand": True, "confidence": 0.8})
    def boom(p, o):
        raise RuntimeError("x")
    assert not comp.check({"brand_name": "GlowCo"}, "", {"name": "Rare"}, m, write=boom)["ok"]


def test_strong_share_and_clean_hook():
    target = {"key": "topicals", "handles": ["topicals"]}
    vids = [{"mentions": ["topicals"]}, {"hashtags": ["topicalspartner"]}, {"caption": "topicals"}, {}]
    assert comp.strong_share(vids, target) == 0.5
    assert comp.clean_hook("pov you finally fixed your dark spots with @topicals #ad") == "pov you finally fixed your dark spots"
