"""Polly Autopilot API (Pro): connect Gmail, plan the week's brands, draft, approve, send.
``auto_fill`` runs the same plan + draft from the cron."""

import os

from flask import Blueprint, jsonify, redirect, request

from services.polly_autopilot import (
    AUTO_DRAFTS_PER_RUN,
    FILL_BLOCKED_WAIT,
    FOLLOWUP_GAPS,
    MANUAL_BATCH,
    TARGET_MAX,
    TARGET_MIN,
    TARGET_OPTIONS,
    _default_drafts_email,
    add_draft,
    approve,
    batch_size,
    claim_unannounced_drafts,
    drafts_note,
    fill_candidates,
    list_items,
    mark_filled,
    month_counts,
    per_week,
    post_thread_note,
    queued_brand_ids,
    sends_per_day,
    set_enabled,
    settings,
    skip_item,
    update_draft,
    utc_now,
)
from services.polly_autopilot import update_settings as save_settings
from services.polly_gmail import (
    GmailError,
    auth_url,
    disconnect,
    exchange_code,
    gmail_configured,
    load_account,
    read_state,
    save_account,
)

autopilot_bp = Blueprint("polly_autopilot", __name__, url_prefix="/api/polly/autopilot")

POLLY_PATH = "/creator/dashboard/for-you"
PLAN_ALTERNATES = 4


def _frontend(path: str) -> str:
    return (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/") + path


def _auth():
    from routes.polly import _creator_auth
    return _creator_auth()


def _is_pro(creator_id, conn) -> bool:
    from routes.polly import _unlock_balance
    return bool(_unlock_balance(creator_id, conn=conn).get("is_unlimited"))


NICHE_OPTIONS = ("skincare", "makeup", "haircare", "fashion", "fitness", "food", "wellness", "lifestyle", "tech")


def _targeting(conn, creator_id):
    from services.polly_discovery import stated_niches
    from services.polly_memory import load_thread
    from services.polly_prefs import CATEGORY_WORDS, get_prefs

    notes = load_thread(conn, creator_id).get("notes") or {}
    niches = [n for n in stated_niches(notes) if n in NICHE_OPTIONS]
    if not niches:
        cur = conn.cursor()
        cur.execute("SELECT creator_niches FROM creators WHERE id = %s", (creator_id,))
        row = cur.fetchone()
        niches = [n for n in dict.fromkeys(str(x).strip().lower() for x in (row[0] if row and row[0] else []))
                  if n in NICHE_OPTIONS]
    return {
        "niches": niches,
        "avoid_categories": list(get_prefs(notes).get("avoid_categories") or []),
        "niche_options": list(NICHE_OPTIONS),
        "avoid_options": list(CATEGORY_WORDS),
    }


def _save_targeting(conn, creator_id, body):
    from services.polly_memory import load_thread, save_thread
    from services.polly_prefs import CATEGORY_WORDS, get_prefs

    if "niches" not in body and "avoid_categories" not in body:
        return
    thread = load_thread(conn, creator_id)
    notes = dict(thread.get("notes") or {})
    prefs = get_prefs(notes)
    niches = list(notes.get("niche") or []) if isinstance(notes.get("niche"), list) else []
    before = list(niches)
    avoid = list(prefs.get("avoid_categories") or [])
    # The list just edited wins when a category is in both.
    if isinstance(body.get("niches"), list):
        niches = [n for n in dict.fromkeys(str(x).strip().lower() for x in body["niches"]) if n in NICHE_OPTIONS]
        avoid = [c for c in avoid if c not in niches]
    if isinstance(body.get("avoid_categories"), list):
        avoid = [c for c in dict.fromkeys(str(x) for x in body["avoid_categories"]) if c in CATEGORY_WORDS]
        niches = [n for n in niches if n not in avoid]
    notes["niche"] = niches
    prefs["avoid_categories"] = avoid
    notes["prefs"] = prefs
    save_thread(conn, creator_id, thread.get("messages") or [], thread.get("suggested_brands") or [], notes=notes)
    if isinstance(body.get("niches"), list) or niches != before:
        _sync_for_you_niches(conn, creator_id, niches)


def _save_pitch_prefs(conn, creator_id, body):
    from services.polly_memory import load_thread, save_thread
    from services.polly_prefs import PITCH_PREF_KEYS, merge_prefs

    raw = body.get("pitch_prefs")
    if not isinstance(raw, dict):
        return
    patch = {k: raw[k] for k in PITCH_PREF_KEYS if k in raw}
    if not patch:
        return
    thread = load_thread(conn, creator_id)
    notes = merge_prefs(dict(thread.get("notes") or {}), patch)
    save_thread(conn, creator_id, thread.get("messages") or [], thread.get("suggested_brands") or [], notes=notes)


def _pitch_prefs(conn, creator_id):
    from services.polly_memory import load_thread
    from services.polly_prefs import PITCH_PREF_KEYS, get_prefs, pitch_prefs_line

    try:
        prefs = get_prefs(load_thread(conn, creator_id).get("notes") or {})
    except Exception as err:
        print(f"[Polly autopilot] pitch prefs load skipped: {err}")
        conn.rollback()
        return None
    out = {k: prefs.get(k) for k in PITCH_PREF_KEYS}
    out["summary"] = pitch_prefs_line(prefs)
    return out


def _sync_for_you_niches(conn, creator_id, niches):
    """For You matches on creators.creator_niches, so Autopilot targeting and For You stay one pool."""
    from routes.polly import _FOR_YOU_CACHE, _FOR_YOU_LOCK
    from services.mentor_matchmaker import invalidate_mentor_matches

    cur = conn.cursor()
    cur.execute("UPDATE creators SET creator_niches = %s WHERE id = %s", (list(niches), creator_id))
    conn.commit()
    with _FOR_YOU_LOCK:
        _FOR_YOU_CACHE.pop(int(creator_id), None)
    invalidate_mentor_matches(creator_id)


def _status_payload(conn, creator_id, is_pro):
    account = load_account(conn, creator_id) if gmail_configured() else None
    prefs = settings(conn, creator_id)
    counts = month_counts(conn, creator_id)
    target = prefs["monthly_target"]
    return {
        "success": True,
        "available": gmail_configured(),
        "app_verified": os.getenv("POLLY_GMAIL_APP_VERIFIED", "").strip().lower() in ("1", "true", "yes"),
        "is_pro": is_pro,
        "gmail": {
            "connected": bool(account and not account.get("revoked_at")),
            "email": (account or {}).get("email"),
            "needs_reconnect": bool(account and account.get("revoked_at")),
        },
        "enabled": prefs["enabled"],
        "monthly_target": target,
        "deal_focus": prefs["deal_focus"],
        "send_days": prefs["send_days"],
        "followups": prefs["followups"],
        "per_week": per_week(target),
        "per_day": sends_per_day(target, prefs["send_days"]),
        "target_range": [TARGET_MIN, TARGET_MAX],
        "target_options": list(TARGET_OPTIONS),
        "max_followups": len(FOLLOWUP_GAPS),
        "targeting": _targeting(conn, creator_id) if is_pro else None,
        "pitch_prefs": _pitch_prefs(conn, creator_id) if is_pro else None,
        "month": counts,
        "next_batch": batch_size(counts, target),
        "items": list_items(conn, creator_id),
    }


def _guard(require_gmail=True):
    """(creator_id, conn, creator, error_response)."""
    creator_id, conn, creator = _auth()
    if not creator_id:
        return None, None, None, (jsonify({"success": False, "error": "Not authenticated"}), 401)
    if not _is_pro(creator_id, conn):
        conn.close()
        return None, None, None, (jsonify({"success": False, "error": "pro_required", "paywall": True}), 402)
    if require_gmail:
        account = load_account(conn, creator_id) if gmail_configured() else None
        if not account or account.get("revoked_at"):
            conn.close()
            return None, None, None, (jsonify({"success": False, "error": "gmail_not_connected"}), 409)
    return creator_id, conn, creator, None


@autopilot_bp.route("/status", methods=["GET"])
def status():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        return jsonify(_status_payload(conn, creator_id, _is_pro(creator_id, conn)))
    finally:
        conn.close()


@autopilot_bp.route("/gmail/connect", methods=["GET"])
def gmail_connect():
    """Open to free creators too: they send their first pitches from Gmail in one tap."""
    if not gmail_configured():
        return jsonify({"success": False, "error": "gmail_unavailable"}), 503
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        return jsonify({"success": True, "url": auth_url(creator_id)})
    finally:
        conn.close()


@autopilot_bp.route("/gmail/callback", methods=["GET"])
def gmail_callback():
    from pr_crm_routes import get_db_connection

    creator_id = read_state(request.args.get("state") or "")
    if not creator_id or request.args.get("error") or not request.args.get("code"):
        return redirect(_frontend(f"{POLLY_PATH}?autopilot=gmail_denied"))
    conn = get_db_connection()
    try:
        save_account(conn, creator_id, exchange_code(request.args["code"]))
        if _is_pro(creator_id, conn):
            set_enabled(conn, creator_id, True)
        return redirect(_frontend(f"{POLLY_PATH}?autopilot=gmail_connected"))
    except GmailError as gerr:
        print(f"[Polly autopilot] gmail connect failed creator={creator_id}: {gerr}")
        reason = "gmail_scope" if "gmail.send" in str(gerr) else "gmail_failed"
        return redirect(_frontend(f"{POLLY_PATH}?autopilot={reason}"))
    finally:
        conn.close()


@autopilot_bp.route("/gmail/disconnect", methods=["POST"])
def gmail_disconnect():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        disconnect(conn, creator_id)
        set_enabled(conn, creator_id, False)
        return jsonify(_status_payload(conn, creator_id, _is_pro(creator_id, conn)))
    finally:
        conn.close()


@autopilot_bp.route("/toggle", methods=["POST"])
def toggle():
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        set_enabled(conn, creator_id, bool((request.get_json(silent=True) or {}).get("enabled")))
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


@autopilot_bp.route("/settings", methods=["POST"])
def update_settings():
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        if "enabled" in body:
            set_enabled(conn, creator_id, bool(body.get("enabled")))
        save_settings(conn, creator_id, body)
        _save_targeting(conn, creator_id, body)
        _save_pitch_prefs(conn, creator_id, body)
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


def _brands_with_email(conn, ids):
    from psycopg2.extras import RealDictCursor

    if not ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, brand_name, NULLIF(TRIM(contact_email), '') AS email
        FROM pr_brands
        WHERE id = ANY(%s)
          AND COALESCE(LOWER(email_status), '') NOT IN ('bounced', 'invalid', 'undeliverable', 'risky')
          AND COALESCE(email_bounce_count, 0) = 0
        """,
        (list(ids),),
    )
    return {int(r["id"]): r for r in cur.fetchall() if r.get("email")}


def _creator_context(conn, creator):
    from routes.polly import _load_scrape
    from services.polly_memory import load_thread

    notes = load_thread(conn, creator["id"]).get("notes") or {}
    return notes, _load_scrape(conn, creator.get("user_id"))


POOL_SECTIONS = ("recruiting", "open_lists", "matched")
POOL_SIZE = 60
# For You's growth_match floor. Live rosters For You didn't score stay out of the pipeline.
MIN_FIT = 35


def _fit(brand):
    try:
        return int(brand.get("match_score") or 0)
    except (TypeError, ValueError):
        return 0


def _for_you_pool(exclude_ids):
    """The creator's For You matches (same scoring and cutoffs), deeper than the 8 cards shown."""
    from flask import current_app, session as flask_session
    from pr_crm_routes import get_for_you
    from routes.polly import _copy_session, unpack_view_result

    saved = _copy_session()
    headers = {}
    for key in ("Authorization", "Cookie"):
        if request.headers.get(key):
            headers[key] = request.headers.get(key)
    query = {"pool": "autopilot", "exclude": ",".join(str(int(i)) for i in exclude_ids)}
    with current_app.test_request_context("/api/pr-crm/for-you", query_string=query, headers=headers):
        flask_session.update(saved)
        return unpack_view_result(get_for_you())


def _pick_brands(conn, creator_id, creator, notes, scrape):
    """For You matches that are emailable and not pitched, queued or blocked, best first. (brands, error)."""
    from routes.polly import _match_scrape, _pipeline_pitched_ids
    from services.polly import drop_pitched, flatten_for_you
    from services.polly_discovery import required_categories_for_match, stated_niches
    from services.polly_prefs import filter_brands_by_prefs

    queued = queued_brand_ids(conn, creator_id)
    status, data = _for_you_pool(queued)
    if status != 200 or not (data or {}).get("success"):
        return [], (data or {}).get("error") or "Could not load matches"
    match_scrape = _match_scrape(scrape, notes, creator)
    cats = required_categories_for_match(notes, match_scrape)
    exclude = list(queued) + list(_pipeline_pitched_ids(creator_id) or [])
    brands = flatten_for_you(
        {key: data.get(key) for key in POOL_SECTIONS},
        limit=POOL_SIZE,
        scrape=match_scrape,
        niches=cats or stated_niches(notes) or (creator or {}).get("creator_niches"),
        required_categories=cats,
        exclude_ids=exclude,
    )
    brands = filter_brands_by_prefs(drop_pitched(brands, notes, extra_ids=exclude), notes)
    brands = [b for b in brands if _fit(b) >= MIN_FIT]
    emailable = _brands_with_email(conn, [int(b["id"]) for b in brands if b.get("id")])
    matched = [b for b in brands if b.get("id") and int(b["id"]) in emailable]
    return matched, None if matched else "no_matches"


def _write_draft(conn, creator_id, creator, brand_id, notes, scrape, announced=False):
    """Generate and queue one pitch. (item, error code)."""
    from routes.polly import _invoke_generate_pr_package
    from services.polly import (
        apply_gifted_ask_to_pitch,
        apply_location_to_pitch,
        apply_paid_ask_to_pitch,
        pitch_from_package_response,
        pitch_has_placeholder,
        resolve_pitch_location,
    )

    brand = _brands_with_email(conn, [brand_id]).get(brand_id)
    if not brand:
        return None, "no_contact_email"
    shipping = resolve_pitch_location(creator, scrape, notes)
    if shipping.get("needs_location"):
        return None, "needs_location"
    status, pkg = _invoke_generate_pr_package(
        brand_id, city=shipping.get("city") or "", country=shipping.get("country") or "",
    )
    if status != 200 or not pkg.get("success"):
        return None, pkg.get("error") or "draft_failed"
    pitch = pitch_from_package_response(pkg) or {}
    loc_display = str(shipping.get("display") or "").strip()
    focus = settings(conn, creator_id)["deal_focus"]
    if focus == "auto":
        focus = "paid" if notes.get("deal_intent") == "paid" else "gifted"
    if focus == "paid":
        from services.polly_kit import load_kit_snapshot

        pitch = apply_paid_ask_to_pitch(pitch, kit=load_kit_snapshot(conn, creator_id), scrape=scrape,
                                        location_display=loc_display or None)
    else:
        pitch = apply_gifted_ask_to_pitch(pitch, location_display=loc_display or None)
    if loc_display:
        pitch = apply_location_to_pitch(pitch, shipping.get("city") or "", shipping.get("country") or "", loc_display)
    from services.polly_pitch_edit import apply_pitch_prefs, pitch_pref_inputs
    kit = None
    if (notes.get("prefs") or {}).get("lead_metric") or (notes.get("prefs") or {}).get("custom_template"):
        from services.polly_kit import load_kit_snapshot
        kit = load_kit_snapshot(conn, creator_id)
    inputs = pitch_pref_inputs(conn, notes, brand_id, creator=creator, scrape=scrape, kit=kit,
                               location=loc_display)
    pitch = apply_pitch_prefs(pitch, notes, slots=inputs["slots"], facts=inputs["facts"])
    body = pitch.get("body") or ""
    if not body or pitch_has_placeholder(body):
        return None, "needs_location"
    item = add_draft(
        conn, creator_id, brand_id, brand["brand_name"], pitch.get("email") or brand["email"],
        pitch.get("subject") or f"{brand['brand_name']} collab", body, announced=announced,
    )
    return (item, None) if item else (None, "already_queued")


_DRAFT_STATUS = {"no_contact_email": 422, "needs_location": 409, "already_queued": 409}


@autopilot_bp.route("/plan", methods=["POST"])
def plan():
    """This week's picks: matched, emailable, not pitched or queued already."""
    from routes.polly import json_safe

    creator_id, conn, creator, err = _guard()
    if err:
        return err
    try:
        prefs = settings(conn, creator_id)
        size = min(MANUAL_BATCH, batch_size(month_counts(conn, creator_id), prefs["monthly_target"]))
        if size <= 0:
            return jsonify({"success": True, "brands": [], "reason": "target_reached"})
        notes, scrape = _creator_context(conn, creator)
        matched, error = _pick_brands(conn, creator_id, creator, notes, scrape)
        picks, alternates = matched[:size], matched[size:size + PLAN_ALTERNATES]
        return jsonify({"success": True, "brands": json_safe(picks), "alternates": json_safe(alternates),
                        "size": size, "error": None if picks else error})
    finally:
        conn.close()


@autopilot_bp.route("/draft", methods=["POST"])
def draft():
    """Write one pitch for the batch. The frontend calls this per brand so progress shows."""
    creator_id, conn, creator, err = _guard()
    if err:
        return err
    try:
        try:
            brand_id = int((request.get_json(silent=True) or {}).get("brand_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "brand_id required"}), 400
        notes, scrape = _creator_context(conn, creator)
        item, error = _write_draft(conn, creator_id, creator, brand_id, notes, scrape, announced=True)
        if error:
            return jsonify({"success": False, "error": error}), _DRAFT_STATUS.get(error, 502)
        scheduled = approve(conn, creator_id, [item["id"]])
        return jsonify({"success": True, "item": item, "scheduled": scheduled})
    finally:
        conn.close()


def _load_creator(conn, creator_id):
    from psycopg2.extras import RealDictCursor

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.user_id, c.niche, c.creator_niches,
               to_jsonb(c) -> 'shipping_address' AS shipping_address,
               u.first_name, u.last_name, u.country
        FROM creators c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (creator_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


_BLOCKED_NOTES = {
    "needs_location": "I'm ready to write this week's Autopilot pitches, but I need the city you'd want "
                      "products shipped to. Tell me where you're based and I'll get them written.",
    "no_brands": "I couldn't find new matched brands with a verified email for Autopilot this week. "
                 "Add a niche in **Autopilot** settings and I'll look again.",
}


def _fill_creator(conn, row, creator, budget):
    """Draft up to ``row['need']`` pitches, at most ``budget`` attempts. (names, blocked, attempts, failed)."""
    creator_id = row["creator_id"]
    notes, scrape = _creator_context(conn, creator)
    from services.polly import resolve_pitch_location

    if resolve_pitch_location(creator, scrape, notes).get("needs_location"):
        return [], "needs_location", 0, False
    matched, _error = _pick_brands(conn, creator_id, creator, notes, scrape)
    if not matched:
        return [], "no_brands", 0, False
    names, attempts, failed = [], 0, False
    for brand in matched:
        if len(names) >= row["need"] or attempts >= budget:
            break
        attempts += 1
        conn.commit()  # don't sit in an open transaction while the pitch generates
        item, error = _write_draft(conn, creator_id, creator, int(brand["id"]), notes, scrape)
        if item:
            approve(conn, creator_id, [item["id"]])
            names.append(item.get("brand_name") or brand.get("name") or "a brand")
        elif error == "needs_location":
            return names, "needs_location", attempts, True
        else:
            print(f"[Polly autopilot] auto draft failed creator={creator_id} brand={brand.get('id')}: {error}")
            failed = True
    return names, None, attempts, failed


def auto_fill(conn, now=None, budget=AUTO_DRAFTS_PER_RUN, note_fn=None, email_fn=None):
    """Cron: keep each enabled creator a week of scheduled pitches ahead, so the monthly target is
    reached without anyone coming back to plan or approve."""
    from flask import current_app, session

    now = now or utc_now()
    note = note_fn or post_thread_note
    email = email_fn or _default_drafts_email
    out = {"drafted": 0, "creators": 0, "blocked": 0}
    for row in fill_candidates(conn, now):
        if budget <= 0:
            break
        creator_id = row["creator_id"]
        creator = _load_creator(conn, creator_id) if _is_pro(creator_id, conn) else None
        if not creator:
            mark_filled(conn, creator_id, now + FILL_BLOCKED_WAIT, "not_pro")
            continue
        with current_app.test_request_context("/api/polly/autopilot/fill", method="POST"):
            session["creator_id"] = creator_id
            session["user_id"] = creator["user_id"]
            names, blocked, attempts, failed = _fill_creator(conn, row, creator, budget)
        budget -= attempts
        out["drafted"] += len(names)
        out["creators"] += 1 if names else 0
        if blocked:
            out["blocked"] += 1
            if blocked != row.get("fill_blocked") and blocked in _BLOCKED_NOTES:
                note(conn, creator_id, _BLOCKED_NOTES[blocked], [])
        if not (blocked or failed or len(names) >= row["need"]):
            continue
        mark_filled(conn, creator_id, now + FILL_BLOCKED_WAIT if blocked and not names else now, blocked)
        fresh = claim_unannounced_drafts(conn, creator_id, now)
        if fresh:
            note(conn, creator_id, drafts_note(fresh), [])
            try:
                email(conn, creator_id, fresh)
            except Exception as err:
                print(f"[Polly autopilot] drafts email skipped: {err}")
                conn.rollback()
    return out


@autopilot_bp.route("/items/<int:item_id>", methods=["PATCH"])
def edit_item(item_id):
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        ok = update_draft(conn, creator_id, item_id, body.get("subject") or "", body.get("body") or "")
        return jsonify({"success": ok})
    finally:
        conn.close()


@autopilot_bp.route("/items/<int:item_id>/skip", methods=["POST"])
def skip(item_id):
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        skip_item(conn, creator_id, item_id)
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


@autopilot_bp.route("/approve", methods=["POST"])
def approve_batch():
    creator_id, conn, _creator, err = _guard()
    if err:
        return err
    try:
        ids = (request.get_json(silent=True) or {}).get("ids") or []
        scheduled = approve(conn, creator_id, ids)
        payload = _status_payload(conn, creator_id, True)
        payload["scheduled"] = scheduled
        return jsonify(payload)
    finally:
        conn.close()
