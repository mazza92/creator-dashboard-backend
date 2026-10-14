"""Lifecycle Phase 1: Resend delivery, Polly-first activation, one win-back per dormancy, open/click webhook."""

import base64
import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lifecycle_email_engine as engine
from routes import resend_webhooks
from routes.resend_webhooks import record_engagement, verify_signature

SECRET = b'test-webhook-secret'


def _sign(body, msg_id='msg_1', ts=None, secret=SECRET):
    ts = str(int(ts if ts is not None else time.time()))
    sig = base64.b64encode(hmac.new(secret, f'{msg_id}.{ts}.{body}'.encode(), hashlib.sha256).digest()).decode()
    return msg_id, ts, f'v1,{sig}'


class TestDeliverEmail(unittest.TestCase):
    @patch('services.resend_mail.send_resend_email')
    @patch('services.resend_mail.resend_configured', return_value=True)
    def test_resend_with_tags_and_reply_to(self, _configured, send):
        send.return_value = {'success': True, 'message_id': 're_42', 'error': None, 'retryable': False}
        ok, msg_id = engine._deliver_email('a@b.com', 'Hi', '<p>x</p>', 'pitch_brand', 'https://u')
        self.assertTrue(ok)
        self.assertEqual(msg_id, 're_42')
        kwargs = send.call_args.kwargs
        self.assertIn({'name': 'template', 'value': 'pitch_brand'}, kwargs['tags'])
        self.assertIn({'name': 'category', 'value': 'lifecycle'}, kwargs['tags'])
        self.assertEqual(kwargs['reply_to'], 'team@newcollab.co')
        self.assertEqual(kwargs['unsubscribe_url'], 'https://u')

    @patch.object(engine, '_send_smtp', return_value=(True, '<smtp>'))
    @patch('services.resend_mail.resend_configured', return_value=False)
    def test_smtp_when_resend_not_configured(self, _configured, smtp):
        self.assertEqual(engine._deliver_email('a@b.com', 'Hi', '<p>x</p>', 'welcome_manager'), (True, '<smtp>'))
        smtp.assert_called_once()

    @patch.object(engine, 'SMTP_PASSWORD', 'set')
    @patch.object(engine, '_send_smtp', return_value=(True, '<smtp>'))
    @patch('services.resend_mail.send_resend_email')
    @patch('services.resend_mail.resend_configured', return_value=True)
    def test_smtp_fallback_only_on_retryable(self, _configured, send, smtp):
        send.return_value = {'success': False, 'message_id': None, 'error': '429', 'retryable': True}
        self.assertEqual(engine._deliver_email('a@b.com', 'Hi', '<p>x</p>', 'x'), (True, '<smtp>'))
        send.return_value = {'success': False, 'message_id': None, 'error': 'bad to', 'retryable': False}
        self.assertEqual(engine._deliver_email('a@b.com', 'Hi', '<p>x</p>', 'x'), (False, 'bad to'))
        self.assertEqual(smtp.call_count, 1)


class TestResendReplyTo(unittest.TestCase):
    @patch('services.resend_mail.requests.post')
    def test_reply_to_and_sender_name(self, post):
        from services.resend_mail import send_resend_email
        post.return_value = MagicMock(status_code=200)
        post.return_value.json.return_value = {'id': 're_1'}
        with patch.dict('os.environ', {'RESEND_API_KEY': 're_test'}, clear=True):
            send_resend_email('c@x.com', 'Hi', '<p>Hi</p>', reply_to='team@newcollab.co', from_name='Your Newcollab Manager')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['reply_to'], 'team@newcollab.co')
        self.assertEqual(payload['from'], 'Your Newcollab Manager <team@newcollab.co>')


class TestPollyActivation(unittest.TestCase):
    TEMPLATE = {
        'slug': 'meet_manager',
        'trigger_type': 'day_based',
        'trigger_conditions': {'condition': 'polly_never_opened', 'days_after_signup': 4},
    }

    def _creator(self, day=5):
        return {'id': 7, 'lifecycle_state': 'new', 'days_since_signup': day}

    def test_sends_when_polly_never_opened(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        self.assertTrue(engine.evaluate_trigger(self._creator(), self.TEMPLATE, cursor))
        self.assertIn("event = 'open'", cursor.execute.call_args.args[0])

    def test_skips_once_polly_opened(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        self.assertFalse(engine.evaluate_trigger(self._creator(), self.TEMPLATE, cursor))

    def test_outside_window(self):
        cursor = MagicMock()
        self.assertFalse(engine.evaluate_trigger(self._creator(day=12), self.TEMPLATE, cursor))


class TestPollyEmailGap(unittest.TestCase):
    def _send(self, slug, polly_recent):
        cursor = MagicMock()
        template = {'slug': slug, 'feature_flag': None, 'exempt_from_daily_cap': True,
                    'subject_template': 'Hi', 'body_template_file': 'lifecycle/04_meet_manager.html'}
        cursor.fetchone.side_effect = [template, (1,) if polly_recent else None, None]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(engine, 'get_db_connection', return_value=conn), \
                patch.object(engine, 'check_throttling', return_value=(True, '')), \
                patch.object(engine, '_deliver_email', return_value=(False, 'stop')) as deliver:
            result = engine.send_lifecycle_email('a@b.com', slug, {}, 7)
        return result, deliver

    def test_steps_aside_after_polly_email(self):
        (ok, msg), deliver = self._send('weekly_digest', polly_recent=True)
        self.assertFalse(ok)
        self.assertIn('Polly emailed', msg)
        deliver.assert_not_called()

    def test_quota_email_exempt(self):
        cursor = MagicMock()
        template = {'slug': 'max_quota_hit', 'feature_flag': None, 'exempt_from_daily_cap': True,
                    'subject_template': 'Hi', 'body_template_file': 'lifecycle/16_max_quota_hit.html'}
        cursor.fetchone.side_effect = [template, {'user_id': 3, 'approval_status': 'approved'}, None]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(engine, 'get_db_connection', return_value=conn), \
                patch.object(engine, 'check_throttling', return_value=(True, '')), \
                patch('services.quota_trial_test.active_arm', return_value='control'), \
                patch.object(engine, '_polly_emailed_recently') as polly_check, \
                patch.object(engine, '_deliver_email', return_value=(False, 'stop')) as deliver:
            engine.send_lifecycle_email('a@b.com', 'max_quota_hit', {}, 7)
        polly_check.assert_not_called()
        deliver.assert_called_once()


class TestReengagementOncePerDormancy(unittest.TestCase):
    TEMPLATE = {
        'slug': 'reengagement_new',
        'trigger_type': 'state_based',
        'trigger_conditions': {'state': 'dormant', 'days_dormant': 14},
    }

    def _creator(self):
        return {'id': 9, 'lifecycle_state': 'dormant', 'days_since_login': 40}

    def test_skips_when_already_sent_this_dormancy(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        self.assertFalse(engine.evaluate_trigger(self._creator(), self.TEMPLATE, cursor))
        self.assertIn('COALESCE(u.last_login, c.created_at)', cursor.execute.call_args.args[0])

    def test_sends_first_time(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        self.assertTrue(engine.evaluate_trigger(self._creator(), self.TEMPLATE, cursor))


class TestWeeklyDigestPicks(unittest.TestCase):
    def test_skips_handled_and_prioritises_active(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with patch.object(engine, 'get_db_connection', return_value=conn), \
                patch.object(engine, 'is_feature_enabled', return_value=True):
            engine.process_weekly_digest(limit=5, skip_day_check=True)
        sql, args = cursor.execute.call_args.args
        self.assertIn("s.template_slug = 'weekly_digest'", sql)
        self.assertIn("date_trunc('week', NOW())", sql)
        self.assertIn('FROM polly_email_log l', sql)
        self.assertIn("NOT IN ('pro', 'elite')", sql)
        self.assertIn('ORDER BY u.last_login DESC NULLS LAST, c.id', sql)
        self.assertEqual(args, (engine.POLLY_EMAIL_GAP_HOURS, engine.MAX_EMAILS_PER_DAY, 5))


class TestTemplateCopy(unittest.TestCase):
    def _render(self, name, **ctx):
        return engine.render_template(f'lifecycle/{name}', {'first_name': 'Ema', 'cta_url': 'https://x', **ctx})

    def test_activation_email_is_about_polly(self):
        html = self._render('04_meet_manager.html', unlocks_quota=3)
        self.assertIn("haven't met Polly yet", html)
        self.assertIn('first 3 pitches this month are free', html)
        self.assertNotIn('your plan', html)

    def test_weekly_digest_points_to_polly(self):
        html = self._render('21_weekly_digest.html', current_score=40, is_pro=False, credits_remaining=2,
                            credits_used=1, credits_quota=3, new_brands=[{'name': 'Kora', 'reason': 'fits you'}])
        self.assertIn("POLLY'S PICKS THIS WEEK", html)
        self.assertIn('Free pitches:', html)
        self.assertNotIn('Credits:', html)

    def test_welcome_drops_unlocks_and_guides(self):
        html = self._render('02_welcome_manager.html', unlocks_quota=3)
        self.assertNotIn('unlock', html.lower())
        self.assertNotIn('guide every', html)
        self.assertIn('3 free pitches a month', html)

    def test_reengagement_drops_unlock_line(self):
        html = self._render('19_reengagement_new.html', weeks_inactive=3, new_brands_count=12,
                            top_new_brands=[], unlocks_available=3, is_pro=False, reset_date='November 01')
        self.assertNotIn('unlock', html.lower())
        self.assertIn('3 free pitches this month', html)


class TestResendWebhook(unittest.TestCase):
    def test_signature_valid_invalid_and_stale(self):
        body = '{"type":"email.opened"}'
        msg_id, ts, sig = _sign(body)
        self.assertTrue(verify_signature(body.encode(), msg_id, ts, sig, SECRET))
        self.assertFalse(verify_signature(body.encode(), msg_id, ts, sig, b'other'))
        self.assertFalse(verify_signature((body + ' ').encode(), msg_id, ts, sig, SECRET))
        old_id, old_ts, old_sig = _sign(body, ts=time.time() - 3600)
        self.assertFalse(verify_signature(body.encode(), old_id, old_ts, old_sig, SECRET))

    def test_click_stamps_open_and_click(self):
        cursor = MagicMock(rowcount=1)
        self.assertEqual(record_engagement(cursor, 'email.clicked', 're_1', 'https://newcollab.co/x'), 1)
        sql, args = cursor.execute.call_args.args
        self.assertIn('clicked_at = COALESCE(clicked_at, NOW())', sql)
        self.assertIn('opened_at = COALESCE(opened_at, NOW())', sql)
        self.assertEqual(args, ('https://newcollab.co/x', 're_1'))

    def test_falls_back_to_polly_email_log(self):
        cursor = MagicMock()
        type(cursor).rowcount = property(lambda self: 1 if 'polly_email_log' in self.execute.call_args.args[0] else 0)
        self.assertEqual(record_engagement(cursor, 'email.opened', 're_7'), 1)
        tables = [c.args[0].split()[1] for c in cursor.execute.call_args_list]
        self.assertEqual(tables, ['lifecycle_email_sends', 'polly_email_log'])
        self.assertIn('WHERE message_id = %s', cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1], ('re_7',))

    def test_unknown_message_updates_nothing(self):
        cursor = MagicMock(rowcount=0)
        self.assertEqual(record_engagement(cursor, 'email.clicked', 're_x', 'https://x'), 0)
        self.assertEqual(cursor.execute.call_count, 2)

    def test_other_events_ignored(self):
        cursor = MagicMock()
        self.assertEqual(record_engagement(cursor, 'email.delivered', 're_1'), 0)
        cursor.execute.assert_not_called()

    def _client(self):
        from flask import Flask
        self.app = Flask(__name__)
        self.app.register_blueprint(resend_webhooks.resend_webhooks_bp)
        return self.app.test_client()

    def test_endpoint_rejects_bad_signature(self):
        secret_env = 'whsec_' + base64.b64encode(SECRET).decode()
        with patch.dict('os.environ', {'RESEND_WEBHOOK_SECRET': secret_env}):
            resp = self._client().post('/api/webhooks/resend', data='{}', headers={
                'svix-id': 'm', 'svix-timestamp': str(int(time.time())), 'svix-signature': 'v1,nope'})
        self.assertEqual(resp.status_code, 401)

    def test_endpoint_records_open(self):
        body = json.dumps({'type': 'email.opened', 'data': {'email_id': 're_9'}})
        msg_id, ts, sig = _sign(body)
        conn = MagicMock()
        conn.cursor.return_value = MagicMock(rowcount=1)
        secret_env = 'whsec_' + base64.b64encode(SECRET).decode()
        with patch.dict('os.environ', {'RESEND_WEBHOOK_SECRET': secret_env}), \
                patch.object(engine, 'get_db_connection', return_value=conn):
            resp = self._client().post('/api/webhooks/resend', data=body, headers={
                'svix-id': msg_id, 'svix-timestamp': ts, 'svix-signature': sig})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['updated'], 1)
        conn.commit.assert_called_once()

    def test_endpoint_unconfigured(self):
        with patch.dict('os.environ', {'RESEND_WEBHOOK_SECRET': ''}):
            resp = self._client().post('/api/webhooks/resend', data='{}')
        self.assertEqual(resp.status_code, 503)


class TestCheckoutEmailCampaign(unittest.TestCase):
    def test_logged_with_checkout(self):
        import subscription_routes
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = None
        with patch('services.polly_usage.log_usage') as log_usage:
            subscription_routes._log_checkout_source(
                conn, 5, {'source': 'polly', 'email_campaign': 'pitch_brand', 'interval': 'monthly'}, 'cs_1')
        self.assertEqual(log_usage.call_args.kwargs['meta']['email_campaign'], 'pitch_brand')


if __name__ == '__main__':
    unittest.main()
