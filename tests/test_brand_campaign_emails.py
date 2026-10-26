from services import brand_campaign_emails as bce
from services import brand_workspace as ws


def test_received_email_for_workspace_brand():
    mail = bce.received_email(brand_name="GlowCo", product_name="Vitamin C serum", first_name="Ana Lee")
    assert mail["subject"] == "We got your Vitamin C serum campaign"
    html = mail["html"]
    assert "Hi Ana," in html and "Scan a competitor" in html and "Open my workspace" in html
    assert "/brand/workspace?" in html and "utm_campaign=campaign_received" in html
    assert "—" not in html


def test_received_email_without_account_keeps_roster_promise():
    mail = bce.received_email(brand_name="Old Brand", product_name="Tea", has_workspace=False)
    assert "Hi Old Brand team," in mail["html"]
    assert "private creator roster link" in mail["html"] and "/brand/workspace" not in mail["html"]


def test_live_email_links_workspace_or_roster():
    ws_mail = bce.live_email(brand_name="GlowCo", product_name="Serum", roster_url="https://app.newcollab.co/r/tok")
    assert ws_mail["subject"] == "Your Serum campaign is live" and "/brand/workspace/roster?" in ws_mail["html"]
    old = bce.live_email(brand_name="Old", product_name="Tea", roster_url="https://app.newcollab.co/r/tok", has_workspace=False)
    assert "https://app.newcollab.co/r/tok?utm_source=email" in old["html"]


def test_escapes_brand_and_product():
    mail = bce.received_email(brand_name="<b>x</b>", product_name="<script>y</script>")
    assert "<script>" not in mail["html"]


def test_decision_email_uses_branded_template():
    mail = ws.decision_email({"name": "GlowCo", "first_name": "Ana", "email": "a@glowco.com"}, True)
    assert mail["subject"] == "Your Newcollab workspace is ready"
    assert "Scan your competitors" in mail["html"] and "Open my workspace" in mail["html"]
    assert "<html" in mail["html"].lower()


def test_workspace_account_lookup():
    class Cur:
        def execute(self, sql, args): self.args = args
        def fetchone(self): return {"first_name": "Ana"}
    assert bce.workspace_account(Cur(), "a@glowco.com") == {"exists": True, "first_name": "Ana"}
