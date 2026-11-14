import pytest

from services import ugc_tools as t


def test_parse_input_cleans_and_requires_product():
    got = t.parse_input("hooks", {"product": "  Vitamin C <script>serum\n", "niche": "Skincare", "platform": "evil"})
    assert got["product"] == "Vitamin C script serum" and got["niche"] == "skincare" and got["platform"] == "any"
    with pytest.raises(ValueError):
        t.parse_input("hooks", {"product": " "})


@pytest.mark.parametrize("tool", t.TOOLS)
def test_template_fallback_is_complete_and_has_no_numbers_claims(tool):
    inp = t.parse_input(tool, {"product": "vitamin C serum", "niche": "skincare"})
    out = t.generate(tool, inp, use_ai=False)
    assert out["source"] == "template" and t._valid(tool, out)
    text = str(out)
    assert "vitamin C serum" in text and "%" not in text


def test_ai_output_used_when_valid_and_falls_back_when_not():
    inp = t.parse_input("hooks", {"product": "dog shampoo", "niche": "pets"})
    good = {"hooks": [f"Hook {i} for dog shampoo" for i in range(12)]}
    out = t.generate("hooks", inp, llm=lambda s, u: good)
    assert out["source"] == "ai" and len(out["hooks"]) == 10
    out = t.generate("hooks", inp, llm=lambda s, u: {"hooks": ["only one"]})
    assert out["source"] == "template" and len(out["hooks"]) == 10


def test_pitch_keeps_placeholders():
    inp = t.parse_input("pitch", {"product": "protein bar", "brand": "Barbell", "niche": "fitness"})
    out = t.generate("pitch", inp, use_ai=False)
    assert "[portfolio link]" in out["body"] and "[first name]" in out["body"] and "Barbell" in out["body"]
