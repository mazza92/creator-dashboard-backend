from unittest import mock

from services import free_week_offer as fw


def test_copy_offers_the_week_without_overclaiming():
    mail = fw.free_week_email("Maya", {"brand_names": ["Saltwell", "Kindle"], "waiting": 9})
    assert mail["subject"] == "Your free week of Pro is ready"
    html = mail["html"]
    assert "Saltwell" in html and "7 more brands" in html
    assert "Start my free week" in html and "pay nothing" in html
    for claim in ("guarantee", "negotiat"):
        assert claim not in html.lower()


def test_sends_once_only_to_eligible_free_creators():
    sent = []
    with mock.patch.object(fw, "_candidates", return_value=[{"creator_id": 1}, {"creator_id": 2}, {"creator_id": 3}]), \
            mock.patch("services.polly_alerts.ensure_alert_tables"), \
            mock.patch("services.polly_alerts._recipient", side_effect=lambda c, cid, kind=None: {
                1: {"user_id": 11, "email": "a@x.co", "first": "A", "is_pro": False},
                2: {"user_id": 12, "email": "b@x.co", "first": "B", "is_pro": True},
                3: {"user_id": 13, "email": "c@x.co", "first": "C", "is_pro": False}}[cid]), \
            mock.patch("services.quota_trial_test.trial_eligible", side_effect=lambda cur, cid: cid == 1), \
            mock.patch("services.pro_upsell.pro_week_facts", return_value={"brand_names": [], "matched": 0}), \
            mock.patch("services.polly_alerts._unsubscribe_url", return_value="u"), \
            mock.patch("services.polly_alerts._log_email"), \
            mock.patch("services.polly_alerts._cursor"):
        out = fw.email_free_week_offers(mock.MagicMock(),
                                        send_fn=lambda *a: sent.append(a[0]) or {"success": True})
    assert sent == ["a@x.co"] and [o["creator_id"] for o in out] == [1]
