"""Pro conversion emails: paywall follow-up, Pro line for free users, free-limit trial test."""

import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lifecycle_email_engine as engine
from services import polly_alerts
from services import quota_trial_test as qt
from services.polly_alerts import kit_view_email, nudge_email, paywall_followup_email

QUOTA_TEMPLATE = {'slug': 'max_quota_hit', 'feature_flag': None, 'exempt_from_daily_cap': True,
                  'subject_template': 'Hi', 'body_template_file': 'lifecycle/16_max_quota_hit.html'}


class TestPaywallFollowupEmail(unittest.TestCase):
    def test_copy_follows_the_moment(self):
        self.assertEqual(paywall_followup_email("Ana", "pitch", "Glow Co")["subject"],
                         "Your Glow Co pitch is one tap away")
        self.assertIn("Glow Co", paywall_followup_email("Ana", "paid_reply", "Glow Co")["subject"])
        self.assertIn("more brands", paywall_followup_email("Ana", "credits_out")["subject"])
        self.assertEqual(paywall_followup_email("Ana", "pitch")["subject"], "Want me to run your outreach?")

    def test_cta_opens_pro_with_campaign(self):
        html = paywall_followup_email("Ana", "after_send", "Glow Co")["html"]
        self.assertIn("upgrade=pro", html)
        self.assertIn("utm_medium=polly_alert", html)
        self.assertIn("utm_campaign=paywall_after_send", html)
        self.assertIn("$19/mo", html)

    def test_brand_name_escaped(self):
        html = paywall_followup_email("Ana", "pitch", "<b>X</b>")["html"]
        self.assertNotIn("<b>X</b>", html)

    def test_skips_pro_and_logs_send(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [
            {"creator_id": 1, "moment": "pitch", "brand_id": "9", "brand_name": "Glow Co"},
            {"creator_id": 2, "moment": "credits_out", "brand_id": None, "brand_name": None},
        ]
        recipients = {
            1: {"user_id": 11, "email": "a@x.co", "first": "Ana", "is_pro": False},
            2: {"user_id": 12, "email": "b@x.co", "first": "Bo", "is_pro": True},
        }
        send = MagicMock(return_value={"success": True, "message_id": "re_1"})
        with patch.object(polly_alerts, "ensure_alert_tables"), \
                patch.object(polly_alerts, "_cursor", return_value=cur), \
                patch.object(polly_alerts, "_recipient", side_effect=lambda c, cid: recipients[cid]), \
                patch.object(polly_alerts, "_unsubscribe_url", return_value=""), \
                patch.object(polly_alerts, "_log_email") as log:
            out = polly_alerts.email_paywall_followups(conn, send_fn=send)
        self.assertEqual([o["creator_id"] for o in out], [1])
        send.assert_called_once()
        self.assertEqual(log.call_args.args[1:4], (1, "paywall_followup", 9))

    def test_query_respects_quota_email_and_holdout(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        with patch.object(polly_alerts, "ensure_alert_tables"), \
                patch.object(polly_alerts, "_cursor", return_value=cur):
            polly_alerts.email_paywall_followups(conn, dry_run=True)
        sql = cur.execute.call_args.args[0]
        self.assertIn("'holdout'", sql)
        self.assertIn("'credits_out'", sql)
        self.assertIn("checkout_completed", sql)
        self.assertIn("paywall_followup", sql)

    def test_in_alert_cron(self):
        import inspect
        self.assertIn("email_paywall_followups", inspect.getsource(polly_alerts.run_alerts))


class TestProLineForFreeUsers(unittest.TestCase):
    def test_kit_view_free_gets_pro_link(self):
        html = kit_view_email("Ana", "Glow Co", 9, is_pro=False, category="beauty")["html"]
        self.assertIn("utm_campaign=kit_view_pro", html)
        self.assertNotIn("Glow Co", html)

    def test_kit_view_pro_has_no_pitch(self):
        self.assertNotIn("kit_view_pro", kit_view_email("Ana", "Glow Co", 9, is_pro=True)["html"])

    def test_reply_needed_free_gets_pro_link(self):
        nudge = {"key": "reply_needed", "brand_name": "Glow Co", "brand_id": 9, "message": "They replied."}
        self.assertIn("reply_needed_pro", nudge_email("Ana", nudge, is_pro=False)["html"])
        self.assertNotIn("reply_needed_pro", nudge_email("Ana", nudge, is_pro=True)["html"])

    def test_other_nudges_unchanged(self):
        nudge = {"key": "content_due", "brand_name": "Glow Co", "brand_id": 9, "message": "Film it."}
        self.assertNotIn("_pro", nudge_email("Ana", nudge, is_pro=False)["html"].split("utm_campaign=")[-1][:20])


class TestQuotaTrialArms(unittest.TestCase):
    def test_deterministic_and_split(self):
        self.assertEqual(qt.quota_trial_arm(42), qt.quota_trial_arm(42))
        counts = Counter(qt.quota_trial_arm(i) for i in range(1, 10001))
        self.assertAlmostEqual(counts["holdout"] / 10000, 0.10, delta=0.02)
        self.assertAlmostEqual(counts["trial"] / 10000, 0.45, delta=0.03)

    def test_flag_off_means_control(self):
        with patch.object(qt, "experiment_on", return_value=False):
            self.assertEqual({qt.active_arm(i) for i in range(1, 200)}, {"control"})

    def test_trial_eligible_needs_trial_arm(self):
        cursor = MagicMock()
        with patch.object(qt, "active_arm", return_value="control"):
            self.assertFalse(qt.trial_eligible(cursor, 5))
        cursor.execute.assert_not_called()
        cursor.fetchone.return_value = (1,)
        with patch.object(qt, "active_arm", return_value="trial"):
            self.assertTrue(qt.trial_eligible(cursor, 5))
        sql = cursor.execute.call_args.args[0]
        self.assertIn("max_quota_hit_trial", sql)
        self.assertIn("subscription_started_at IS NULL", sql)


class TestQuotaEmailArms(unittest.TestCase):
    def _send(self, arm, paywall_sent=False):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [QUOTA_TEMPLATE, None, {'user_id': 3, 'approval_status': 'approved'},
                                       (1,) if paywall_sent else None]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(engine, 'get_db_connection', return_value=conn), \
                patch.object(engine, 'check_throttling', return_value=(True, '')), \
                patch('services.quota_trial_test.active_arm', return_value=arm), \
                patch.object(engine, 'render_template', return_value='<p>x</p>') as render, \
                patch.object(engine, '_deliver_email', return_value=(False, 'stop')) as deliver:
            result = engine.send_lifecycle_email('a@b.com', 'max_quota_hit', {'cta_url': 'https://app/x'}, 7,
                                                 dedup_key='k')
        return result, cursor, render, deliver

    def test_holdout_logs_without_sending(self):
        (ok, msg), cursor, _render, deliver = self._send('holdout')
        self.assertFalse(ok)
        self.assertIn('holdout', msg)
        deliver.assert_not_called()
        insert = cursor.execute.call_args_list[-1].args
        self.assertIn("'holdout'", insert[0])
        self.assertIn('max_quota_hit_holdout', insert[1])

    def test_trial_arm_gets_trial_copy_and_campaign(self):
        _result, _cursor, render, deliver = self._send('trial')
        ctx = render.call_args.args[1]
        self.assertEqual(ctx['quota_arm'], 'trial')
        self.assertIn('utm_campaign=max_quota_hit_trial', ctx['cta_url'])
        self.assertIn('free for 7 days', ctx['preheader'])
        deliver.assert_called_once()

    def test_skips_when_paywall_followup_sent(self):
        (ok, msg), _cursor, _render, deliver = self._send('control', paywall_sent=True)
        self.assertFalse(ok)
        self.assertIn('paywall follow-up', msg)
        deliver.assert_not_called()

    def test_template_trial_copy(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(ROOT / 'templates')))
        tpl = env.get_template('lifecycle/16_max_quota_hit.html')
        base = {'first_name': 'Ana', 'days_until_reset': 10, 'reset_date': 'Nov 1', 'cta_url': 'https://x'}
        self.assertIn('Start my free week', tpl.render(quota_arm='trial', **base))
        control = tpl.render(quota_arm='control', **base)
        self.assertIn('Put Polly on autopilot', control)
        self.assertNotIn('free week', control)


class TestCheckoutTrial(unittest.TestCase):
    def _checkout(self, eligible, offer=''):
        from flask import Flask
        import subscription_routes as sr

        self.app = Flask(__name__)
        self.app.register_blueprint(sr.subscription_bp, url_prefix='/api/subscription')
        cursor = MagicMock()
        cursor.fetchone.return_value = {'email': 'a@b.com', 'name': 'ana', 'subscription_tier': 'free',
                                        'subscription_status': None, 'stripe_subscription_id': None,
                                        'stripe_customer_id': None}
        conn = MagicMock()
        conn.cursor.return_value = cursor
        session = MagicMock(url='https://stripe/x', id='cs_1')
        with patch.object(sr, 'get_creator_id_from_session', return_value=5), \
                patch.object(sr, 'get_db_connection', return_value=conn), \
                patch('services.quota_trial_test.trial_eligible', return_value=eligible), \
                patch('services.subscription_retention.qualifies_for_winback_coupon', return_value=False), \
                patch.dict('os.environ', {'STRIPE_PRICE_ID_PRO': 'price_x'}), \
                patch.object(sr.stripe.checkout.Session, 'create', return_value=session) as create:
            resp = self.app.test_client().post('/api/subscription/create-checkout',
                                               json={'tier': 'pro', 'offer': offer})
        return resp, create

    def test_trial_added_when_eligible(self):
        resp, create = self._checkout(True)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['subscription_data'], {'trial_period_days': 7})
        self.assertEqual(kwargs['metadata']['trial'], '7')
        self.assertEqual(resp.get_json()['trial_days'], 7)

    def test_no_trial_otherwise(self):
        _resp, create = self._checkout(False)
        kwargs = create.call_args.kwargs
        self.assertNotIn('subscription_data', kwargs)
        self.assertNotIn('trial', kwargs['metadata'])


class TestTrialOfferInApp(unittest.TestCase):
    def _get(self, eligible, creator_id=5):
        from flask import Flask
        import subscription_routes as sr

        self.app = Flask(__name__)
        self.app.register_blueprint(sr.subscription_bp, url_prefix='/api/subscription')
        with patch.object(sr, 'get_creator_id_from_session', return_value=creator_id), \
                patch.object(sr, 'get_db_connection', return_value=MagicMock()), \
                patch('services.quota_trial_test.trial_eligible', return_value=eligible) as check:
            resp = self.app.test_client().get('/api/subscription/trial-offer')
        return resp.get_json(), check

    def test_endpoint_uses_checkout_check(self):
        self.assertEqual(self._get(True)[0], {'trial_days': 7})
        self.assertEqual(self._get(False)[0], {'trial_days': 0})

    def test_endpoint_logged_out(self):
        body, check = self._get(True, creator_id=None)
        self.assertEqual(body, {'trial_days': 0})
        check.assert_not_called()

    def test_credits_out_message_trial_copy(self):
        from services.polly import credits_out_offer_message
        balance = {"remaining": 0, "reset_at": "2026-11-01T00:00:00+00:00"}
        trial = credits_out_offer_message("Ana", balance, None, {}, trial=True)
        self.assertIn("Try Pro **free for 7 days**", trial["content"])
        self.assertEqual(trial["task_chips"][0]["label"], "Start my free week")
        control = credits_out_offer_message("Ana", balance, None, {})
        self.assertIn("Pro is **$19/mo** and I work as your manager", control["content"])
        self.assertNotIn("free for 7 days", control["content"])
        self.assertEqual(control["task_chips"][0]["label"], "Put Polly to work · $19/mo")


class TestCancelClearsUnlockTier(unittest.TestCase):
    def _webhook(self, event):
        from flask import Flask
        import subscription_routes as sr

        self.app = Flask(__name__)
        self.app.register_blueprint(sr.subscription_bp, url_prefix='/api/subscription')
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(sr, 'get_db_connection', return_value=conn), \
                patch.dict('os.environ', {'STRIPE_WEBHOOK_SECRET_SUBSCRIPTION': 'whsec'}), \
                patch.object(sr.stripe.Webhook, 'construct_event', return_value=event):
            self.app.test_client().post('/api/subscription/webhook', data=b'{}',
                                        headers={'Stripe-Signature': 'x'})
        return ' '.join(c.args[0] for c in cursor.execute.call_args_list)

    def test_deleted_clears_both_tiers(self):
        sql = self._webhook({'type': 'customer.subscription.deleted', 'data': {'object': {'id': 'sub_1'}}})
        self.assertIn("subscription_tier = 'free'", sql)
        self.assertIn("unlocks_tier = 'free'", sql)

    def test_lapsed_update_clears_both_tiers(self):
        sql = self._webhook({'type': 'customer.subscription.updated',
                             'data': {'object': {'id': 'sub_1', 'status': 'canceled'}}})
        self.assertIn("unlocks_tier = 'free'", sql)


if __name__ == '__main__':
    unittest.main()
