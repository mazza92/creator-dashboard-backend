from services import quota_trial_test as qt


class Cur:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_emailed_creator_gets_the_trial_without_arm_or_age_rules(monkeypatch):
    monkeypatch.setattr(qt, "active_arm", lambda _cid: "control")
    cur = Cur([{"x": 1}])
    assert qt.checkout_trial_eligible(cur, 42) is True
    assert "JOIN ugc_supply l" in cur.sql[0] and "stripe_customer_id IS NULL" in cur.sql[0]


def test_other_creators_keep_the_experiment_rules(monkeypatch):
    monkeypatch.setattr(qt, "active_arm", lambda _cid: "control")
    assert qt.checkout_trial_eligible(Cur([None]), 42) is False
