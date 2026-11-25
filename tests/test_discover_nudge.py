"""Day-21 discover nudge: the brands to pitch instead of a slow one come from Thanos."""
from unittest import mock

from services.thanos import Ranking


def test_alternatives_are_thanos_ranked_from_the_slow_brands_category():
    import email_cron_routes as ecr

    cards = [{"id": 3, "brand_name": "Glowly", "reply_reason": "2 creators your size heard back"}]
    with mock.patch("services.thanos.rank", return_value=Ranking(brands=cards)) as rank:
        out = ecr.discover_alternatives("conn", 7, "Skincare", 11)
    args, kwargs = rank.call_args
    assert args == ("conn", 7)
    assert kwargs == {"surface": "alternatives", "n": 3, "exclude_ids": [11], "categories": ["skincare"]}
    assert out == cards


def test_alternatives_never_raise():
    import email_cron_routes as ecr

    with mock.patch("services.thanos.rank", side_effect=RuntimeError("db down")):
        assert ecr.discover_alternatives("conn", 7, None, None) == []
