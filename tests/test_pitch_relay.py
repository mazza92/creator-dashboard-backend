import os
import unittest
from unittest.mock import MagicMock, patch

from services import pitch_relay as pr
from services.polly_gmail import GmailError


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _conn(email="ana@outlook.com", send_via="newcollab"):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.side_effect = lambda: (email,) if cur.execute.call_args[0][0].lstrip().startswith("SELECT u.email") \
        else (send_via,)
    return conn


class RelaySendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"PITCH_RELAY_DOMAIN": "send.newcollab.co", "RESEND_API_KEY": "re_x"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_sends_as_creator_via_newcollab_with_reply_to_creator(self):
        http = MagicMock(return_value=_Resp(200, {"id": "abc"}))
        out = pr.send_email(_conn(), 42, "pr@brand.com", "Hi", "Body", from_name="Ana Lee", http=http)
        payload = http.call_args.kwargs["json"]
        self.assertEqual(payload["from"], "Ana Lee via Newcollab <ana.lee.42@send.newcollab.co>")
        self.assertEqual(payload["reply_to"], ["ana@outlook.com"])
        self.assertEqual(payload["text"], "Body")
        self.assertTrue(out["gmail_id"].startswith("relay:"))
        self.assertTrue(out["message_id"].endswith("@send.newcollab.co>"))

    def test_follow_up_threads_with_in_reply_to(self):
        http = MagicMock(return_value=_Resp(200, {"id": "abc"}))
        pr.send_email(_conn(), 42, "pr@brand.com", "Re: Hi", "Body", from_name="Ana", in_reply_to="<m1@x>", http=http)
        headers = http.call_args.kwargs["json"]["headers"]
        self.assertEqual(headers["In-Reply-To"], "<m1@x>")

    def test_error_from_resend_raises_gmail_error(self):
        http = MagicMock(return_value=_Resp(422, {"message": "bad"}))
        with self.assertRaises(GmailError):
            pr.send_email(_conn(), 42, "pr@brand.com", "Hi", "Body", http=http)

    def test_off_without_domain(self):
        with patch.dict(os.environ, {"PITCH_RELAY_DOMAIN": ""}):
            self.assertFalse(pr.relay_available())
            with self.assertRaises(GmailError):
                pr.send_email(_conn(), 42, "pr@brand.com", "Hi", "Body")


class DispatchTests(unittest.TestCase):
    def test_gmail_connected_uses_gmail(self):
        with patch("services.polly_gmail.load_account", return_value={"email": "a@gmail.com", "revoked_at": None}), \
                patch("services.polly_gmail.send_email", return_value={"gmail_id": "g"}) as gsend, \
                patch.object(pr, "send_email") as rsend:
            pr.send_for_creator(MagicMock(), 1, "pr@b.com", "s", "b")
            gsend.assert_called_once()
            rsend.assert_not_called()

    def test_no_gmail_and_opted_in_uses_relay(self):
        with patch("services.polly_gmail.load_account", return_value=None), \
                patch.object(pr, "send_via", return_value="newcollab"), \
                patch.object(pr, "send_email", return_value={"gmail_id": "relay:1"}) as rsend:
            pr.send_for_creator(MagicMock(), 1, "pr@b.com", "s", "b")
            rsend.assert_called_once()

    def test_no_gmail_not_opted_in_raises(self):
        with patch("services.polly_gmail.load_account", return_value=None), \
                patch.object(pr, "send_via", return_value="gmail"):
            with self.assertRaises(GmailError):
                pr.send_for_creator(MagicMock(), 1, "pr@b.com", "s", "b")


if __name__ == "__main__":
    unittest.main()
