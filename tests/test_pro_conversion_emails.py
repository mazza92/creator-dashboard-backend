"""Pro conversion emails: paywall follow-up, Pro line for free users, free-limit trial test."""

import sys
import unittest
from collections import Counter
from datetime import datetime, timezone
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
        with patch.object(qt, "active_arm", return_value="trial"), \
                patch("services.unlock_quota.count_delivered_unlocks_all_time", return_value=3):
            self.assertTrue(qt.trial_eligible(cursor, 5))
        sql = cursor.execute.call_args.args[0]
        self.assertIn("subscription_started_at IS NULL", sql)
        self.assertIn("stripe_customer_id IS NULL", sql)
        self.assertIn("created_at <=", sql)
        self.assertEqual(cursor.execute.call_args.args[1], (5, qt.MIN_ACCOUNT_AGE_DAYS))

    def test_trial_needs_real_usage(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        with patch.object(qt, "active_arm", return_value="trial"), \
                patch("services.unlock_quota.count_delivered_unlocks_all_time", return_value=2):
            self.assertFalse(qt.trial_eligible(cursor, 5))

    def test_trial_skips_paid_or_new_accounts(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch.object(qt, "active_arm", return_value="trial"), \
                patch("services.unlock_quota.count_delivered_unlocks_all_time", return_value=9) as count:
            self.assertFalse(qt.trial_eligible(cursor, 5))
        count.assert_not_called()


class TestQuotaEmailArms(unittest.TestCase):
    def _send(self, arm, paywall_sent=False, eligible=True):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [QUOTA_TEMPLATE, None, {'user_id': 3, 'approval_status': 'approved'},
                                       (1,) if paywall_sent else None]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(engine, 'get_db_connection', return_value=conn), \
                patch.object(engine, 'check_throttling', return_value=(True, '')), \
                patch('services.quota_trial_test.active_arm', return_value=arm), \
                patch('services.quota_trial_test.trial_eligible', return_value=eligible), \
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

    def test_trial_arm_not_yet_eligible_gets_control_copy(self):
        _result, _cursor, render, _deliver = self._send('trial', eligible=False)
        ctx = render.call_args.args[1]
        self.assertEqual(ctx['quota_arm'], 'control')
        self.assertIn('utm_campaign=max_quota_hit_control', ctx['cta_url'])

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


class TestTrialJourney(unittest.TestCase):
    ENDS = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)

    def test_started_email_sets_expectations(self):
        from services.pro_trial import trial_started_email
        html = trial_started_email("Ana", self.ENDS)["html"]
        self.assertIn("Connect Gmail", html)
        self.assertIn("Nothing to approve", html)
        self.assertIn("Wednesday, Oct 21", html)
        self.assertIn("autopilot=pro_started", html)

    def test_ending_email_recaps_and_states_charge(self):
        from services.pro_trial import trial_ending_email
        mail = trial_ending_email("Ana", self.ENDS, {"sent": 5, "followups": 2, "replies": 1, "kit_views": 3,
                                                     "lined_up": 4, "gmail": True})
        self.assertEqual(mail["subject"], "Your free week: 5 pitches sent")
        self.assertIn("1 brand</strong> replied", mail["html"])
        self.assertIn("$19/mo", mail["html"])
        self.assertIn("Cancel in Settings", mail["html"])
        self.assertIn("Manage subscription", mail["html"])

    def test_ending_email_says_what_stops_on_cancel(self):
        from services.pro_trial import trial_ending_email
        mail = trial_ending_email("Ana", self.ENDS, {"sent": 4, "followups_due": 3, "lined_up": 6, "gmail": True})
        self.assertIn("If you cancel, I stop: <strong>the 3 follow-ups already scheduled and the 6 pitches "
                      "lined up</strong> won't go out, so the brands I already pitched", mail["html"])
        quiet = trial_ending_email("Ana", self.ENDS, {"sent": 0, "lined_up": 1, "gmail": True})["html"]
        self.assertIn("<strong>the 1 pitch lined up</strong> won't go out.", quiet)
        none = trial_ending_email("Ana", self.ENDS, {"sent": 2, "gmail": True})["html"]
        self.assertNotIn("If you cancel, I stop", none)

    def test_first_sent_email_names_brands(self):
        from services.pro_trial import trial_first_sent_email
        nxt = datetime(2026, 10, 16, 15, 10, tzinfo=timezone.utc)
        mail = trial_first_sent_email("Ana", self.ENDS, {"names": ["Glossier", "Tower 28"], "lined_up": 4,
                                                         "next_at": nxt})
        self.assertEqual(mail["subject"], "Your first pitches are out: Glossier and Tower 28")
        self.assertIn("sent 2 pitches from your Gmail to <strong>Glossier</strong>, <strong>Tower 28</strong>",
                      mail["html"])
        self.assertIn("4 more pitches are lined up, the next one on Friday, Oct 16", mail["html"])
        self.assertIn("Wednesday, Oct 21", mail["html"])
        one = trial_first_sent_email("Ana", self.ENDS, {"names": ["Rhode"]})
        self.assertEqual(one["subject"], "Your first pitch is out: Rhode")

    def test_first_sent_waits_for_send_window_to_close(self):
        from services.pro_trial import first_sent_ready_at
        self.assertEqual(first_sent_ready_at(datetime(2026, 10, 15, 14, 14, tzinfo=timezone.utc)),
                         datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(first_sent_ready_at(datetime(2026, 10, 15, 21, 30, tzinfo=timezone.utc)),
                         datetime(2026, 10, 15, 22, 30, tzinfo=timezone.utc))

    def test_ending_email_without_sends_pushes_gmail(self):
        from services.pro_trial import trial_ending_email
        mail = trial_ending_email("Ana", self.ENDS, {"sent": 0, "gmail": False})
        self.assertIn("haven't sent anything", mail["subject"])
        self.assertIn("Connect Gmail", mail["html"])

    def _journey(self, started_ago, gmail=False, nudged=False, reminded=False, recipient=True,
                 first_sent_ago=None, told_sent=False):
        from services import pro_trial
        now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        cur = MagicMock()
        cur.fetchall.return_value = [{"creator_id": 4, "started": now - started_ago, "gmail": gmail,
                                      "nudged": nudged, "reminded": reminded, "told_sent": told_sent,
                                      "first_sent": now - first_sent_ago if first_sent_ago else None}]
        conn = MagicMock()
        conn.cursor.return_value = cur
        who = {"user_id": 9, "email": "a@x.co", "first": "Ana", "is_pro": True} if recipient else None
        send = MagicMock(return_value={"success": True, "message_id": "re_1"})
        with patch.object(pro_trial, "_now", return_value=now), \
                patch.object(polly_alerts, "ensure_alert_tables"), \
                patch.object(polly_alerts, "_recipient", return_value=who), \
                patch.object(polly_alerts, "_recently_emailed", return_value=False), \
                patch.object(polly_alerts, "_unsubscribe_url", return_value=""), \
                patch.object(polly_alerts, "_log_email"), \
                patch.object(pro_trial, "_billing_recipient", return_value={"user_id": 9, "email": "a@x.co",
                                                                               "first": "Ana"}) as billing, \
                patch.object(pro_trial, "trial_facts", return_value={"sent": 2, "gmail": True}), \
                patch.object(pro_trial, "first_sent_facts", return_value={"names": ["Glossier"]}):
            out = pro_trial.email_trial_journey(conn, send_fn=send)
        return [o["kind"] for o in out], billing

    def test_gmail_nudge_day_one(self):
        from datetime import timedelta
        self.assertEqual(self._journey(timedelta(hours=30))[0], ["trial_gmail"])
        self.assertEqual(self._journey(timedelta(hours=30), gmail=True)[0], [])
        self.assertEqual(self._journey(timedelta(hours=30), nudged=True)[0], [])
        self.assertEqual(self._journey(timedelta(hours=10))[0], [])

    def test_ending_reminder_day_five_once(self):
        from datetime import timedelta
        self.assertEqual(self._journey(timedelta(days=5, hours=2), gmail=True)[0], ["trial_ending"])
        self.assertEqual(self._journey(timedelta(days=5, hours=2), reminded=True)[0], [])
        self.assertEqual(self._journey(timedelta(days=7, hours=1))[0], [])

    def test_first_sent_once_after_window_and_not_after_recap(self):
        from datetime import timedelta
        day2 = timedelta(days=2)
        self.assertEqual(self._journey(day2, gmail=True, first_sent_ago=timedelta(hours=20))[0],
                         ["trial_first_sent"])
        self.assertEqual(self._journey(day2, gmail=True, first_sent_ago=timedelta(minutes=10))[0], [])
        self.assertEqual(self._journey(day2, gmail=True, first_sent_ago=timedelta(hours=20), told_sent=True)[0], [])
        self.assertEqual(self._journey(timedelta(days=5, hours=2), gmail=True,
                                       first_sent_ago=timedelta(hours=20))[0], ["trial_ending"])
        self.assertEqual(self._journey(day2, gmail=True, first_sent_ago=timedelta(hours=20),
                                       recipient=False)[0], [])

    def test_ending_reminder_reaches_unsubscribed(self):
        from datetime import timedelta
        kinds, billing = self._journey(timedelta(days=5, hours=2), recipient=False)
        self.assertEqual(kinds, ["trial_ending"])
        billing.assert_called_once()
        self.assertEqual(self._journey(timedelta(hours=30), recipient=False)[0], [])

    def test_in_alert_cron(self):
        import inspect
        self.assertIn("email_trial_journey", inspect.getsource(polly_alerts.run_alerts))

    def test_checkout_webhook_sends_trial_welcome(self):
        from flask import Flask
        import subscription_routes as sr

        app = Flask(__name__)
        app.register_blueprint(sr.subscription_bp, url_prefix='/api/subscription')
        event = {'type': 'checkout.session.completed', 'data': {'object': {
            'metadata': {'creator_id': '5', 'tier': 'pro', 'trial': '7', 'interval': 'monthly'},
            'subscription': 'sub_1', 'customer': 'cus_1', 'amount_total': 0, 'id': 'cs_1'}}}
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(sr, 'get_db_connection', return_value=conn), \
                patch.dict('os.environ', {'STRIPE_WEBHOOK_SECRET_SUBSCRIPTION': 'whsec'}), \
                patch.object(sr.stripe.Webhook, 'construct_event', return_value=event), \
                patch.object(sr, 'send_ga4_event'), \
                patch.object(sr, 'send_pro_subscriber_notification'), \
                patch.object(sr, '_send_creator_resend') as activation, \
                patch('services.pro_trial.send_trial_started', return_value=True) as welcome:
            app.test_client().post('/api/subscription/webhook', data=b'{}', headers={'Stripe-Signature': 'x'})
        welcome.assert_called_once()
        self.assertEqual(welcome.call_args.args[1], 5)
        activation.assert_not_called()


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
