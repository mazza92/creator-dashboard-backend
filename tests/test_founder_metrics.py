import unittest
from unittest.mock import MagicMock

from services import founder_metrics as fm


class TestMilestones(unittest.TestCase):
    def test_stage_is_next_rung(self):
        m = fm.milestones(701, 19)
        self.assertEqual(m["goal"], 1000)
        self.assertEqual(m["previous"], 500)
        self.assertEqual(m["next_after"], 2000)
        self.assertEqual(m["subs_needed"], 16)
        self.assertEqual(m["scale"], [0, 250, 500, 750, 1000])
        self.assertEqual(m["ultimate"], 10000)
        self.assertEqual(m["ultimate_pct"], 7.0)
        self.assertEqual(m["ultimate_subs_needed"], 490)

    def test_moves_up_when_rung_reached(self):
        self.assertEqual(fm.milestones(1000, 19)["goal"], 2000)
        self.assertEqual(fm.milestones(1000, 19)["scale"], [0, 500, 1000, 1500, 2000])
        self.assertEqual(fm.milestones(0, 19)["goal"], 100)
        top = fm.milestones(12000, 19)
        self.assertEqual(top["goal"], 10000)
        self.assertEqual(top["progress_pct"], 100)
        self.assertEqual(top["subs_needed"], 0)


class TestPace(unittest.TestCase):
    def test_months_to_goals(self):
        p = fm.pace(12, 2, 701, 1000, 19)
        self.assertEqual(p["net_mrr_30d"], 190)
        self.assertEqual(p["months_to_goal"], 1.6)
        self.assertEqual(p["months_to_ultimate"], 48.9)

    def test_not_growing(self):
        p = fm.pace(1, 3, 701, 1000, 19)
        self.assertIsNone(p["months_to_goal"])
        self.assertIsNone(p["months_to_ultimate"])


def _trial(status, **kw):
    row = {"creator_id": 1, "username": "a", "status": status, "trial_ends": "2026-10-21",
           "ended": status != "trialing", "cancel_scheduled": False, "gmail": True, "sent": 0}
    row.update(kw)
    return row


class TestTrials(unittest.TestCase):
    def test_early_trials_use_benchmark(self):
        rows = [_trial("trialing", sent=2), _trial("trialing", cancel_scheduled=True),
                _trial("trialing", gmail=False), _trial("active")]
        s = fm.summarize_trials(rows, 19)
        self.assertEqual(s["active"], 3)
        self.assertEqual(s["cancel_scheduled"], 1)
        self.assertEqual(s["gmail_connected"], 2)
        self.assertEqual(s["sending"], 1)
        self.assertEqual(s["converted"], 1)
        self.assertEqual(s["conversion_rate"], 100.0)
        self.assertEqual(s["expected_rate_basis"], "benchmark")
        self.assertEqual(s["pipeline_mrr"], 38)
        self.assertEqual(s["expected_mrr"], 19)
        self.assertEqual(s["mrr_from_trials"], 19)

    def test_observed_rate_after_enough_ended(self):
        rows = [_trial("active")] * 3 + [_trial("canceled")] + [_trial("past_due")] + [_trial("trialing")]
        s = fm.summarize_trials(rows, 19)
        self.assertEqual(s["ended"], 5)
        self.assertEqual(s["conversion_rate"], 60.0)
        self.assertEqual(s["expected_rate_basis"], "observed")
        self.assertEqual(s["payment_failing"], 1)
        self.assertEqual(s["lost"], 1)
        self.assertEqual(s["expected_mrr"], 11)

    def test_cancel_flag_comes_from_stripe(self):
        cur = MagicMock()
        cur.fetchall.return_value = [_trial("trialing", stripe_subscription_id="sub_a"),
                                     _trial("trialing", stripe_subscription_id="sub_b")]
        rows = fm.trial_rows(cur, "2026-01-01", {"sub_a": True, "sub_b": False})
        self.assertEqual([r["cancel_scheduled"] for r in rows], [True, False])
        self.assertNotIn("stripe_subscription_id", rows[0])
        self.assertFalse(fm.trial_rows(cur, "2026-01-01", None)[0]["cancel_scheduled"])

    def test_founder_metrics_shape(self):
        cur = MagicMock()
        cur.fetchone.side_effect = [{"new_paid": 10, "churned": 1}, {"n": 3}]
        cur.fetchall.side_effect = [[_trial("trialing", started="2026-10-14")], [_trial("trialing")]]
        out = fm.founder_metrics(cur, 701, 19, "2026-10-08")
        self.assertEqual(out["milestones"]["goal"], 1000)
        self.assertEqual(out["pace"]["net_subs_30d"], 9)
        self.assertEqual(out["trials"]["period"], {"started": 1, "paid_direct": 3, "share_pct": 25})
        self.assertEqual(out["trials"]["mrr_with_pipeline"], 720)


if __name__ == "__main__":
    unittest.main()
