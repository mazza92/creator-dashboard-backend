import random

from services import brand_pipeline as bp


def _norm(sql):
    return " ".join((sql or "").split())


class FakeCursor:
    def __init__(self, handlers=None):
        self.handlers = [(_norm(k), v) for k, v in (handlers or [])]
        self.sql = []
        self._rows = []

    def execute(self, sql, params=None):
        sql = _norm(sql)
        self.sql.append((sql, params))
        self._rows = []
        for key, rows in self.handlers:
            if key in sql:
                self._rows = list(rows() if callable(rows) else rows)
                break

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def ran(self, key):
        return [p for s, p in self.sql if _norm(key) in s]


def _row(**over):
    row = {
        "brand_id": 7, "brand_name": "Kappel", "website": "https://www.mdaire.com", "category": "skincare",
        "brand_email": "hello@mdaire.com", "email_status": "valid", "pipeline": None, "stage": "new",
        "sequence_step": 0, "variant": None, "next_action": None, "next_action_at": None,
        "contact_email": "tacy@mdaire.com", "contact_name": "Tacy Bailey", "contact_role": "Brand Manager",
        "ref_code": "abc123", "thread_subject": None, "thread_message_id": None,
        "last_inbound_message_id": None, "last_inbound_subject": None, "reply_count": 0,
        "auto_reply_count": 0, "last_contacted_at": None, "outreach_count": 0,
    }
    row.update(over)
    return row


class Sender:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, to, subject, html_body, **kw):
        self.calls.append({"to": to, "subject": subject, "html": html_body, **kw})
        return {"success": self.ok, "message_id": f"<sent{len(self.calls)}@newcollab.co>"}


def no_llm(*_a, **_k):
    return {}


# --------------------------------------------------------------- classification on real replies


def test_rules_classify_real_export_replies():
    cases = [
        ("Re: PR + UGC swap for Kappel?", "Worth a look. Send me the details\n\nKind Regards,", "tacy@mdaire.com", "interested"),
        ("Re: PR + UGC swap for Gochi?", "Hey Mazza, thanks for reaching out sure, what do you need from me? and what's the pricing", "julian@gochi.store", "question"),
        ("Re: Quick UGC idea for Lumiara", "Do you have a media deck I could review or could we hop on a call sometime?", "olga@miamimd.co", "meeting"),
        ("Re: PR + UGC swap for Airyday?", "At the moment we're not looking to onboard any additional creator collaborations.", "hi@airyday.co", "not_interested"),
        ("PR + UGC swap for Dore & Rose?", "Please send your proposal to our marketing team at Anna@doreandrose.com. This email was automatically generated", "help@doreandrose.com", "redirect"),
        ("Automatic reply: 6 creators", "I am no longer with the business. For anything urgent please email fiona.jones@clubllondon.com", "x@clubllondon.com", "redirect"),
        ("PR + UGC swap for Humantra?", "For all partnership enquiries, we ask that you please fill out this form https://form.example/x", "hi@gethumantra.com", "redirect"),
        ("You're invited to join our Affiliate Programme!", "We'd love to invite you to apply to join our new 47 Skin Affiliate Programme", "hello@47skin.com", "affiliate"),
        ("PR + UGC swap for Dore & Rose?", "How would you rate the service you received? Take 10 seconds", "support@engaige.com", "auto_reply"),
        ("Delivery Status Notification (Failure)", "Address not found", "mailer-daemon@googlemail.com", "bounce"),
    ]
    for subject, body, sender, want in cases:
        got = bp.classify(subject, body, sender, llm=no_llm)
        assert got["intent"] == want, (subject, got)
    dore = bp.classify(cases[4][0], cases[4][1], cases[4][2], llm=no_llm)
    assert dore["redirect_email"] == "anna@doreandrose.com"


def test_llm_redirect_email_must_appear_in_the_reply():
    llm = lambda *_a, **_k: {"intent": "redirect", "confidence": 0.9, "redirect_email": "made.up@brand.com"}
    got = bp.classify("Re: x", "Please contact our marketing team.", "a@brand.com", llm=llm)
    assert got["intent"] == "redirect" and got["redirect_email"] == ""


def test_strip_quoted_keeps_only_their_words():
    body = "Sounds good, send details.\n\nOn Tue, Aug 4, 2026 Mazza <team@newcollab.co> wrote:\n> Hey,\n> We work with"
    assert bp.strip_quoted(body) == "Sounds good, send details."


# --------------------------------------------------------------- decisions + reply safety


def test_decide_hybrid_autonomy():
    row = _row(stage="contacted", pipeline="v2")
    assert bp.decide({"intent": "interested", "confidence": 0.85}, row) == "auto"
    assert bp.decide({"intent": "interested", "confidence": 0.5}, row) == "hold"
    assert bp.decide({"intent": "question", "confidence": 0.9, "flags": ["negotiation"]}, row) == "hold"
    assert bp.decide({"intent": "interested", "confidence": 0.9}, _row(auto_reply_count=1, pipeline="v2")) == "hold"
    legacy = _row(stage="replied", pipeline="legacy")
    assert bp.decide({"intent": "interested", "confidence": 0.95}, legacy) == "hold"
    assert bp.decide({"intent": "not_interested", "confidence": 0.9}, legacy) == "log"
    assert bp.decide({"intent": "other", "confidence": 0.9}, row) == "hold"
    assert bp.decide({"intent": "not_interested", "confidence": 0.9}, row) == "log"
    assert bp.decide({"intent": "redirect", "confidence": 0.8, "redirect_email": "a@b.com"}, row) == "auto"
    assert bp.decide({"intent": "interested", "confidence": 0.9}, _row(stage="won")) == "hold"


def test_reply_guard_blocks_invented_facts():
    link = bp.brief_link("skincare", "abc123")
    assert bp._safe_reply(f"Hi Tacy,\n\nHere you go: {link}\n\nMazza", "send details", link)
    assert not bp._safe_reply("Hi Tacy, no link here", "send details", link)
    assert not bp._safe_reply(f"Check @glowwithjen: {link}", "send details", link)
    assert not bp._safe_reply(f"Our creators average 37% engagement {link}", "send details", link)
    assert bp._safe_reply(f"Yes, creators aged 40+ are in the list {link}", "best creators aged 40 and above", link)


def test_compose_reply_falls_back_to_template_when_llm_is_unsafe():
    row = _row()
    link = bp.brief_link("skincare", "abc123")
    llm = lambda *_a, **_k: {"body": "Hi! We have @sarahglows with 92K followers."}
    text, source = bp.compose_reply(row, "What's the pricing? Are creators US-based?",
                                    {"intent": "question", "questions": ["pricing", "countries"]}, link, llm=llm)
    assert source == "template"
    assert text.startswith("Hi Tacy,")
    assert link in text and "product + shipping" in text
    assert "you only see creators in those countries" in text
    assert bp.BOOKING_URL in text


# --------------------------------------------------------------- sending


def test_initial_send_logs_variant_ref_and_schedules_day3():
    cur = FakeCursor([("bo.last_inbound_subject", [_row()]), ("AS positive", [])])
    sender = Sender()
    out = bp.send_step(cur, 7, "initial", sender=sender)
    assert out["ok"] and out["variant"] == "swap"
    call = sender.calls[0]
    assert call["to"] == "tacy@mdaire.com"
    assert call["subject"] == "PR + UGC swap for Kappel?"
    assert call["text_content"].startswith("Hey Tacy,")
    assert call["from_name"] == bp.FROM_NAME
    upd = cur.ran("SET outreach_count = COALESCE(outreach_count, 0) + 1, last_contacted_at = NOW(), last_subject = %s, pipeline")
    assert upd and upd[0][2] == "swap" and upd[0][3] == "abc123" and upd[0][6] == 3
    kinds = [p[1] for p in cur.ran("INSERT INTO brand_activity")]
    assert kinds == ["stage_change", "sent"]
    assert cur.ran("INSERT INTO brand_outreach_log")


def test_initial_send_refuses_brands_already_contacted():
    cur = FakeCursor([("bo.last_inbound_subject", [_row(stage="contacted", last_contacted_at="2026-10-01")])])
    out = bp.send_step(cur, 7, "initial", sender=Sender())
    assert not out["ok"]


def test_followup_threads_on_the_first_email():
    row = _row(pipeline="v2", stage="contacted", next_action="followup", sequence_step=0, variant="ads",
               thread_subject="UGC ads for Kappel, product + shipping only", thread_message_id="<first@newcollab.co>")
    cur = FakeCursor([("bo.last_inbound_subject", [row])])
    sender = Sender()
    out = bp.send_step(cur, 7, "followup", sender=sender)
    assert out["ok"] and out["step"] == 1
    call = sender.calls[0]
    assert call["subject"] == "Re: UGC ads for Kappel, product + shipping only"
    assert call["in_reply_to"] == "<first@newcollab.co>"
    assert "who handles creators" in call["text_content"]


def test_last_followup_moves_brand_to_nurture():
    row = _row(pipeline="v2", stage="contacted", next_action="followup", sequence_step=2, variant="swap",
               thread_subject="PR + UGC swap for Kappel?", thread_message_id="<first@newcollab.co>")
    cur = FakeCursor([("bo.last_inbound_subject", [row])])
    out = bp.send_step(cur, 7, "followup", sender=Sender())
    assert out["step"] == 3
    stages = [p[4] for p in cur.ran("INSERT INTO brand_activity") if p[1] == "stage_change"]
    assert stages == ["nurture"]


def test_pick_variant_balances_then_exploits_the_winner():
    few = FakeCursor([("AS positive", [{"variant": "swap", "sent": 90, "positive": 3},
                                       {"variant": "ads", "sent": 10, "positive": 0}])])
    assert bp.pick_variant(few) == "ambassador"
    full = [{"variant": "swap", "sent": 100, "positive": 2}, {"variant": "ads", "sent": 100, "positive": 9},
            {"variant": "ambassador", "sent": 100, "positive": 3}]

    class Always:
        def random(self):
            return 0.1

    assert bp.pick_variant(FakeCursor([("AS positive", full)]), rng=Always()) == "ads"


def test_queue_new_skips_domains_already_contacted_or_customers():
    taken = [{"website": "https://mdaire.com", "email": ""}, {"website": "", "email": "x@gmail.com"}]
    pool = [
        {"brand_id": 1, "brand_name": "Kappel", "website": "mdaire.com", "contact_email": "", "email_status": "valid", "category": "skincare", "source": "curated"},
        {"brand_id": 2, "brand_name": "Glow", "website": "https://www.glow.co", "contact_email": "", "email_status": "valid", "category": "beauty", "source": "curated"},
        {"brand_id": 3, "brand_name": "Glow US", "website": "glow.co/us", "contact_email": "", "email_status": "valid", "category": "beauty", "source": "curated"},
        {"brand_id": 4, "brand_name": "", "website": "https://shop.tidy-pets.com", "contact_email": "", "email_status": "", "category": "pet", "source": "awin"},
    ]
    cur = FakeCursor([("UNION ALL", taken), ("ORDER BY (CASE", pool)])
    got = bp.queue_new(cur, 40)
    assert [g["brand_id"] for g in got] == [2, 4]
    assert got[1]["brand_name"] == "Tidy Pets"


# --------------------------------------------------------------- inbound


def _inbound_cursor(row):
    return FakeCursor([
        ("kind = 'reply_in' AND message_id", []),
        ("TRIM(BOTH '<>'", [{"brand_id": 7}]),
        ("WHERE LOWER(contact_email) = %s", [{"brand_id": 7}]),
        ("bo.last_inbound_subject", [row]),
    ])


def test_interested_reply_gets_one_auto_answer_in_thread():
    row = _row(pipeline="v2", stage="contacted", next_action="followup", thread_message_id="<first@newcollab.co>")
    cur = _inbound_cursor(row)
    sender = Sender()
    msg = {"message_id": "<their1@mdaire.com>", "in_reply_to": "<first@newcollab.co>", "from_email": "tacy@mdaire.com",
           "from_name": "Tacy Bailey", "subject": "Re: PR + UGC swap for Kappel?", "body": "Worth a look. Send me the details"}
    llm = lambda system, *_a: ({"intent": "interested", "confidence": 0.9, "questions": []}
                               if "triage" in system else {})
    out = bp.ingest_message(cur, msg, sender=sender, llm=llm)
    assert out["action"] == "auto" and out["intent"] == "interested"
    call = sender.calls[0]
    assert call["to"] == "tacy@mdaire.com"
    assert call["subject"] == "Re: PR + UGC swap for Kappel?"
    assert call["in_reply_to"] == "<their1@mdaire.com>"
    assert "ref=abc123" in call["text_content"]
    kinds = [p[1] for p in cur.ran("INSERT INTO brand_activity")]
    assert kinds[0] == "reply_in" and "auto_reply_sent" in kinds
    assert cur.ran("SET auto_reply_count = COALESCE(auto_reply_count, 0) + 1")


def test_unusual_reply_is_held_as_draft():
    row = _row(pipeline="v2", stage="contacted")
    cur = _inbound_cursor(row)
    sender = Sender()
    msg = {"message_id": "<p1@nailpop.com>", "from_email": "ops@nailpop.com", "subject": "Partnership Nail Pop Studio",
           "body": "Could you share your pricing structures and contract terms? We want exclusivity."}
    llm = lambda system, *_a: ({"intent": "other", "confidence": 0.8, "flags": ["negotiation"]}
                               if "triage" in system else {})
    out = bp.ingest_message(cur, msg, sender=sender, llm=llm)
    assert out["action"] == "hold" and not sender.calls
    assert out["draft"]["to"] == "ops@nailpop.com"
    assert out["draft"]["in_reply_to"] == "<p1@nailpop.com>"
    assert [p[1] for p in cur.ran("INSERT INTO brand_activity")][-1] == "draft_held"


def test_redirect_mails_the_new_contact_and_restarts_the_sequence():
    row = _row(pipeline="v2", stage="contacted", contact_email="hello@doreandrose.com", contact_name=None,
               thread_subject="PR + UGC swap for Dore & Rose?", brand_name="Dore & Rose", website="doreandrose.com")
    cur = _inbound_cursor(row)
    sender = Sender()
    msg = {"message_id": "<r1@doreandrose.com>", "from_email": "hello@doreandrose.com", "from_name": "Emma from Dore & Rose",
           "subject": "PR + UGC swap for Dore & Rose?",
           "body": "Please send your proposal to our marketing team at Anna@doreandrose.com."}
    out = bp.ingest_message(cur, msg, sender=sender, llm=no_llm)
    assert out["action"] == "auto" and out["sent_to"] == "anna@doreandrose.com"
    assert sender.calls[0]["to"] == "anna@doreandrose.com"
    assert sender.calls[0]["text_content"].startswith("Hi,\n\nEmma pointed me your way")
    assert cur.ran("SET contact_email = %s")


def test_duplicate_inbound_is_skipped():
    cur = FakeCursor([("kind = 'reply_in' AND message_id", [{"brand_id": 7}])])
    out = bp.ingest_message(cur, {"message_id": "<x@y.com>", "from_email": "a@y.com"}, sender=Sender(), llm=no_llm)
    assert out["action"] == "skip"


def test_bounce_marks_bad_contact_without_reply():
    cur = _inbound_cursor(_row(pipeline="v2", stage="contacted"))
    sender = Sender()
    msg = {"message_id": "<b1@google.com>", "references": "<sent1@newcollab.co>", "from_email": "mailer-daemon@googlemail.com",
           "subject": "Delivery Status Notification (Failure)", "body": "Address not found"}
    out = bp.ingest_message(cur, msg, sender=sender, llm=no_llm)
    assert out["intent"] == "bounce" and not sender.calls
    assert [p[4] for p in cur.ran("INSERT INTO brand_activity") if p[1] == "stage_change"] == ["bad_contact"]


# --------------------------------------------------------------- closing + ramp


def test_first_brief_with_ref_marks_brand_won():
    cur = FakeCursor([
        ("WHERE ref_code = %s", [{"brand_id": 7}]),
        ("bo.last_inbound_subject", [_row(stage="interested")]),
    ])
    assert bp.record_brief(cur, 991, "abc123", "tacy@mdaire.com", "mdaire.com") == 7
    assert cur.ran("SET won_at = NOW(), opportunity_id = %s")[0][0] == 991
    assert [p[4] for p in cur.ran("INSERT INTO brand_activity") if p[1] == "stage_change"] == ["won"]


def test_brief_from_unknown_brand_is_ignored():
    cur = FakeCursor([])
    assert bp.record_brief(cur, 1, "", "me@gmail.com", "") is None


def test_daily_limit_ramps_on_health():
    assert bp.daily_new_limit({"sent_7d": 200, "bounce_rate_7d": 0.08}) == 20
    assert bp.daily_new_limit({"sent_7d": 200, "bounce_rate_7d": 0.01, "variants": [{"positive": 4}]}) == 50
    assert bp.daily_new_limit({"sent_7d": 30, "bounce_rate_7d": 0.0}) == 40


def test_brief_link_maps_category_to_niche():
    assert "niche=fitness&ref=r1" in bp.brief_link("activewear", "r1")
    assert "niche=lifestyle" in bp.brief_link(None, "r1")
