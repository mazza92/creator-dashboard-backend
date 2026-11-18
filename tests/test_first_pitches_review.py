"""First-pitches review: swap one pitch for another brand, and inline edits saved before sending."""
import unittest
from unittest.mock import MagicMock, patch

from services import polly_tools as pt
from services import unlock_quota as uq
from tests.test_first_session_wow import _state


class Swap(unittest.TestCase):
    def test_swap_replaces_one_pitch_and_keeps_the_others(self):
        stored = [{"brand_id": 1, "brand_name": "A", "email": "a@a.com", "subject": "S1", "body": "My edited A"},
                  {"brand_id": 2, "brand_name": "B", "email": "b@b.com", "subject": "S2", "body": "Hi B"},
                  {"brand_id": 3, "brand_name": "C", "email": "c@c.com", "subject": "S3", "body": "Hi C"}]
        state = _state(notes={"pitch_batch": {"at": "x", "items": [dict(i) for i in stored]}})

        def fake_draft(st, args):
            st.pitch = {"brand_id": args["brand_id"], "brand_name": "D", "email": "d@d.com",
                        "subject": "S4", "body": "Hi D"}

        routes = MagicMock()
        routes._invoke_generate_pr_packages.return_value = {4: ("ok", 4)}
        routes._unlock_balance.return_value = {"is_unlimited": True}
        find = lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": f"B{brand_id}"}
        with patch.object(pt, "_routes", return_value=routes), \
             patch.object(pt, "find_brand", side_effect=find), \
             patch.object(pt, "_pitch_location", return_value=({}, "")), \
             patch.object(pt, "draft_pitch", side_effect=fake_draft), \
             patch("services.unlock_quota.release_unsent", return_value=True) as release:
            out = pt.draft_pitch_batch(state, {"brand_ids": [4], "replace": 2})
        self.assertEqual(out["status"], "swapped")
        release.assert_called_once()
        ids = [i["brand_id"] for i in state.notes["pitch_batch"]["items"]]
        self.assertEqual(ids, [1, 4, 3])
        self.assertEqual(state.notes["pitch_batch"]["items"][0]["body"], "My edited A")
        self.assertEqual([i["brand_id"] for i in state.pitch_batch], [1, 4, 3])
        self.assertIn("Swapped **B** for **D**", state.final_say)

    def test_swap_of_sent_pitch_is_refused(self):
        state = _state(notes={"pitch_batch": {"items": [{"brand_id": 2, "sent_at": "now"}]}})
        find = lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": "X"}
        with patch.object(pt, "_routes"), patch.object(pt, "find_brand", side_effect=find):
            out = pt.draft_pitch_batch(state, {"brand_ids": [4], "replace": 2})
        self.assertEqual(out["status"], "swap_missing")


class Release(unittest.TestCase):
    def test_only_pending_pitches_give_credit_back(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"ok": True}
        cur.rowcount = 0
        self.assertFalse(uq.release_unsent(cur, 1, 2))
        cur.rowcount = 1
        self.assertTrue(uq.release_unsent(cur, 1, 2))
        sqls = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
        self.assertIn("DELETE FROM brand_unlocks", sqls)
        self.assertIn("DELETE FROM pr_packages", sqls)


class Edit(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from routes import polly as P
        self.P = P
        app = Flask(__name__)
        app.register_blueprint(P.polly_bp, url_prefix="/api/polly")
        self.client = app.test_client()

    def test_saves_edit_on_unsent_item(self):
        notes = {"pitch_batch": {"items": [{"brand_id": 5, "body": "old"}, {"brand_id": 6, "body": "x", "sent_at": "t"}]}}
        conn = MagicMock()
        with patch.object(self.P, "_creator_auth", return_value=(9, conn, {})), \
             patch("services.polly_memory.load_thread", return_value={"notes": notes}), \
             patch("services.polly_memory.save_notes") as save:
            ok = self.client.post("/api/polly/pitch-batch/edit", json={"brand_id": 5, "body": "new words"})
            sent = self.client.post("/api/polly/pitch-batch/edit", json={"brand_id": 6, "body": "nope"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(save.call_args[0][2]["pitch_batch"]["items"][0]["body"], "new words")
        self.assertEqual(sent.status_code, 404)


if __name__ == "__main__":
    unittest.main()
