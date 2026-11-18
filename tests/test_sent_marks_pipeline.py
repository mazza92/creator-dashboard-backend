"""A pitch sent through Polly (Gmail batch / single, 'Did it send? Yes') shows as pitched on the Tracker."""
import unittest
from unittest.mock import MagicMock, patch

from services import polly_tracker as pt


class MarkPitched(unittest.TestCase):
    def test_sent_pitch_marks_pipeline(self):
        conn = MagicMock()
        with patch.object(pt, "lookup_brand", return_value={"id": 63, "name": "Girlfriend Collective"}), \
             patch.object(pt, "_open_tasks", return_value=[]), \
             patch.object(pt, "create_task", return_value=1), \
             patch.object(pt, "add_event"), \
             patch.object(pt, "mark_pipeline_pitched") as mark:
            pt.record_pitch_sent(conn, 4529, {"id": 63}, drafted=False)
        mark.assert_called_once_with(conn, 4529, 63)

    def test_drafted_pitch_does_not(self):
        conn = MagicMock()
        with patch.object(pt, "lookup_brand", return_value={"id": 63, "name": "X"}), \
             patch.object(pt, "_open_tasks", return_value=[]), \
             patch.object(pt, "create_task", return_value=1), \
             patch.object(pt, "add_event"), \
             patch.object(pt, "mark_pipeline_pitched") as mark:
            pt.record_pitch_sent(conn, 4529, {"id": 63}, drafted=True)
        mark.assert_not_called()

    def test_upsert_never_moves_a_later_stage_back(self):
        conn = MagicMock()
        pt.mark_pipeline_pitched(conn, 1, 2)
        sql = conn.cursor.return_value.execute.call_args[0][0]
        self.assertIn("ON CONFLICT (creator_id, brand_id)", sql)
        self.assertIn("WHEN COALESCE(creator_pipeline.stage, 'saved') = 'saved' THEN 'pitched'", sql)


if __name__ == "__main__":
    unittest.main()
