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
    cur = Cur([None, {"x": 1}])
    assert qt.checkout_trial_eligible(cur, 42) is True
    assert "JOIN ugc_supply l" in cur.sql[1] and "stripe_customer_id IS NULL" in cur.sql[1]


def test_other_creators_keep_the_experiment_rules(monkeypatch):
    monkeypatch.setattr(qt, "active_arm", lambda _cid: "control")
    assert qt.checkout_trial_eligible(Cur([None, None]), 42) is False


def test_trial_offer_link_grants_the_trial_without_matching_email(monkeypatch):
    monkeypatch.setattr(qt, "active_arm", lambda _cid: "control")
    cur = Cur([{"t": "creator_signup_offers"}, {"x": 1}])
    assert qt.checkout_trial_eligible(cur, 42) is True
    assert "JOIN creator_signup_offers" in cur.sql[1]


def test_missing_offer_table_is_not_an_error(monkeypatch):
    monkeypatch.setattr(qt, "active_arm", lambda _cid: "control")
    cur = Cur([{"t": None}, None])
    assert qt.checkout_trial_eligible(cur, 42) is False
    assert not any("JOIN creator_signup_offers" in q for q in cur.sql)


def test_offer_recorder_ignores_unknown_refs():
    from services import creator_offers as co

    class Conn:
        def cursor(self):
            raise AssertionError("no DB call for unknown refs")
    assert co.record_offer(Conn(), 7, "blog_widget") is False
