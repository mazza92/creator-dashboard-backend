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
