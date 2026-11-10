"""The send gate: Autopilot never sends to an unverified or dead inbox; follow-ups re-check; manual sends may go
to an unverified inbox only on a live domain."""

from types import SimpleNamespace
from unittest import mock

from services import deliverability as d


def _verify(status, email=None, changed=False):
    return lambda conn, bid, e, creator_id=None: {"email": email if email is not None else e, "status": status,
                                                  "changed": changed}


def _live(value):
    return mock.patch.object(d, "domain_receives_mail", return_value=value)


def test_valid_and_catch_all_go_out():
    with _live(True):
        assert d.gate(None, 1, "pr@brand.com", verify_fn=_verify("valid"))["send"] is True
        assert d.gate(None, 1, "pr@brand.com", verify_fn=_verify("catch-all"))["send"] is True


def test_unverified_never_goes_out_on_autopilot_but_defers():
    with _live(True):
        for status in ("unknown", "unchecked"):
            res = d.gate(None, 1, "pr@brand.com", verify_fn=_verify(status))
            assert res["send"] is False and res["defer"] is True
        manual = d.gate(None, 1, "pr@brand.com", verify_fn=_verify("unchecked"), manual=True)
        assert manual["send"] is True and manual["reason"] == "unverified_manual"


def test_invalid_without_replacement_is_blocked_with_replacement_is_swapped():
    with _live(True):
        blocked = d.gate(None, 1, "hailey@jovsbeauty.com", verify_fn=_verify("invalid", email=""))
        assert blocked["send"] is False and blocked["defer"] is False
        swapped = d.gate(None, 1, "old@brand.com", verify_fn=_verify("valid", email="pr@brand.com", changed=True))
        assert swapped["send"] and swapped["email"] == "pr@brand.com" and swapped["changed"]


def test_dead_domain_blocks_even_manual_and_flags_the_brand():
    conn = mock.MagicMock()
    with _live(False), mock.patch("services.contact_repair._log") as log:
        res = d.gate(conn, 7, "info@deadbrand.co", verify_fn=_verify("unchecked"), manual=True)
    assert res["send"] is False and res["reason"] == "domain_receives_no_mail"
    log.assert_called_once()


def test_bad_format_blocked():
    assert d.gate(None, 1, "not-an-email", verify_fn=_verify("valid"))["reason"] == "bad_format"


def test_dns_over_https_mx_lookup_and_cache():
    d._dns_cache.clear()
    answers = {"MX": {"Status": 0, "Answer": [{"type": 15, "data": "10 mx.brand.com."}]}}
    http = mock.MagicMock(side_effect=lambda url, params, headers, timeout: SimpleNamespace(
        status_code=200, json=lambda: answers.get(params["type"], {"Status": 0})))
    assert d.domain_receives_mail("brand.com", http=http) is True
    assert d.domain_receives_mail("brand.com", http=http) is True
    assert http.call_count == 1  # cached
    nx = mock.MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: {"Status": 3}))
    assert d.domain_receives_mail("gone-brand.xyz", http=nx) is False
    down = mock.MagicMock(side_effect=d.requests.RequestException("dns down"))
    assert d.domain_receives_mail("other.com", http=down) is None  # never block on DNS being unreachable


def test_defer_counter():
    assert d.defer_count("verify_pending:2") == 2
    assert d.defer_count(None) == 0 and d.defer_count("already_pitched") == 0


# ----- run_autopilot uses the gate for pitches and follow-ups -------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from services import polly_autopilot as pa  # noqa: E402

NOW = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)


def _run(pitches=(), follows=(), verify=None):
    calls = {"set": [], "sent": [], "requeued": [], "notes": []}
    batches = iter([list(pitches), [], list(follows)])

    def fake_set(conn, item_id, **fields):
        calls["set"].append((item_id, fields))

    def fake_send(conn, cid, to, subject, body, **kw):
        calls["sent"].append(to)
        return {"gmail_id": "g", "thread_id": "t", "message_id": "m"}

    patches = [
        mock.patch.object(pa, "ensure_autopilot_tables"), mock.patch("services.polly_gmail.ensure_gmail_table"),
        mock.patch.object(pa, "auto_approve_due", return_value=0),
        mock.patch.object(pa, "_due", side_effect=lambda *a, **k: next(batches, [])),
        mock.patch.object(pa, "_set", side_effect=fake_set),
        mock.patch.object(pa, "_already_pitched", return_value=False),
        mock.patch.object(pa, "_brand_closed", return_value=False),
        mock.patch.object(pa, "_followed_up_by_hand", return_value=False),
        mock.patch.object(pa, "_creator_names", return_value={"first": "Nat", "full": "Nat R"}),
        mock.patch.object(pa, "_hold_day4_nudge"), mock.patch.object(pa, "_close_day4_task"),
        mock.patch.object(pa, "requeue_after_bounce", side_effect=lambda c, cid, bid, e, now=None: calls["requeued"].append(e)),
        mock.patch("services.kit_view_tracking.track_kit_links", side_effect=lambda body, *a: body),
        mock.patch("services.polly_tracker.record_pitch_sent"), mock.patch("services.polly_tracker.add_event"),
    ]
    for p in patches:
        p.start()
    try:
        out = pa.run_autopilot(None, now=NOW, send_fn=fake_send, verify_fn=verify,
                               note_fn=lambda c, cid, text, chips: calls["notes"].append(text),
                               headsup_fn=lambda *a: None, daily_fn=lambda *a, **k: None)
    finally:
        for p in patches:
            p.stop()
    return out, calls


def _pitch(i, email, error=None):
    return {"id": i, "creator_id": 3975, "brand_id": 10 + i, "brand_name": f"B{i}", "to_email": email,
            "subject": "s", "body": "b", "error": error, "followups": 2}


def _follow(i, email):
    return {"id": i, "creator_id": 3975, "brand_id": 10 + i, "brand_name": f"B{i}", "to_email": email,
            "subject": "s", "body": "b", "followups": 2, "followups_sent": 0, "sent_at": NOW - timedelta(days=5),
            "followup_due_at": NOW, "thread_id": "t", "message_id": "m", "pitch_meta": {}}


def test_autopilot_defers_unverified_then_gives_up():
    with _live(True):
        out, calls = _run([_pitch(1, "pr@b1.com")], verify=_verify("unchecked"))
        assert calls["sent"] == [] and out.get("deferred") == 1
        assert calls["set"][0][1]["error"] == "verify_pending:1"
        out, calls = _run([_pitch(1, "pr@b1.com", error="verify_pending:3")], verify=_verify("unchecked"))
        assert calls["sent"] == [] and out["skipped"] == 1
        assert calls["set"][0][1]["status"] == "skipped"


def test_autopilot_sends_valid_and_skips_dead():
    with _live(True):
        out, calls = _run([_pitch(1, "pr@b1.com"), _pitch(2, "x@b2.com")],
                          verify=lambda c, bid, e, creator_id=None: (
                              {"email": e, "status": "valid"} if bid == 11 else {"email": None, "status": "invalid"}))
    assert calls["sent"] == ["pr@b1.com"] and out["pitches"] == 1 and out["skipped"] == 1
    assert any("couldn't confirm a working inbox" in n for n in calls["notes"])


def test_follow_up_to_dead_inbox_is_cancelled_and_moved_inbox_gets_a_fresh_pitch():
    with _live(True):
        _, dead = _run(follows=[_follow(1, "hailey@jovsbeauty.com")], verify=_verify("invalid", email=""))
        assert dead["sent"] == [] and dead["set"][0][1]["followup_status"] == "cancelled"
        _, moved = _run(follows=[_follow(2, "old@b2.com")],
                        verify=_verify("valid", email="pr@b2.com", changed=True))
        assert moved["sent"] == [] and moved["requeued"] == ["pr@b2.com"]
        _, ok = _run(follows=[_follow(3, "pr@b3.com")], verify=_verify("valid"))
        assert ok["sent"] == ["pr@b3.com"]
