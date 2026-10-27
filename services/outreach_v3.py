"""Scan-led cold outreach (pipeline v3): every email carries real numbers from a verified competitor's Brand Scan.

Flow per prospect (a pr_brands row):
  prepare()  find competitors (services.competitors), measure them from the store, start a Brand Scan for the
             best unproven ones, and on later calls pick the verified competitor -> status 'ready' or
             'no_evidence' (with the reason for every rejected candidate).
  render()   the email for a sequence step, built only from numbers re-measured at render time. Returns the
             facts behind every number so a human can check them against the linked report.
  send()     test mode (default): goes to OUTREACH_TEST_EMAIL (team@newcollab.co) with a QA footer, changes no
             pipeline state. Live mode needs OUTREACH_V3_LIVE=1 and, unless OUTREACH_V3_REQUIRE_TEST=0, a test
             send of that same brand first.
  mark()     attribution from the email's ref code: scan_open -> signed_up -> campaign -> paid.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from psycopg2.extras import RealDictCursor

from services import brand_pipeline as bp
from services import brand_scan as scan
from services import competitors as comp

PIPELINE = "v3"
VARIANT = "scan_competitor"
SITE = (os.getenv("PUBLIC_SITE_URL") or "https://newcollab.co").rstrip("/")
TEST_EMAIL = os.getenv("OUTREACH_TEST_EMAIL") or "team@newcollab.co"
READY_FOR = timedelta(days=7)
SCAN_WAIT = timedelta(minutes=12)
SCANS_PER_PROSPECT = 2
FOLLOWUP_GAP_DAYS = {1: 3, 2: 4, 3: 7}
LAST_STEP = 3
SELF_MIN_CREATORS = 3
PRICE = "$299/mo"
EVENTS = ("scan_open", "signed_up", "campaign", "paid")


def live() -> bool:
    return os.getenv("OUTREACH_V3_LIVE") == "1"


def require_test() -> bool:
    return os.getenv("OUTREACH_V3_REQUIRE_TEST", "1") != "0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brand_outreach_v3 (
          brand_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'new',
          product TEXT,
          candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
          tried JSONB NOT NULL DEFAULT '[]'::jsonb,
          pick JSONB,
          scans JSONB NOT NULL DEFAULT '{}'::jsonb,
          reason TEXT,
          scans_started_at TIMESTAMPTZ,
          prepared_at TIMESTAMPTZ,
          test_sent_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
    cursor.execute("ALTER TABLE brand_outreach_v3 ENABLE ROW LEVEL SECURITY")


def _state(cursor, brand_id: int) -> Dict[str, Any]:
    cursor.execute("SELECT * FROM brand_outreach_v3 WHERE brand_id = %s", (brand_id,))
    row = cursor.fetchone()
    return dict(row) if row else {"brand_id": brand_id, "status": "new", "candidates": [], "tried": [], "scans": {}}


def _save(cursor, brand_id: int, **fields) -> None:
    cols = ["brand_id"] + list(fields)
    vals = [brand_id] + [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in fields.values()]
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields) + ", updated_at = NOW()"
    cursor.execute(f"INSERT INTO brand_outreach_v3 ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                   f"ON CONFLICT (brand_id) DO UPDATE SET {sets}", vals)


def _prospect(cursor, brand_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute("""SELECT id AS brand_id, brand_name, website, category, description, hero_product
                      FROM pr_brands WHERE id = %s""", (brand_id,))
    row = cursor.fetchone()
    if not row:
        return None
    row = dict(row)
    row["brand_name"] = bp.display_name(row.get("brand_name"), row.get("website"))
    return row


def _hints(conn, category: str) -> List[str]:
    try:
        from services import brand_insights as bi
        from services import gifting_radar as radar

        niche = bi.niche_for(category)
        return [r["name"] for r in radar._stats_rows(conn, niche)[:20]] if niche else []
    except Exception as err:
        conn.rollback()
        print(f"[outreach-v3] hints skipped: {type(err).__name__}: {err}")
        return []


# ----- prepare ---------------------------------------------------------------------------------------

def prepare(conn, brand_id: int, *, force: bool = False, write=None, redis_client=None,
            start_scan: Optional[Callable] = None, poll_scan: Optional[Callable] = None) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_schema(cur)
    prospect = _prospect(cur, brand_id)
    if not prospect or not prospect["brand_name"]:
        return {"brand_id": brand_id, "status": "error", "reason": "brand not found or no usable name"}
    st = _state(cur, brand_id)
    if force:
        st = {"brand_id": brand_id, "status": "new", "candidates": [], "tried": [], "scans": {}}
    if st["status"] in ("ready", "no_evidence") and st.get("prepared_at") and _now() - st["prepared_at"] < READY_FOR:
        return public_state(st, prospect)

    if not st.get("candidates"):
        found = comp.propose(prospect, _hints(conn, prospect.get("category") or ""), write=write)
        st["candidates"], st["product"] = found["candidates"], found["product"]
        if not st["candidates"]:
            _save(cur, brand_id, status="no_evidence", product=st["product"], candidates=[], tried=[],
                  reason="couldn't identify direct competitors", prepared_at=_now())
            conn.commit()
            return public_state(_state(cur, brand_id), prospect)

    start_scan = start_scan or (lambda name: scan.start(conn, name, redis_client=redis_client, ref="outreach_v3"))
    poll_scan = poll_scan or (lambda sid: scan.poll(conn, sid, redis_client=redis_client))
    scans = dict(st.get("scans") or {})
    pending = [name for name, sid in scans.items() if sid and poll_scan(int(sid)).get("status") == "searching"]
    started_at = st.get("scans_started_at")
    if pending and started_at and _now() - started_at < SCAN_WAIT:
        _save(cur, brand_id, status="scanning", product=st.get("product"), candidates=st["candidates"], scans=scans)
        conn.commit()
        return {**public_state(_state(cur, brand_id), prospect), "waiting_on": pending}

    result = comp.evaluate(conn, prospect, st.get("product") or "", st["candidates"], write=write)
    if result["pick"]:
        _save(cur, brand_id, status="ready", product=st.get("product"), candidates=st["candidates"],
              tried=result["tried"], pick=result["pick"], scans=scans, reason=None, prepared_at=_now())
        conn.commit()
        return public_state(_state(cur, brand_id), prospect)

    # Nothing verified from stored data yet: scan the best unproven candidates once (shared across prospects).
    if not scans:
        thin = [t["name"] for t in result["tried"] if str((t.get("verdict") or {}).get("reason") or "").startswith("thin")]
        for name in thin[:SCANS_PER_PROSPECT]:
            try:
                out = start_scan(name)
            except Exception as err:
                print(f"[outreach-v3] scan start failed {name}: {type(err).__name__}: {err}")
                continue
            if out.get("scan_id"):
                scans[name] = out["scan_id"]
        if any(poll_scan(int(sid)).get("status") == "searching" for sid in scans.values()):
            _save(cur, brand_id, status="scanning", product=st.get("product"), candidates=st["candidates"],
                  tried=result["tried"], scans=scans, scans_started_at=_now())
            conn.commit()
            return {**public_state(_state(cur, brand_id), prospect), "waiting_on": list(scans)}
        if scans:  # served straight from the store: evaluate again with what it had
            result = comp.evaluate(conn, prospect, st.get("product") or "", st["candidates"], write=write)
            if result["pick"]:
                _save(cur, brand_id, status="ready", product=st.get("product"), candidates=st["candidates"],
                      tried=result["tried"], pick=result["pick"], scans=scans, reason=None, prepared_at=_now())
                conn.commit()
                return public_state(_state(cur, brand_id), prospect)
    _save(cur, brand_id, status="no_evidence", product=st.get("product"), candidates=st["candidates"],
          tried=result["tried"], pick=None, scans=scans, reason="no competitor passed verification",
          prepared_at=_now())
    conn.commit()
    return public_state(_state(cur, brand_id), prospect)


def public_state(st: Dict[str, Any], prospect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {k: st.get(k) for k in ("brand_id", "status", "product", "pick", "tried", "candidates", "reason")}
    for k in ("prepared_at", "test_sent_at"):
        out[k] = st[k].isoformat() if st.get(k) else None
    if prospect:
        out["brand_name"] = prospect["brand_name"]
        out["website"] = prospect.get("website")
    return out


# ----- render -----------------------------------------------------------------------------------------

def scan_link(name: str, ref: str) -> str:
    return f"{SITE}/brands/scan?b={quote(name)}&ref=outreach&oref={quote(ref)}"


def signup_link(category: str, ref: str) -> str:
    niche = bp.niche_for(category)
    return f"{bp.APP_URL}/for-brands?ref=outreach&oref={quote(ref)}&niche={quote(niche)}"


def possessive(name: str) -> str:
    return f"{name}’" if name.lower().endswith("s") else f"{name}’s"


def _num(n: int) -> str:
    return f"{n:,}"


def render(conn, brand_id: int, step: int = 0, *, ref: str = "", first: str = "") -> Dict[str, Any]:
    """{ok, subject, text, facts, links} or {ok: False, error}. Numbers are re-measured now, so the email matches
    what the linked report shows when it's opened."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_schema(cur)
    prospect = _prospect(cur, brand_id)
    st = _state(cur, brand_id)
    if not prospect or st["status"] != "ready" or not st.get("pick"):
        return {"ok": False, "error": f"not ready (status={st['status']})"}
    pick = st["pick"]
    m = comp.measure(conn, pick["name"])
    if not m.get("ok"):
        return {"ok": False, "error": f"competitor no longer passes checks: {m.get('reason')}"}
    brand, competitor = prospect["brand_name"], m["name"]
    ref = ref or "preview"
    hi = f"Hi {first}," if first else "Hi,"
    link = scan_link(competitor, ref)
    signup = signup_link(prospect.get("category") or "", ref)
    facts = [
        {"claim": f"{competitor} is a direct competitor", "value": pick.get("why") or "",
         "source": f"model check: {pick['verdict'].get('reason')} (confidence {pick['verdict'].get('confidence')})"},
        {"claim": "creators posting about the competitor, last 6 months", "value": m["creators"], "source": link},
        {"claim": "their videos / total views", "value": f"{m['videos']} / {m['views_label']}", "source": link},
        {"claim": "matches that @mention or hashtag the brand", "value": f"{int(m['strong_share'] * 100)}%",
         "source": "evidence gate"},
    ]
    sig = bp.SIGNATURE
    if step == 0:
        subject = f"{possessive(competitor)} TikTok creators"
        lines = [hi, "",
                 f"{competitor} had {_num(m['creators'])} creators post about them on TikTok in the last 6 months, "
                 f"{_num(m['videos'])} videos with {m['views_label']} views."]
        if m.get("top_hook"):
            h = m["top_hook"]
            lines.append(f"The opener {h['creators']} of them reused: “{h['text']}”")
            facts.append({"claim": "top repeated opener", "value": f"{h['text']} ({h['creators']} creators)", "source": link})
        lines += ["", "I put the full breakdown here, with the creators, their videos and the hooks:", link, "",
                  f"Want the same for {brand}?", "", sig]
    elif step == 1:
        subject = None
        own = comp.measure(conn, brand)
        if own.get("ok") or (own.get("creators", 0) >= SELF_MIN_CREATORS and own.get("strong_share", 0) >= comp.MIN_STRONG_SHARE):
            own_link = scan_link(brand, ref)
            lines = [hi, "", f"I ran it for {brand} too: {_num(own['creators'])} creators posted about {brand} on "
                             "TikTok in the last 6 months. Here's who they are:", own_link, "", "Mazza"]
            facts.append({"claim": f"creators posting about {brand}, last 6 months", "value": own["creators"],
                          "source": own_link})
        else:
            lines = [hi, "", f"The creators posting for {competitor} are the same profile who'd post for {brand}.",
                     "", "Your first gifted campaign on Newcollab is free: you pick the creators, ship your "
                         "product, and get the videos back to run as ads.", signup, "", "Mazza"]
    elif step == 2:
        subject = None
        lines = [hi, "", f"Last idea for {brand}: run one gifted campaign with small creators like "
                         f"{competitor}'s, free.", "",
                 "You choose every creator first. They post, and you keep the videos for ads. "
                 f"If you want more campaigns after that, Pro is {PRICE}.", signup, "", "Mazza"]
    elif step == 3:
        subject = None
        lines = [hi, "", "I'll stop here. If creator content becomes a priority, your scan and the creators are "
                         "waiting:", link, "", f"Or grab 15 minutes with me: {bp.BOOKING_URL}", "", "Mazza"]
    else:
        return {"ok": False, "error": f"unknown step {step}"}
    text = "\n".join(lines)
    return {"ok": True, "brand_id": brand_id, "brand_name": brand, "competitor": competitor, "step": step,
            "subject": subject, "text": text, "facts": facts, "links": {"scan": link, "signup": signup},
            "measured": {k: v for k, v in m.items() if k != "captions"}}


def _qa_footer(email: Dict[str, Any], real_to: str, st: Dict[str, Any]) -> str:
    rows = ["", "", "---- QA (test send, not what the brand sees below this line) ----",
            f"Would go to: {real_to or 'no contact email yet'}", f"Competitor: {email['competitor']}"]
    rows += [f"- {f['claim']}: {f['value']}  [{f['source']}]" for f in email["facts"]]
    rejected = [t for t in st.get("tried") or [] if not (t.get("verdict") or {}).get("ok")]
    if rejected:
        rows.append("Rejected candidates:")
        rows += [f"- {t['name']}: {(t.get('verdict') or {}).get('reason')}" for t in rejected[:8]]
    return "\n".join(rows)


# ----- send ------------------------------------------------------------------------------------------

def send(conn, brand_id: int, step: int = 0, *, test: Optional[bool] = None, sender=None) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_schema(cur)
    row = bp.load_row(cur, brand_id)
    if not row:
        return {"ok": False, "error": "brand not found"}
    test = True if not live() else bool(test)
    st = _state(cur, brand_id)
    real_to = (row.get("contact_email") or row.get("brand_email") or "").strip().lower()
    first = bp.first_name(row.get("contact_name"))

    if not test:
        if not bp._EMAIL_RE.fullmatch(real_to):
            return {"ok": False, "error": "no contact email"}
        if require_test() and not st.get("test_sent_at"):
            return {"ok": False, "error": "send a test of this brand to the team inbox first"}
        if step == 0 and (row.get("last_contacted_at") or (row.get("stage") or "new") != "new"):
            return {"ok": False, "error": f"already in pipeline (stage={row.get('stage')})"}
        if step > 0 and (row.get("pipeline") != PIPELINE or row.get("stage") != "contacted"
                         or int(row.get("sequence_step") or 0) + 1 != step):
            return {"ok": False, "error": f"not due for step {step} (stage={row.get('stage')})"}

    ref = row.get("ref_code") or bp.new_ref()
    email = render(conn, brand_id, step, ref=ref if not test else f"test-{ref}", first=first)
    if not email.get("ok"):
        return email
    subject = email["subject"] or "Re: " + (row.get("thread_subject") or f"{possessive(email['competitor'])} TikTok creators")
    text = email["text"]
    in_reply_to = row.get("thread_message_id") if step > 0 and not test else None

    if test:
        res_mid = bp._deliver(cur, row, TEST_EMAIL, f"[TEST to {real_to or 'no contact'}] {subject}",
                              text + _qa_footer(email, real_to, st), sender)
        if not res_mid:
            conn.commit()
            return {"ok": False, "error": "test send failed"}
        cur.execute("UPDATE brand_outreach_v3 SET test_sent_at = NOW(), updated_at = NOW() WHERE brand_id = %s",
                    (brand_id,))
        bp.log_activity(cur, brand_id, "v3_test_sent", variant=VARIANT, email=TEST_EMAIL, subject=subject,
                        body=text, message_id=res_mid, meta={"step": step, "would_send_to": real_to,
                                                             "facts": email["facts"]})
        conn.commit()
        return {**email, "subject": subject, "test": True, "to": TEST_EMAIL, "would_send_to": real_to,
                "message_id": res_mid}

    mid = bp._deliver(cur, row, real_to, subject, text, sender, in_reply_to=in_reply_to, references=in_reply_to)
    if not mid:
        conn.commit()
        return {"ok": False, "error": "send failed"}
    bp.ensure_tracking(cur, brand_id)
    nxt = FOLLOWUP_GAP_DAYS.get(step + 1)
    if step == 0:
        cur.execute("""UPDATE brand_outreach_tracking
                       SET outreach_count = COALESCE(outreach_count, 0) + 1, last_contacted_at = NOW(),
                           last_subject = %s, pipeline = %s, sequence_step = 0, variant = %s, ref_code = %s,
                           thread_subject = %s, thread_message_id = %s, next_action = 'followup',
                           next_action_at = NOW() + (%s * INTERVAL '1 day')
                       WHERE brand_id = %s""",
                    (subject, PIPELINE, VARIANT, ref, subject, mid, FOLLOWUP_GAP_DAYS[1], brand_id))
        bp._set_stage(cur, row, "contacted")
    else:
        cur.execute("""UPDATE brand_outreach_tracking
                       SET outreach_count = COALESCE(outreach_count, 0) + 1, last_contacted_at = NOW(),
                           sequence_step = %s,
                           next_action = CASE WHEN %s IS NULL THEN NULL ELSE 'followup' END,
                           next_action_at = CASE WHEN %s IS NULL THEN NULL ELSE NOW() + (%s * INTERVAL '1 day') END
                       WHERE brand_id = %s""", (step, nxt, nxt, nxt or 0, brand_id))
        if step >= LAST_STEP:
            bp._set_stage(cur, row, "nurture", "v3 sequence finished without reply")
    bp.log_activity(cur, brand_id, "sent", variant=VARIANT, email=real_to, subject=subject, body=text,
                    message_id=mid, in_reply_to=in_reply_to,
                    meta={"kind": "initial" if step == 0 else "followup", "step": step, "pipeline": PIPELINE,
                          "competitor": email["competitor"], "facts": email["facts"]})
    conn.commit()
    return {**email, "subject": subject, "test": False, "to": real_to, "message_id": mid}


# ----- queues -------------------------------------------------------------------------------------------

def queue_ready(conn, limit: int = 40) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_schema(cur)
    cur.execute("""
        SELECT v.brand_id, b.brand_name, b.website, v.pick->>'name' AS competitor, v.test_sent_at, v.prepared_at
        FROM brand_outreach_v3 v JOIN pr_brands b ON b.id = v.brand_id
        LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = v.brand_id
        WHERE v.status = 'ready' AND bo.last_contacted_at IS NULL AND v.prepared_at > NOW() - %s
        ORDER BY v.prepared_at DESC LIMIT %s""", (READY_FOR, max(1, min(int(limit), 150))))
    return [{**dict(r), "test_sent_at": r["test_sent_at"].isoformat() if r["test_sent_at"] else None,
             "prepared_at": r["prepared_at"].isoformat() if r["prepared_at"] else None} for r in cur.fetchall()]


def queue_followups(conn, limit: int = 100) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT brand_id, sequence_step FROM brand_outreach_tracking
                   WHERE pipeline = %s AND stage = 'contacted' AND next_action = 'followup' AND next_action_at <= NOW()
                   ORDER BY next_action_at LIMIT %s""", (PIPELINE, max(1, min(int(limit), 200))))
    return [{"brand_id": r["brand_id"], "step": int(r["sequence_step"] or 0) + 1} for r in cur.fetchall()]


# ----- attribution --------------------------------------------------------------------------------------

def mark(cursor, ref: str, event: str, meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Record a funnel step for the prospect behind a ref code, once per event. Returns the brand_id."""
    ref = str(ref or "").strip()
    if event not in EVENTS or not ref or ref.startswith("test-") or ref == "preview":
        return None
    cursor.execute("SELECT brand_id, stage FROM brand_outreach_tracking WHERE ref_code = %s", (ref,))
    hit = cursor.fetchone()
    if not hit:
        return None
    brand_id = int(hit["brand_id"] if isinstance(hit, dict) else hit[0])
    cursor.execute("SELECT 1 FROM brand_activity WHERE brand_id = %s AND kind = %s LIMIT 1", (brand_id, f"v3_{event}"))
    if cursor.fetchone():
        return brand_id
    bp.log_activity(cursor, brand_id, f"v3_{event}", meta=meta or {})
    stage = {"signed_up": "interested", "campaign": "won", "paid": "paid"}.get(event)
    if stage == "paid":
        cursor.execute("UPDATE brand_outreach_tracking SET stage = 'paid' WHERE brand_id = %s", (brand_id,))
    elif stage:
        row = bp.load_row(cursor, brand_id)
        if row:
            bp._set_stage(cursor, row, stage, f"v3 {event}")
    return brand_id


def report(conn, days: int = 30) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_schema(cur)
    cur.execute("SELECT status, COUNT(*) AS n FROM brand_outreach_v3 GROUP BY status")
    prep = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute("""
        SELECT kind, COUNT(DISTINCT brand_id) AS n FROM brand_activity
        WHERE created_at > NOW() - (%s * INTERVAL '1 day')
          AND (kind LIKE 'v3_%%' OR (kind = 'sent' AND meta->>'pipeline' = %s))
        GROUP BY kind""", (days, PIPELINE))
    kinds = {r["kind"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute("""SELECT COUNT(*) FILTER (WHERE reply_count > 0 AND COALESCE(intent, '') NOT IN ('auto_reply', 'bounce')) AS replied
                   FROM brand_outreach_tracking WHERE pipeline = %s""", (PIPELINE,))
    replied = int((cur.fetchone() or {}).get("replied") or 0)
    funnel = {"contacted": kinds.get("sent", 0), "replied": replied}
    funnel.update({e: kinds.get(f"v3_{e}", 0) for e in EVENTS})
    return {"pipeline": PIPELINE, "live": live(), "require_test": require_test(), "test_email": TEST_EMAIL,
            "prepare": prep, "tests_sent": kinds.get("v3_test_sent", 0), "funnel": funnel,
            "mrr_usd": funnel["paid"] * 299}


def mark_paid(cursor, pr_brand_id: int) -> Optional[int]:
    """A brand subscribed: credit the cold email behind it (its workspace account's ref, or the prospect row)."""
    refs: List[str] = []
    try:
        cursor.execute("SELECT outreach_ref FROM brands WHERE pr_brand_id = %s AND outreach_ref IS NOT NULL", (pr_brand_id,))
        refs = [r["outreach_ref"] if isinstance(r, dict) else r[0] for r in cursor.fetchall()]
    except Exception:
        cursor.connection.rollback()
    cursor.execute("SELECT ref_code FROM brand_outreach_tracking WHERE brand_id = %s AND pipeline = %s AND ref_code IS NOT NULL",
                   (pr_brand_id, PIPELINE))
    refs += [r["ref_code"] if isinstance(r, dict) else r[0] for r in cursor.fetchall()]
    for ref in dict.fromkeys(refs):
        hit = mark(cursor, ref, "paid", {"pr_brand_id": pr_brand_id})
        if hit:
            return hit
    return None
