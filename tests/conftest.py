"""Suite-wide test setup."""

import pytest


@pytest.fixture(autouse=True)
def _email_policy_off_unless_tested(request, monkeypatch):
    """Most email tests script fake DB rows query by query; the rulebook's own lookup would consume them.
    tests/test_email_policy.py exercises the rulebook against a real database instead."""
    if request.node.fspath.basename == "test_email_policy.py" or request.node.get_closest_marker("email_policy"):
        return
    from services import email_policy
    monkeypatch.setattr(email_policy, "allowed", lambda *a, **k: True)
    monkeypatch.setattr(email_policy, "allowed_for_creator", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def _no_live_dns(request, monkeypatch):
    """The send gate asks DNS whether a brand's domain receives mail; tests never reach the network."""
    if request.node.fspath.basename == "test_deliverability.py":
        return
    from services import deliverability
    monkeypatch.setattr(deliverability, "domain_receives_mail", lambda *a, **k: True)
