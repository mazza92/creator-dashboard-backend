"""'Not working?' on a pitch card: one report never kills an inbox for everyone unless Hunter agrees."""
from unittest import mock

from services import contact_repair as cr


def _run(verdict, status="unknown", bounce_fix=None, alt=None):
    conn = mock.MagicMock()
    brand = {"id": 5, "brand_name": "Varley", "contact_email": "marketing@varley.com", "email_status": status}
    with mock.patch.object(cr, "ensure_tables"), mock.patch.object(cr, "_brand", return_value=brand), \
            mock.patch.object(cr, "_log") as log, mock.patch.object(cr, "hunter_verify", return_value=verdict), \
            mock.patch.object(cr, "repair_after_bounce", return_value=bounce_fix or {}) as bounce, \
            mock.patch.object(cr, "known_bad", return_value=[]), \
            mock.patch.object(cr, "find_replacement", return_value=alt):
        out = cr.report_contact(conn, 5, "marketing@varley.com", creator_id=9)
    return out, log, bounce


def test_invalid_inbox_is_repaired_for_everyone():
    out, log, bounce = _run("invalid", bounce_fix={"new_email": "pr@varley.com"})
    assert out == {"outcome": "replaced", "email": "pr@varley.com"}
    bounce.assert_called_once()
    assert log.call_args[0][4] == "creator_report"


def test_valid_inbox_is_kept_but_creator_gets_an_alternative():
    out, _, bounce = _run("valid", alt={"email": "collabs@varley.com", "status": "valid"})
    assert out == {"outcome": "alternative", "email": "collabs@varley.com"}
    bounce.assert_not_called()


def test_nothing_better_known():
    out, _, bounce = _run("valid")
    assert out == {"outcome": "kept", "email": None}
    out, _, _ = _run(None)
    assert out == {"outcome": "flagged", "email": None}
