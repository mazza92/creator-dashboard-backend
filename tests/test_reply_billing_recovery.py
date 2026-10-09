"""One-tap reply check-ins, renewal dunning, and abandoned Pro checkout recovery."""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from services import checkin_links  # noqa: E402
from services.checkout_recovery import checkout_recovery_kwargs, handle_expired_session  # noqa: E402
from services.polly_alerts import nudge_email  # noqa: E402
from services.subscription_retention import dunning_email_html, dunning_key_for  # noqa: E402

SECRET = {"JWT_SECRET_KEY": "test-secret", "BACKEND_URL": "https://api.example", "FRONTEND_URL": "https://app.example"}


class CheckinTokenTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, SECRET)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_round_trip(self):
        tok = checkin_links.make_token(7, "checkin_interested", 55, 9, "Glow Co")
        info = checkin_links.read_token(tok)
        self.assertEqual((info["creator_id"], info["chip_id"], info["task_id"], info["brand_id"], info["brand_name"]),
                         (7, "checkin_interested", 55, 9, "Glow Co"))

    def test_tampered_expired_and_unknown_are_rejected(self):
        tok = checkin_links.make_token(7, "checkin_quiet", 1, 9, "Glow Co")
        body, sig = tok.rsplit(".", 1)
        forged = checkin_links._b64(b'[8,"checkin_quiet",1,9,"Glow Co",9999999999]')
        self.assertIsNone(checkin_links.read_token(f"{forged}.{sig}"))
        self.assertIsNone(checkin_links.read_token(tok, now=time.time() + checkin_links.TOKEN_TTL_SECONDS + 5))
        self.assertEqual(checkin_links.make_token(7, "line_up"), "")
        self.assertIsNone(checkin_links.read_token("garbage"))

    def test_no_secret_means_no_links(self):
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "", "SECRET_KEY": ""}):
            self.assertEqual(checkin_links.one_tap_url(7, "checkin_quiet"), "")

    def test_next_url_opens_the_follow_on_chip(self):
        info = {"chip_id": "checkin_quiet", "brand_id": 9, "brand_name": "Glow Co"}
        q = parse_qs(urlparse(checkin_links.next_polly_url("https://app.example", info, "follow_up_d4")).query)
        self.assertEqual(q["chip"], ["draft_followup"])
        self.assertEqual(q["logged"], ["checkin_quiet"])


class OneTapEmailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, SECRET)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_day4_email_records_outcome_from_the_email(self):
        mail = nudge_email("Ana", {"key": "follow_up_d4", "brand_name": "Glow Co", "brand_id": 9,
                                   "task_id": 7, "message": "Day 4 on **Glow Co**.", "chips": []}, creator_id=3)
        html = mail["html"]
        self.assertEqual(html.count("https://api.example/api/polly/checkin?t="), 6)
        for label in ("They&#x27;re interested", "They said no", "No reply yet", "Email bounced", "I never sent it"):
            self.assertIn(label, html)
        self.assertIn("no login", html)

    def test_pr_email_has_arrived_buttons(self):
        mail = nudge_email("Ana", {"key": "pr_shipped", "brand_name": "Glow Co", "brand_id": 9,
                                   "task_id": 7, "message": "Box?", "chips": []}, creator_id=3)
        self.assertEqual(mail["html"].count("/api/polly/checkin?t="), 2)
        self.assertIn("Yes, it arrived", mail["html"])


class CheckinEndpointTests(unittest.TestCase):
    def setUp(self):
        from routes import polly as routes_polly

        self.env = patch.dict(os.environ, SECRET)
        self.env.start()
        self.routes = routes_polly
        app = Flask(__name__)
        app.register_blueprint(routes_polly.polly_bp)
        self.client = app.test_client()
        self.applied = set()
        self.conn = MagicMock()
        self.patches = [
            patch("pr_crm_routes.get_db_connection", return_value=self.conn),
            patch.object(routes_polly, "_checkin_already_applied",
                         side_effect=lambda conn, tid: tid in self.applied),
            patch("services.polly_usage.log_usage",
                  side_effect=lambda conn, cid, ev, **kw: self.applied.add(kw["meta"]["token"])),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()

    def test_get_does_not_apply_so_link_scanners_are_harmless(self):
        tok = checkin_links.make_token(3, "checkin_interested", 7, 9, "Glow Co")
        with patch.object(self.routes, "apply_task_chip") as chip:
            resp = self.client.get(f"/api/polly/checkin?t={tok}&src=follow_up_d4")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"document.forms[0].submit()", resp.data)
        chip.assert_not_called()

    def test_post_applies_once_and_redirects_into_polly(self):
        tok = checkin_links.make_token(3, "checkin_quiet", 7, 9, "Glow Co")
        with patch.object(self.routes, "apply_task_chip", return_value={"ok": True}) as chip:
            first = self.client.post("/api/polly/checkin", data={"t": tok, "src": "follow_up_d4"})
            second = self.client.post("/api/polly/checkin", data={"t": tok})
        chip.assert_called_once_with(self.conn, 3, "checkin_quiet", 7, 9, "Glow Co")
        self.assertEqual(first.status_code, 303)
        q = parse_qs(urlparse(first.headers["Location"]).query)
        self.assertEqual(q["chip"], ["draft_followup"])
        self.assertEqual(second.status_code, 303)

    def test_not_sent_unmarks_the_brand(self):
        tok = checkin_links.make_token(3, "checkin_not_sent", 7, 9, "Glow Co")
        thread = {"messages": [], "suggested_brands": [], "notes": {"pitched_brand_names": ["Glow Co"]}}
        with patch.object(self.routes, "apply_task_chip", return_value={"ok": True, "unmark_pitched": True}), \
                patch.object(self.routes, "load_thread", return_value=thread), \
                patch.object(self.routes, "save_thread") as save:
            self.client.post("/api/polly/checkin", data={"t": tok})
        notes = save.call_args[0][4]
        self.assertNotIn("Glow Co", notes.get("pitched_brand_names") or [])

    def test_bad_token_just_opens_polly(self):
        with patch.object(self.routes, "apply_task_chip") as chip:
            resp = self.client.post("/api/polly/checkin", data={"t": "nope.bad"})
        self.assertEqual(resp.status_code, 303)
        chip.assert_not_called()


class DunningTests(unittest.TestCase):
    def test_renewal_failures_pick_the_right_email(self):
        cycle = {"billing_reason": "subscription_cycle", "next_payment_attempt": 123}
        self.assertEqual(dunning_key_for("invoice.payment_failed", {**cycle, "attempt_count": 1}), "dunning_attempt_1")
        self.assertIsNone(dunning_key_for("invoice.payment_failed", {**cycle, "attempt_count": 2}))
        self.assertEqual(dunning_key_for("invoice.payment_failed", {**cycle, "attempt_count": 3}), "dunning_attempt_3")
        final = {"billing_reason": "subscription_cycle", "attempt_count": 4, "next_payment_attempt": None}
        self.assertEqual(dunning_key_for("invoice.payment_failed", final), "dunning_final")
        self.assertIsNone(dunning_key_for("invoice.payment_failed", {**final, "metadata": {"dunning_final": "1"}}))
        self.assertEqual(dunning_key_for("invoice.payment_action_required", cycle), "action_required")

    def test_first_payment_failures_never_get_dunning(self):
        first = {"billing_reason": "subscription_create", "attempt_count": 1}
        self.assertIsNone(dunning_key_for("invoice.payment_failed", first))
        self.assertIsNone(dunning_key_for("invoice.payment_action_required", first))

    def test_email_links_to_hosted_invoice_and_final_to_restart(self):
        html = dunning_email_html("ana", "19.00 USD", "https://invoice.stripe.com/i/abc")
        self.assertIn("https://invoice.stripe.com/i/abc", html)
        self.assertIn("still on for now", html)
        final = dunning_email_html("ana", "19.00 USD", "https://invoice.stripe.com/i/abc", final=True)
        self.assertIn("upgrade=pro", final)
        self.assertNotIn("invoice.stripe.com", final)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        import subscription_routes

        self.sr = subscription_routes
        app = Flask(__name__)
        app.register_blueprint(subscription_routes.subscription_bp)
        self.client = app.test_client()

    def _post(self, event):
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET_SUBSCRIPTION": ""}):
            return self.client.post("/api/subscription/webhook", json=event)

    def test_renewal_failure_emails_pay_link_and_stamps_invoice(self):
        invoice = {"id": "in_1", "billing_reason": "subscription_cycle", "attempt_count": 1,
                   "next_payment_attempt": 1, "amount_due": 1900, "currency": "usd",
                   "hosted_invoice_url": "https://invoice.stripe.com/i/abc", "customer": "cus_1",
                   "subscription": "sub_1", "metadata": {}}
        event = {"id": "evt", "object": "event", "type": "invoice.payment_failed", "data": {"object": invoice}}
        with patch.object(self.sr, "get_db_connection", return_value=MagicMock()), \
                patch.object(self.sr, "_creator_by_stripe", return_value={"id": 3, "email": "a@x.co", "username": "ana"}), \
                patch.object(self.sr, "_send_creator_resend", return_value=True) as send, \
                patch.object(self.sr.stripe.Invoice, "modify") as stamp:
            resp = self._post(event)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("https://invoice.stripe.com/i/abc", send.call_args[0][2])
        stamp.assert_called_once_with("in_1", metadata={"dunning_attempt_1": "1"})

    def test_failed_first_payment_sends_nothing(self):
        invoice = {"id": "in_2", "billing_reason": "subscription_create", "attempt_count": 1,
                   "customer": "cus_1", "subscription": "sub_1", "metadata": {}}
        event = {"id": "evt", "object": "event", "type": "invoice.payment_failed", "data": {"object": invoice}}
        with patch.object(self.sr, "get_db_connection", return_value=MagicMock()), \
                patch.object(self.sr, "_creator_by_stripe", return_value={"id": 3, "email": "a@x.co"}), \
                patch.object(self.sr, "_send_creator_resend") as send:
            self._post(event)
        send.assert_not_called()

    def test_expired_checkout_routes_to_recovery(self):
        session = {"id": "cs_1", "metadata": {"tier": "pro", "creator_id": "3"}}
        event = {"id": "evt", "object": "event", "type": "checkout.session.expired", "data": {"object": session}}
        with patch.object(self.sr, "get_db_connection", return_value=MagicMock()), \
                patch("services.checkout_recovery.handle_expired_session", return_value={"sent": True}) as handle:
            resp = self._post(event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(handle.call_args[0][1]["id"], "cs_1")


class CheckoutRecoveryTests(unittest.TestCase):
    WHO = {"creator_id": 3, "user_id": 11, "email": "a@x.co", "first": "Ana", "is_pro": False}
    SESSION = {"metadata": {"tier": "pro", "creator_id": "3"},
               "after_expiration": {"recovery": {"url": "https://checkout.stripe.com/r/abc"}}}

    def _run(self, who=None, recent=False, status="inactive", session=None):
        sent = []
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {"subscription_status": status}
        with patch("services.polly_alerts.ensure_alert_tables"), \
                patch("services.polly_alerts._recipient", return_value=who or self.WHO), \
                patch("services.polly_alerts._recently_emailed", return_value=recent), \
                patch("services.polly_alerts._unsubscribe_url", return_value="https://u"), \
                patch("services.polly_alerts._log_email"):
            out = handle_expired_session(conn, session or self.SESSION,
                                         send_fn=lambda *a: sent.append(a) or {"success": True})
        return out, sent

    def test_emails_the_stripe_recovery_link(self):
        out, sent = self._run()
        self.assertTrue(out["sent"])
        self.assertIn("https://checkout.stripe.com/r/abc", sent[0][2])
        self.assertEqual(sent[0][4], "checkout_recovery")

    def test_skips_pro_throttled_and_non_pro_sessions(self):
        self.assertEqual(self._run(who={**self.WHO, "is_pro": True})[0]["why"], "already_pro")
        self.assertEqual(self._run(recent=True)[0]["why"], "throttled")
        self.assertEqual(self._run(status="active")[0]["why"], "already_pro")
        self.assertEqual(self._run(session={"metadata": {"product": "brand_gifted_ugc"}})[0]["why"], "not_pro_checkout")

    def test_falls_back_to_in_app_upgrade(self):
        _, sent = self._run(session={"metadata": {"tier": "pro", "creator_id": "3"}})
        self.assertIn("upgrade=pro", sent[0][2])

    def test_session_kwargs(self):
        kw = checkout_recovery_kwargs(now=1000)
        self.assertEqual(kw["expires_at"], 1000 + 2 * 3600)
        self.assertTrue(kw["after_expiration"]["recovery"]["enabled"])
        self.assertFalse(checkout_recovery_kwargs(promo_codes=False)["after_expiration"]["recovery"]["allow_promotion_codes"])

    def test_create_checkout_is_not_card_only(self):
        import subscription_routes as sr

        app = Flask(__name__)
        app.register_blueprint(sr.subscription_bp)
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {
            "email": "a@x.co", "name": "ana", "subscription_tier": "free", "subscription_status": None,
            "stripe_subscription_id": None, "stripe_customer_id": None}
        created = MagicMock(url="https://checkout.stripe.com/c/1", id="cs_1")
        with patch.object(sr, "get_creator_id_from_session", return_value=3), \
                patch.object(sr, "get_db_connection", return_value=conn), \
                patch.dict(os.environ, {"STRIPE_PRICE_ID_PRO": "price_x"}), \
                patch.object(sr.stripe.checkout.Session, "create", return_value=created) as create:
            resp = app.test_client().post("/api/subscription/create-checkout", json={"tier": "pro"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        kw = create.call_args.kwargs
        self.assertNotIn("payment_method_types", kw)
        self.assertTrue(kw["after_expiration"]["recovery"]["enabled"])
        self.assertEqual(kw["metadata"]["creator_id"], "3")


if __name__ == "__main__":
    unittest.main()
