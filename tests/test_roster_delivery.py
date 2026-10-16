from datetime import datetime, timedelta, timezone

import pytest

from services import roster_delivery as rd


class FakeCursor:
    """Scripted cursor: each execute pops the next result for fetchone/fetchall."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []
        self._current = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._current = self.results.pop(0) if self.results else None

    def fetchone(self):
        cur = self._current
        if isinstance(cur, list):
            return cur[0] if cur else None
        return cur

    def fetchall(self):
        cur = self._current
        if cur is None:
            return []
        return cur if isinstance(cur, list) else [cur]


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.tiktok.com/@me/video/1", "https://www.tiktok.com/@me/video/1"),
        ("drive.google.com/file/d/abc", "https://drive.google.com/file/d/abc"),
        ("javascript:alert(1)", None),
        ("not a link", None),
        ("", None),
        ("https://x.co/" + "a" * 700, None),
    ],
)
def test_clean_content_url(raw, expected):
    assert rd.clean_content_url(raw) == expected


def _pick_row(**over):
    row = {
        "id": 501,
        "brand_name": "ReGlow",
        "sku_note": "Glow serum 30ml",
        "hero_product": "",
        "instagram_handle": "reglow",
        "tiktok_handle": "",
        "first_name": "Ana",
        "shipping_address": {"city": "Austin", "country": "US"},
        "email": "creator@example.com",
    }
    row.update(over)
    return row


def test_picked_email_has_brief_deadline_and_cta():
    out = rd.build_creator_picked_email(_pick_row())
    assert out["subject"] == "ReGlow picked you for a gifted PR package"
    html = out["html"]
    assert "Glow serum 30ml" in html
    assert "@reglow" in html
    assert "1 raw UGC video" in html
    assert f"within {rd.CONTENT_DUE_DAYS} days" in html
    assert "Austin, US" in html
    assert "picked=501" in out["url"]
    assert "\u2014" not in html


def test_picked_email_escapes_brand_and_name():
    out = rd.build_creator_picked_email(_pick_row(brand_name="<b>Evil</b>", first_name="<script>"))
    assert "<script>" not in out["html"]
    assert "&lt;b&gt;Evil" in out["html"]


def test_picked_email_after_ship_says_already_shipped():
    out = rd.build_creator_picked_email(_pick_row(), already_shipped=True)
    assert "already shipped your box" in out["html"]


def test_content_ready_email_upsells_only_when_not_subscribed():
    row = {
        "brand_name": "ReGlow",
        "creator_name": "Ana",
        "token": "tok",
        "contact_first_name": "Sam",
        "delivered_content": {"organic_url": "https://tiktok.com/v/1", "ugc_url": "https://drive.google.com/x"},
        "delivered_count": 1,
        "pick_count": 5,
        "waiting_applicants": 12,
        "subscribed": False,
    }
    out = rd.build_content_ready_email(row)
    assert out["subject"] == "Ana delivered their ReGlow content"
    assert "1 of 5 creators delivered" in out["html"]
    assert "12 more creators" in out["html"]
    assert "$299/mo" in out["html"]
    assert "step=content" in out["url"]

    row["subscribed"] = True
    assert "$299/mo" not in rd.build_content_ready_email(row)["html"]


def test_pick_ship_reminder_schedule():
    now = datetime.now(timezone.utc)
    assert rd.pick_ship_reminder({"age_days": 1.5, "r1_at": None}) is None
    assert rd.pick_ship_reminder({"age_days": 2.1, "r1_at": None}) == 1
    assert rd.pick_ship_reminder({"age_days": 5.2, "r1_at": now - timedelta(days=1), "r2_at": None}) is None
    assert rd.pick_ship_reminder({"age_days": 5.2, "r1_at": now - timedelta(days=3), "r2_at": None}) == 2
    assert rd.pick_ship_reminder({"age_days": 9, "r1_at": now - timedelta(days=7), "r2_at": now}) is None


def test_ship_reminder_email_links_to_ship_step():
    row = {"brand_name": "ReGlow", "pick_count": 5, "token": "tok",
           "locked_at": datetime(2026, 10, 15, tzinfo=timezone.utc), "contact_first_name": None}
    out = rd.build_ship_reminder_email(row, 1)
    assert out["subject"] == "Your 5 creators for ReGlow are ready to ship"
    assert "Oct 15" in out["html"]
    assert out["trigger_number"] == 21
    assert "step=ship" in out["url"]
    assert rd.build_ship_reminder_email(row, 2)["trigger_number"] == 22


def test_deliver_requires_a_link():
    result, error = rd.deliver_content(FakeCursor(), creator_id=1, application_id=2,
                                       organic_url="", ugc_url="", note="")
    assert result is None and "link" in error


def test_deliver_rejects_unpicked_application():
    cur = FakeCursor([{"id": 2, "brand_id": 9, "status": "review", "campaign_id": 4,
                       "delivered_at": None, "campaign_status": "active"}])
    result, error = rd.deliver_content(cur, creator_id=1, application_id=2,
                                       organic_url="https://tiktok.com/v/1", ugc_url=None, note=None)
    assert result is None and "picks you" in error


def test_deliver_marks_posted_and_auto_ships_locked_campaign(monkeypatch):
    consumed = []
    monkeypatch.setattr("services.brand_billing.mark_free_campaign_consumed",
                        lambda cursor, brand_id: consumed.append(brand_id))
    cur = FakeCursor([
        {"id": 2, "brand_id": 9, "status": "ships", "campaign_id": 4,
         "delivered_at": None, "campaign_status": "locked"},
        None, None, None,
    ])
    result, error = rd.deliver_content(cur, creator_id=1, application_id=2,
                                       organic_url="https://tiktok.com/v/1",
                                       ugc_url="drive.google.com/file/x", note=" thanks ")
    assert error is None
    assert result["first"] is True
    assert result["content"]["ugc_url"] == "https://drive.google.com/file/x"
    assert result["content"]["note"] == "thanks"
    sqls = [s for s, _ in cur.executed]
    assert any("SET status = 'posted'" in s for s in sqls)
    assert any("UPDATE brand_pr_campaigns SET status = 'shipped'" in s for s in sqls)
    assert any("roster_content_delivered" in s for s in sqls)
    assert consumed == [9]


def test_clean_brief_trims_drops_empty_and_validates():
    brief, error = rd.clean_brief({
        "message": "  Show how fast it foams  ",
        "must_include": "",
        "avoid": "Competitor brands",
        "tags": "@bite #plasticfree",
        "reference_url": "tiktok.com/@bite/video/1",
        "junk": "ignored",
    })
    assert error is None
    assert brief["message"] == "Show how fast it foams"
    assert "must_include" not in brief and "junk" not in brief
    assert brief["reference_url"] == "https://tiktok.com/@bite/video/1"
    assert brief["updated_at"]

    assert rd.clean_brief({"message": "x" * 281})[1]
    assert rd.clean_brief({"reference_url": "javascript:alert(1)"})[1]
    assert rd.clean_brief({"message": "  "}) == ({}, None)
    assert rd.brief_public({}) is None
    assert rd.brief_public({"avoid": "x"})["message"] == ""


def test_picked_email_includes_brief_teaser_escaped():
    out = rd.build_creator_picked_email(_pick_row(brief={"message": "Show the <foam>"}))
    assert "Their brief in one line" in out["html"]
    assert "Show the &lt;foam&gt;" in out["html"]
    assert "Their brief in one line" not in rd.build_creator_picked_email(_pick_row())["html"]


def test_brief_added_email_links_to_brief():
    out = rd.build_creator_brief_added_email(_pick_row(brief={"message": "Morning routine"}))
    assert out["subject"] == "ReGlow added a brief for your post"
    assert "Morning routine" in out["html"]
    assert "picked=501" in out["url"]


def test_creator_notices_claim_before_send_and_release_on_failure(monkeypatch):
    monkeypatch.setattr(rd, "_BRIEF_READY", True)
    rows = [
        _pick_row(id=1, picked_notified_at=None, shipped_notified_at=None, creator_shipped_at=None, brand_id=9),
        _pick_row(id=2, picked_notified_at=datetime.now(timezone.utc), shipped_notified_at=None,
                  creator_shipped_at=datetime.now(timezone.utc), brand_id=9),
        _pick_row(id=3, picked_notified_at=None, shipped_notified_at=None, creator_shipped_at=None, brand_id=9),
    ]
    cur = FakeCursor([rows, {"id": 1}, {"id": 2}, None])
    sent = []

    def send(to, subject, html, kind):
        sent.append((subject, kind))
        return {"success": kind != "roster_creator_shipped"}

    stats = rd.send_creator_notices(cur, FakeConn(), send_fn=send)
    assert stats["picked"] == 1
    assert stats["failed"] == 1
    assert [k for _, k in sent] == ["roster_creator_picked", "roster_creator_shipped"]
    release = [s for s, _ in cur.executed if "shipped_notified_at = NULL" in s]
    assert len(release) == 1


def test_card_exposes_delivered_links_for_posted_picks():
    from brand_pr_roster_routes import _card_from_row

    base = {
        "application_id": 11, "creator_id": 22, "brand_id": 33, "status": "posted",
        "selected_posts": [], "shipping_address": None, "applied_at": None, "shipped_at": None,
        "username": "ana", "first_name": "Ana", "image_profile": "", "followers_count": 900,
        "niche": "", "kit_slug": "", "social_links": [],
        "delivered_content": {"organic_url": "https://tiktok.com/v/1", "ugc_url": "", "note": ""},
        "delivered_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
    }
    card = _card_from_row(base, reveal_shipping=True, selected_ids=[], campaign_status="shipped")
    assert card["delivered"]["organic_url"] == "https://tiktok.com/v/1"
    assert card["delivered"]["delivered_at"].startswith("2026-10-20")

    review = dict(base, status="review")
    assert "delivered" not in _card_from_row(review, reveal_shipping=False, selected_ids=[], campaign_status="active")
