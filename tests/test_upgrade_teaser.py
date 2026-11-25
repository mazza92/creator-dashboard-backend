"""Upgrade nudge email: its locked teaser brands are Thanos's picks for the creator."""
from unittest import mock

from services.thanos import Ranking


def test_teaser_brands_come_from_thanos():
    from routes.admin_reports import upgrade_teaser_brands

    cards = [{"id": 1, "brand_name": "Glowly", "response_rate": 40, "reply_reason": "Recruiting creators right now"},
             {"id": 2, "brand_name": "Homey"}]
    with mock.patch("services.thanos.rank", return_value=Ranking(brands=cards)) as rank:
        out = upgrade_teaser_brands("conn", 7)
    assert rank.call_args.args == ("conn", 7)
    assert rank.call_args.kwargs == {"surface": "email", "n": 2}
    assert [b["name"] for b in out] == ["Glowly", "Homey"]
    assert (out[0]["initial"], out[0]["reply_rate"], out[1]["reply_rate"]) == ("GL", 40, None)
    assert out[0]["logo_color"] != out[1]["logo_color"]


def test_teaser_brands_never_raise():
    from routes.admin_reports import upgrade_teaser_brands

    with mock.patch("services.thanos.rank", side_effect=RuntimeError("down")):
        assert upgrade_teaser_brands("conn", 7) == []
