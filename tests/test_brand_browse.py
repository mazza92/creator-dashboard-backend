"""Directory search: the query picks the brands, Thanos orders them for the creator."""
from unittest import mock

from services import brand_browse as bb
from services.thanos import Ranking


def _conn(rows):
    cur = mock.MagicMock()
    cur.fetchall.return_value = rows
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


ROWS = [{"id": i, "slug": f"b{i}", "name": f"Brand {i}", "category": "skincare"} for i in (1, 2, 3)]


def test_creator_search_is_in_thanos_order():
    conn, cur = _conn([dict(r) for r in ROWS])
    cards = [{"id": 3, "match_score": 80, "reply_band": "high", "reply_reason": "Answers", "reply_chance": 0.05},
             {"id": 1, "match_score": 50, "reply_band": "new", "reply_reason": "Fits", "reply_chance": 0.02}]
    with mock.patch("services.thanos.score_brands", return_value=Ranking(brands=cards)) as score, \
            mock.patch("services.brand_reply_signal.load_stats", return_value={}):
        out = bb.browse_brands(conn, "skincare", creator_id=7)
    args, kwargs = score.call_args
    assert args[1] == 7 and sorted(args[2]) == [1, 2, 3] and kwargs["surface"] == "search"
    assert [b["id"] for b in out["brands"]] == [3, 1]          # 2 doesn't fit them
    assert (out["brands"][0]["reply_band"], out["brands"][0]["match_score"]) == ("high", 80)
    assert "response_rate" not in cur.execute.call_args.args[0]


def test_no_creator_keeps_query_order_and_badges():
    conn, _ = _conn([dict(r) for r in ROWS])
    stats = {2: {"pitched": 30, "replied": 0, "selected": 0}}
    with mock.patch("services.thanos.score_brands") as score, \
            mock.patch("services.brand_reply_signal.load_stats", return_value=stats):
        out = bb.browse_brands(conn, "skincare")
    score.assert_not_called()
    assert [b["id"] for b in out["brands"]] == [1, 2, 3]
    assert out["brands"][1]["reply_signal"]["tier"] == "cold"
