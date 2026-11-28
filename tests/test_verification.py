"""Verification is separate from activation (services.verification)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from services import start_choice as sc
from services import verification as v


class Cur:
    def __init__(self, row=None, profile=None, fail=False):
        self.row, self.profile, self.fail, self.last = row, profile, fail, None

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("db down")
        self.last = self.profile if "creator_profile_data" in sql else self.row

    def fetchone(self):
        return self.last


class Conn:
    def __init__(self, **kw):
        self.cur = Cur(**kw)
        self.rolled_back = False

    def cursor(self, *a, **k):
        return self.cur

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_pending_and_not_ready_creators_are_locked_until_pro():
    assert v.is_unverified(Conn(row=("pending", "free")), 1)
    assert v.is_unverified(Conn(row=("rejected", "free")), 1)
    assert not v.is_unverified(Conn(row=("pending", "pro")), 1)
    assert not v.is_unverified(Conn(row=("approved", "free")), 1)
    assert not v.is_unverified(Conn(row=("pro_approved", "pro")), 1)


def test_lock_fails_open():
    conn = Conn(fail=True)
    assert not v.is_unverified(conn, 1)
    assert conn.rolled_back
    assert not v.is_unverified(Conn(row=None), 1)
    assert not v.is_unverified(Conn(row=("pending", "free")), None)


def test_skipped_review_is_paid_without_team_approval():
    assert v.skipped_review(Conn(row=("pro_approved", "pro")), 1)
    assert not v.skipped_review(Conn(row=("approved", "pro")), 1)
    assert not v.skipped_review(Conn(fail=True), 1)


def test_redacted_pitch_keeps_the_words_not_the_inbox():
    out = v.redact_pitch({"brand": "Glow", "email": "pr@glow.com", "mailto": "mailto:pr@glow.com", "body": "Hi"})
    assert out["email"] == "" and out["mailto"] is None and out["locked"] == "verification"
    assert out["body"] == "Hi" and out["brand"] == "Glow"
    assert v.redact_pitch(None) is None


def test_readiness_lists_what_the_review_checks():
    out = v.readiness(Conn(row=("pending", "free"), profile=(8, 45)), 1)
    assert out["status"] == "checking"
    checks = {c["id"]: c for c in out["checks"]}
    assert checks["posts"]["ok"] is False and checks["posts"]["detail"] == "You have 8"
    assert checks["recent"]["ok"] is False
    assert checks["quality"]["ok"] is None
    ready = v.readiness(Conn(row=("rejected", "free"), profile=(30, 2)), 1)
    assert ready["status"] == "not_ready"
    assert all(c["ok"] in (True, None) for c in ready["checks"])
    assert v.readiness(Conn(row=("approved", "free")), 1)["status"] == "verified"


def test_relay_is_gmail_only_after_skipping_review():
    from services import pitch_relay as pr

    class RelayCur(Cur):
        def execute(self, sql, params=None):
            self.last = ("newcollab",) if "polly_autopilot" in sql else self.row

    class RelayConn(Conn):
        def __init__(self, status):
            super().__init__()
            self.cur = RelayCur(row=(status, "pro"))

    assert pr.send_via(RelayConn("pro_approved"), 1) == pr.SEND_VIA_GMAIL
    assert pr.send_via(RelayConn("approved"), 1) == pr.SEND_VIA_NEWCOLLAB


def test_creators_being_verified_get_the_matches_first_welcome():
    from tests.test_start_choice import FakeCursor
    created = datetime(2026, 11, 27, tzinfo=timezone.utc)
    cur = FakeCursor({1: ("pending", None, None, None, None), 2: ("rejected", None, None, None, None),
                      3: ("pending", None, "sub_1", None, created)})
    assert sc._in_test(cur, 1)
    assert sc._in_test(cur, 2)
    assert not sc._in_test(cur, 3)


def test_polly_pitch_comes_back_locked_for_unverified():
    from services import polly_tools as pt
    state = SimpleNamespace(pitch={"brand": "Glow", "email": "pr@glow.com", "mailto": "m"},
                            pitch_batch=[{"brand": "B", "email": "b@b.com"}], creator_id=1,
                            db=lambda: Conn(row=("pending", "free")))
    result = {"ok": True, "inbox": "pr@glow.com"}
    pt._lock_unverified(state, result)
    assert state.pitch["email"] == "" and state.pitch["locked"] == "verification"
    assert state.pitch_batch[0]["email"] == ""
    assert result["outreach_locked"] and "inbox" not in result

    state = SimpleNamespace(pitch={"brand": "Glow", "email": "pr@glow.com"}, pitch_batch=None, creator_id=1,
                            db=lambda: Conn(row=("approved", "free")))
    result = {"ok": True, "inbox": "pr@glow.com"}
    pt._lock_unverified(state, result)
    assert state.pitch["email"] == "pr@glow.com" and result["inbox"]


def test_contact_routes_refuse_unverified(monkeypatch):
    import pr_crm_routes
    monkeypatch.setattr(pr_crm_routes, "get_db_connection", lambda: Conn(row=("pending", "free")))
    body, code = v.refuse_unverified(5)
    assert code == 403 and body["verification_required"]
    monkeypatch.setattr(pr_crm_routes, "get_db_connection", lambda: Conn(row=("approved", "free")))
    assert v.refuse_unverified(5) is None


def test_creators_being_verified_never_get_the_free_week(monkeypatch):
    from services import quota_trial_test as q
    import services.start_choice as sc_mod
    import services.creator_offers as offers
    monkeypatch.setattr(sc_mod, "picked_free", lambda c, i: False)
    monkeypatch.setattr(offers, "has_trial_offer", lambda c, i: True)
    assert q.checkout_trial_eligible(Cur(row=("pending",)), 1) is False
    assert q.checkout_trial_eligible(Cur(row=("rejected",)), 1) is False
    assert q.checkout_trial_eligible(Cur(row=("approved",)), 1) is True


def test_gig_apply_refused_while_being_verified(monkeypatch):
    import opportunities_routes as opp
    import services.verification as ver
    monkeypatch.setattr(opp, "get_creator_id_from_session", lambda: 5)
    monkeypatch.setattr(ver, "refuse_unverified", lambda cid: (ver.VERIFICATION_REQUIRED, 403))
    from flask import Flask
    app = Flask(__name__)
    with app.test_request_context(json={}):
        body, code = opp.apply_opportunity(1)
    assert code == 403 and body.get_json()["verification_required"]


def test_verified_welcome_comes_back_once_after_exploring(monkeypatch):
    import services.creator_tracker as ct
    seen = set()
    monkeypatch.setattr(ct, "milestones", lambda conn, cid: seen)
    approved = Conn(row=("approved", "free"))
    assert not v.needs_verified_welcome(approved, 1)          # never explored while pending: normal rules
    seen.add(v.EXPLORED)
    assert v.needs_verified_welcome(approved, 1)              # explored, now verified: welcome is back
    assert not v.needs_verified_welcome(Conn(row=("pending", "free")), 1)
    assert not v.needs_verified_welcome(Conn(row=("pro_approved", "pro")), 1)  # paid to skip: Autopilot instead
    seen.add(v.RETURN_DONE)
    assert not v.needs_verified_welcome(approved, 1)          # once


def test_saved_locked_pitches_get_their_inbox_back_once_verified():
    class UnlockCur(Cur):
        def execute(self, sql, params=None):
            self.sql = sql
            self.last = self.row

        def fetchall(self):
            return [(7, "pr@glow.com"), (8, "hi@calpak.com")]

    class UnlockConn(Conn):
        def __init__(self, status):
            super().__init__()
            self.cur = UnlockCur(row=(status, "free"))

    msgs = [
        {"role": "assistant", "pitch": {"brand_id": 7, "subject": "Hi", "body": "Body", "email": "", "mailto": None,
                                        "locked": "verification"}},
        {"role": "assistant", "pitch_batch": {"items": [
            {"brand_id": 8, "subject": "S", "body": "B", "email": "", "locked": "verification"},
            {"brand_id": 9, "subject": "S", "body": "B", "email": "", "locked": "verification"}]}},
    ]
    still, changed = v.unlock_saved_pitches(UnlockConn("pending"), 1, msgs)
    assert not changed and msgs[0]["pitch"]["locked"] == "verification"

    out, changed = v.unlock_saved_pitches(UnlockConn("approved"), 1, msgs,
                                          notes={"pitch_outbox": {"9": {"email": "kept@brand.com"}}})
    assert changed
    p = out[0]["pitch"]
    assert p["email"] == "pr@glow.com" and p["mailto"].startswith("mailto:pr@glow.com") and "locked" not in p
    items = out[1]["pitch_batch"]["items"]
    assert items[0]["email"] == "hi@calpak.com"
    assert items[1]["email"] == "kept@brand.com"   # the outbox Polly kept wins
    _, again = v.unlock_saved_pitches(UnlockConn("approved"), 1, out)
    assert not again


class PreviewCur:
    """creators row (status, tier) + polly_drafts brand ids."""

    def __init__(self, status, drafted):
        self.status, self.drafted, self.last, self.rows = status, drafted, None, []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "to_regclass('polly_drafts')" in s:
            self.last = (True,)
        elif s.startswith("SELECT DISTINCT brand_id FROM polly_drafts"):
            self.rows = [(b,) for b in self.drafted]
        else:
            self.last = (self.status, "free")

    def fetchone(self):
        return self.last

    def fetchall(self):
        return self.rows


class PreviewConn(Conn):
    def __init__(self, status="pending", drafted=()):
        super().__init__()
        self.cur = PreviewCur(status, list(drafted))


def test_first_pitch_is_free_while_being_verified():
    locked, args = v.preview_gate(PreviewConn(drafted=()), 1, "draft_pitch", {"brand_id": 7})
    assert locked is None and args == {"brand_id": 7}


def test_second_brand_is_locked_without_calling_the_model():
    locked, _ = v.preview_gate(PreviewConn(drafted=[7]), 1, "draft_pitch", {"brand_id": 8})
    assert locked["status"] == "preview_used" and locked["verification_required"]
    locked, _ = v.preview_gate(PreviewConn(drafted=[7]), 1, "draft_pitch", {"brand_name": "Calpak"})
    assert locked
    # the same brand again (their preview) stays open
    assert v.preview_gate(PreviewConn(drafted=[7]), 1, "draft_pitch", {"brand_id": 7})[0] is None


def test_batch_is_cut_to_one_then_locked():
    locked, args = v.preview_gate(PreviewConn(drafted=()), 1, "draft_pitch_batch", {"brand_ids": [3, 4, 5]})
    assert locked is None and args["brand_ids"] == [3]
    locked, _ = v.preview_gate(PreviewConn(drafted=[3]), 1, "draft_pitch_batch", {"brand_ids": [4, 5]})
    assert locked["status"] == "preview_used"


def test_brand_replies_locked_and_verified_creators_untouched():
    assert v.preview_gate(PreviewConn(drafted=()), 1, "draft_brand_reply", {})[0]
    for status in ("approved", "pro_approved"):
        locked, args = v.preview_gate(PreviewConn(status=status, drafted=[1, 2, 3]), 1, "draft_pitch_batch",
                                      {"brand_ids": [4, 5, 6]})
        assert locked is None and args["brand_ids"] == [4, 5, 6]
    assert v.preview_gate(PreviewConn(drafted=[7]), 1, "edit_pitch", {"instruction": "shorter"})[0] is None


def test_run_tool_returns_the_lock_without_running_the_tool(monkeypatch):
    from services import polly_tools as pt
    called = []
    monkeypatch.setitem(pt._BY_NAME, "draft_pitch", lambda state, args: called.append(args) or {"ok": True})
    state = SimpleNamespace(creator_id=1, db=lambda: PreviewConn(drafted=[7]), tool_log=[], pitch=None,
                            pitch_batch=None)
    out = pt.run_tool(state, "draft_pitch", {"brand_id": 8})
    assert out["status"] == "preview_used" and not called


def test_chat_locks_once_the_preview_is_used():
    assert not v.chat_locked(PreviewConn(drafted=()), 1)
    assert v.chat_locked(PreviewConn(drafted=[7]), 1)
    assert not v.chat_locked(PreviewConn(status="approved", drafted=[7, 8]), 1)
    assert not v.chat_locked(PreviewConn(drafted=[7]), None)
