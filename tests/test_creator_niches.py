import json
from unittest import mock

from services import creator_niches as cn


class _Cur:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))


class _Conn:
    def __init__(self):
        self.log, self.commits = [], 0

    def cursor(self, *a, **k):
        return _Cur(self.log)

    def commit(self):
        self.commits += 1


def test_clean_niches_maps_to_settings_ids():
    assert cn.clean_niches(["Makeup", "Home & Living", "gaming", "beauty", "Tech & Gadgets", "nonsense"]) == \
        ["beauty", "home", "gaming", "tech"]
    assert cn.clean_niches("Fitness & Wellness") == ["fitness", "wellness"]
    assert cn.clean_niches(["Food & Beverage", "Arts & Crafts"]) == ["food"]
    assert cn.clean_niches(None) == []


def test_recorded_niches_prefers_creator_niches_then_legacy_column():
    assert cn.recorded_niches({"creator_niches": ["makeup", "gaming"], "niche": '["travel"]'}) == ["beauty", "gaming"]
    assert cn.recorded_niches({"creator_niches": [], "niche": '["skincare", "Pet"]'}) == ["skincare", "pet"]


def test_record_overrides_stale_polly_copy():
    notes = {"niche": ["makeup", "travel"], "stage": "growing"}
    out = cn.with_recorded_niches(notes, {"creator_niches": ["fitness"]})
    assert out["niche"] == ["fitness"] and out["stage"] == "growing"
    assert cn.with_recorded_niches(notes, {"creator_niches": []})["niche"] == ["makeup", "travel"]


def test_save_writes_both_columns_and_polly_copy():
    conn = _Conn()
    with mock.patch.object(cn, "_forget_matches") as forget:
        out = cn.save_creator_niches(conn, 7, ["Makeup", "gaming"])
    assert out == ["beauty", "gaming"]
    (creators_sql, p1), (notes_sql, p2) = conn.log
    assert "creator_niches = %s, niche = %s" in creators_sql and p1 == (["beauty", "gaming"], json.dumps(out), 7)
    assert "polly_threads" in notes_sql and p2 == (json.dumps(out), 7)
    assert conn.commits == 1
    forget.assert_called_once_with(7)


def test_save_ignores_empty_or_unknown():
    conn = _Conn()
    assert cn.save_creator_niches(conn, 7, ["nonsense"]) == []
    assert conn.log == [] and conn.commits == 0


def test_save_keeps_top_three():
    conn = _Conn()
    with mock.patch.object(cn, "_forget_matches"):
        out = cn.save_creator_niches(conn, 7, ["beauty", "skincare", "fashion", "travel", "pet"])
    assert out == ["beauty", "skincare", "fashion"]
