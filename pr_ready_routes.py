"""
PR-Ready API — wires scrape + mentor helpers into portfolio kit.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, jsonify, request, session
from flask_jwt_extended import get_jwt_identity
from psycopg2.extras import RealDictCursor

from pr_crm_routes import (
    check_media_kit_complete,
    get_creator_id_from_session,
    get_creator_unlock_balance,
    get_db_connection,
)
from services.creator_profile_scraper import CreatorProfileScraper, scrape_and_enrich_creator
from services.pr_ready import (
    auto_fill_kit_from_scrape,
    build_monetization_plan,
    compute_pr_ready_score,
    ensure_recent_posts_column,
    enrich_scrape_posts_via_embeds,
    generate_pitch_pack,
    merge_previous_post_engagement,
    recover_engagement_from_user_feed,
    rewrite_bio,
    score_brand_readiness,
    scrape_summary,
    fetch_success_story,
    _posts_have_engagement,
    _recent_posts_for_kit,
)


def _apply_free_peek_lock(fixes, checklist):
    """
    Fix 2 — Hunter.io peek: free users see why_blocks (the problem),
    but fix_steps / coaching evidence stay locked.
    """
    for fix in fixes:
        if fix.get("free"):
            continue
        fix["locked"] = True
        if not fix.get("why_blocks") and fix.get("why"):
            fix["why_blocks"] = fix["why"]
        # Keep problem statement visible; gate the treatment
        fix["fix_steps"] = None
        fix["tip"] = None
        fix["suggested_shoot"] = None
        fix["missing"] = None
        fix["evidence"] = None
        fix["cta"] = "See fix — $19/mo"
        fix["cta_action"] = "upgrade"
    for item in checklist:
        if item.get("free"):
            continue
        item["locked"] = True
        if not item.get("why_blocks") and item.get("why"):
            item["why_blocks"] = item["why"]
        item["fix_steps"] = None
        item["tip"] = None
        item["suggested_shoot"] = None
        item["missing"] = None
        item["evidence"] = None
        item["cta"] = "See fix — $19/mo"
        item["cta_action"] = "upgrade"


def _score_delta_fields(previous_score, report, action_label):
    new_score = int(report.get("score") or 0)
    prev = int(previous_score or 0)
    delta = new_score - prev
    out = {
        "previous_score": prev,
        "score_delta": delta,
        "score_delta_label": (
            f"+{delta} · {action_label}" if delta > 0 else None
        ),
    }
    return out

pr_ready_bp = Blueprint("pr_ready", __name__, url_prefix="/api/pr-ready")


def _session_user_id():
    user_id = session.get("user_id")
    if user_id:
        return user_id
    try:
        return get_jwt_identity()
    except Exception:
        return None


def _load_scrape(conn, user_id):
    if not user_id:
        return None
    return CreatorProfileScraper(conn).get_creator_profile(user_id)


def _account_email(conn, user_id):
    if not user_id:
        return ""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone() or {}
        return ((row.get("email") or "").strip())
    finally:
        cur.close()


def _creator_social(conn, creator_id):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        try:
            cursor.execute(
                """
                SELECT id, user_id, bio, niche, kit_tagline, kit_published, kit_slug,
                       social_handle, social_platform, followers_count, subscription_tier,
                       pitches_sent_this_week, last_pitch_reset,
                       rates_reel, rates_tiktok, rates_photo
                FROM creators WHERE id = %s
                """,
                (creator_id,),
            )
        except Exception:
            conn.rollback()
            cursor.execute(
                """
                SELECT id, user_id, bio, niche, kit_tagline, kit_published, kit_slug,
                       social_handle, social_platform, followers_count, subscription_tier,
                       pitches_sent_this_week, last_pitch_reset
                FROM creators WHERE id = %s
                """,
                (creator_id,),
            )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        cursor.close()


def _pitches_used_this_month(creator):
    """Mirror get_pitch_limits monthly reset (1st of month)."""
    if not creator:
        return 0
    from datetime import date

    used = int(creator.get("pitches_sent_this_week") or 0)
    last_reset = creator.get("last_pitch_reset")
    month_start = date.today().replace(day=1)
    if last_reset is None or last_reset < month_start:
        return 0
    return used


def _scrape_summary_for_tier(scrape, *, is_pro: bool):
    """Free sees draft thumbs; engagement stats are Pro-gated."""
    summary = scrape_summary(scrape)
    if is_pro or not summary:
        return summary
    for p in summary.get("recent_posts") or []:
        if isinstance(p, dict):
            p["views"] = None
            p["likes"] = None
            p["comments"] = None
            p["stats_locked"] = True
    return summary


def _maybe_recover_collab_email(conn, user_id, scrape):
    """
    If onboarding never captured the IG bio email (common when meta returns bio_len=0),
    try a lightweight search-snippet pass once and persist the address.
    Does not re-scrape posts.
    """
    if not scrape:
        return scrape
    from services.pr_ready import _extract_collab_email

    if _extract_collab_email(
        scrape.get("raw_bio"),
        scrape.get("collab_email_extracted"),
        scrape.get("biography"),
    ):
        return scrape

    # Already have a bio (just no collab email in it) — do not live-hammer IG search on every GET
    existing_bio = (scrape.get("raw_bio") or scrape.get("biography") or "").strip()
    if existing_bio:
        return scrape

    handle = (scrape.get("handle") or "").lstrip("@")
    platform = (scrape.get("primary_platform") or "instagram").lower()
    if not handle or platform != "instagram":
        return scrape

    try:
        from services.inhouse_social_scraper import _ig_from_search_snippets

        patch = _ig_from_search_snippets(handle) or {}
        bio = (patch.get("biography") or "").strip()
        email = _extract_collab_email(bio)
        if not bio and not email:
            return scrape

        scrape = dict(scrape)
        if bio:
            scrape["raw_bio"] = bio
        if email:
            scrape["has_collab_email"] = True
            scrape["collab_email_extracted"] = email
        cur = conn.cursor()
        if email:
            cur.execute(
                """
                UPDATE creator_profile_data
                SET raw_bio = CASE
                        WHEN %s <> '' THEN %s
                        ELSE raw_bio
                    END,
                    has_collab_email = TRUE,
                    collab_email_extracted = %s
                WHERE user_id = %s
                """,
                (bio, bio, email, user_id),
            )
            print(f"[pr-ready] recovered collab email for @{handle}")
        else:
            # Persist bio so subsequent GETs skip search (bio had no email)
            cur.execute(
                """
                UPDATE creator_profile_data
                SET raw_bio = %s
                WHERE user_id = %s AND (raw_bio IS NULL OR raw_bio = '')
                """,
                (bio, user_id),
            )
            print(f"[pr-ready] recovered bio (no email) for @{handle}")
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[pr-ready] email recovery skipped: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    return scrape


def _persist_scrape_posts(conn, user_id, scrape) -> None:
    """Save kit posts: recent_posts is jsonb, recent_post_thumbnails is text[]."""
    ensure_recent_posts_column(conn)
    thumbs = scrape.get("recent_post_thumbnails") or []
    if not isinstance(thumbs, list):
        thumbs = list(thumbs) if thumbs else []
    thumbs = [str(t) for t in thumbs if t]
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE creator_profile_data
            SET recent_posts = %s::jsonb,
                recent_post_thumbnails = %s
            WHERE user_id = %s
            """,
            (
                json.dumps(scrape.get("recent_posts") or []),
                thumbs,
                user_id,
            ),
        )
        conn.commit()
    finally:
        cur.close()


@pr_ready_bp.route("", methods=["GET"])
@pr_ready_bp.route("/", methods=["GET"])
def get_pr_ready():
    """Score + scrape summary + kit status for the logged-in creator."""
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conn = None
    try:
        conn = get_db_connection()
        creator = _creator_social(conn, creator_id)
        scrape = _load_scrape(conn, user_id)
        scrape = _maybe_recover_collab_email(conn, user_id, scrape)
        _complete, _msg, kit_status = check_media_kit_complete(creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")
        report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )

        # Free: peek why_blocks (problem), lock fix_steps (treatment). Pro: full.
        fixes = report["fixes"]
        checklist = report["checklist"]
        if not is_pro:
            _apply_free_peek_lock(fixes, checklist)

        unlock_balance = get_creator_unlock_balance(creator_id, conn=conn) or {}
        plan = build_monetization_plan(
            is_pro=is_pro,
            unlock_balance=unlock_balance,
            pitches_used=_pitches_used_this_month(creator),
            kit_post_count=int((kit_status or {}).get("post_count") or 0),
            score_capped=bool(report.get("score_capped")),
        )

        scrape_out = _scrape_summary_for_tier(scrape, is_pro=is_pro)
        account_email = _account_email(conn, user_id)

        return jsonify(
            {
                "success": True,
                "is_pro": is_pro,
                "score": report["score"],
                "raw_score": report.get("raw_score"),
                "score_capped": bool(report.get("score_capped")),
                "free_score_cap": report.get("free_score_cap"),
                "status": report["status"],
                "score_label": report.get("score_label") or "Hireability score",
                "score_promise": report.get("score_promise"),
                "projected_score": report.get("projected_score"),
                "projected_gain": report.get("projected_gain"),
                "top_gap_id": report.get("top_gap_id"),
                "manager": report.get("manager"),
                "manager_bar": report.get("manager_bar"),
                "boxes_checked": report["boxes_checked"],
                "boxes_total": report["boxes_total"],
                "checklist": checklist,
                "fixes": fixes,
                "data_quality": report.get("data_quality"),
                "scrape": scrape_out,
                "kit": kit_status,
                "plan": plan,
                "account_email": account_email,
                "monetization": {
                    "free_includes": [
                        "capped readiness score",
                        "1 AI bio rewrite",
                        "draft portfolio (3 posts, stats locked)",
                        "1 sample UGC hook",
                    ],
                    "pro_includes": [
                        "full fix coaching + evidence",
                        "engagement stats on portfolio posts",
                        "uncapped Ready score",
                        "portfolio view tracking",
                        "weekly UGC hooks + pitch pack",
                        "9-post portfolio + unlimited Brand PR unlocks",
                    ],
                    "price": plan.get("price") or "$19/mo",
                    "pitch": plan.get("pitch")
                    or "Free diagnoses. Pro makes you Ready.",
                    "plan": plan,
                },
                "creator": {
                    "bio": (creator or {}).get("bio"),
                    "kit_tagline": (creator or {}).get("kit_tagline"),
                    "kit_slug": (creator or {}).get("kit_slug"),
                    "niche": (creator or {}).get("niche"),
                    "handle": (scrape or {}).get("handle")
                    or (creator or {}).get("social_handle"),
                },
                "has_scrape": bool(scrape),
                "success_story": fetch_success_story(
                    conn, exclude_creator_id=creator_id
                ),
            }
        )
    except Exception as e:
        print(f"[pr-ready] get error: {e}")
        return jsonify({"error": "Failed to load PR-Ready"}), 500
    finally:
        if conn:
            conn.close()


@pr_ready_bp.route("/refresh-scrape", methods=["POST"])
def refresh_scrape():
    """Re-run in-house scrape using creator social handle.

    Body (optional): { "handle": "...", "platform": "instagram"|"tiktok" }
    When provided, persists handle/platform on creators then scrapes.
    """
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conn = None
    try:
        body = request.get_json(silent=True) or {}
        conn = get_db_connection()
        creator = _creator_social(conn, creator_id)
        if not creator:
            return jsonify({"error": "Creator not found"}), 404

        # Prefer existing scrape handle; else creators.social_*
        existing = _load_scrape(conn, user_id)
        body_handle = str(body.get("handle") or "").strip().lstrip("@")
        body_platform = str(body.get("platform") or "").strip().lower()

        handle = body_handle or (existing or {}).get("handle") or creator.get("social_handle")
        platform = (
            body_platform
            or (existing or {}).get("primary_platform")
            or creator.get("social_platform")
            or "instagram"
        )
        platform = str(platform).lower()
        if platform not in ("instagram", "tiktok"):
            platform = "instagram"

        if not handle:
            return jsonify(
                {
                    "error": "No social handle on file. Enter your Instagram or TikTok username to scan.",
                }
            ), 400

        handle = str(handle).lstrip("@").strip()
        if not re.match(r"^[a-zA-Z0-9._]{2,30}$", handle):
            return jsonify({"error": "Invalid username format"}), 400
        if ".." in handle:
            return jsonify({"error": "Username cannot have consecutive periods"}), 400

        # Persist handle so PR-Ready / For You can reuse it
        if body_handle or not creator.get("social_handle"):
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE creators
                SET social_handle = %s,
                    social_platform = %s
                WHERE id = %s
                """,
                (handle, platform, creator_id),
            )
            conn.commit()
            cursor.close()

        bio_fallback = ((existing or {}).get("raw_bio") or creator.get("bio") or "").strip()
        try:
            profile, _vision = scrape_and_enrich_creator(
                user_id,
                handle,
                platform,
                db_conn=conn,
                skip_minimums=True,
            )
            scrape = _load_scrape(conn, user_id) or profile
            stale = False
            warning = None
        except ValueError as e:
            # Live mirrors blocked (e.g. imginn 403) — keep last good audit usable
            if existing and (
                (existing.get("recent_post_thumbnails") or existing.get("recent_posts"))
                and (existing.get("raw_bio") or bio_fallback)
            ):
                print(f"[pr-ready] refresh-scrape soft-fail, using last scrape: {e}")
                scrape = existing
                if bio_fallback and not (scrape.get("raw_bio") or "").strip():
                    scrape = dict(scrape)
                    scrape["raw_bio"] = bio_fallback
                stale = True
                warning = (
                    "Instagram temporarily blocked a live re-scrape. "
                    "Showing your last successful audit — try again in a few minutes."
                )
            else:
                return jsonify({"error": str(e)}), 400

        _complete, _msg, kit_status = check_media_kit_complete(creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")
        report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )

        return jsonify(
            {
                "success": True,
                "scrape": scrape_summary(scrape),
                "score": report["score"],
                "raw_score": report.get("raw_score"),
                "score_capped": bool(report.get("score_capped")),
                "free_score_cap": report.get("free_score_cap"),
                "status": report["status"],
                "checklist": report["checklist"],
                "fixes": report["fixes"],
                "data_quality": report.get("data_quality"),
                "stale": stale,
                "warning": warning,
                "handle": handle,
                "platform": platform,
                "has_scrape": True,
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[pr-ready] refresh-scrape error: {e}")
        return jsonify({"error": "Scrape failed. Try again shortly."}), 500
    finally:
        if conn:
            conn.close()


@pr_ready_bp.route("/rewrite-bio", methods=["POST"])
def rewrite_bio_route():
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conn = None
    try:
        body = request.get_json(silent=True) or {}
        conn = get_db_connection()
        scrape = _load_scrape(conn, user_id)
        if not scrape:
            return jsonify({"error": "No scrape data yet. Refresh profile first."}), 400

        scrape = _hydrate_bio_from_crawler_if_thin(conn, user_id, scrape)

        # Prefer explicit PR email from the manager UI, then account email
        collab_email = str(body.get("email") or "").strip()
        if collab_email.lower().startswith("mailto:"):
            collab_email = collab_email[7:].strip()
        if not collab_email:
            collab_email = (
                scrape.get("collab_email_extracted")
                or _extract_collab_email_safe(scrape)
                or ""
            )
        if not collab_email:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            cur.close()
            account = ((row or {}).get("email") or "").strip()
            if account and "@" in account:
                collab_email = account

        if not collab_email:
            return jsonify(
                {
                    "error": "Add a public creator email so brands can reach you.",
                    "needs_email": True,
                }
            ), 400

        # Basic email shape check
        if not re.search(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", collab_email):
            return jsonify({"error": "Enter a valid email address", "needs_email": True}), 400

        creator = _creator_social(conn, creator_id)
        _complete, _msg, kit_status = check_media_kit_complete(creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")
        prev_report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )
        previous_score = int(prev_report.get("score") or 0)

        result = rewrite_bio(scrape, collab_email=collab_email)
        apply = bool(body.get("apply", True))
        if apply:
            bio_text = (result.get("bio") or "").strip()
            tagline = (result.get("tagline") or "").strip() or None
            email_out = (result.get("email") or collab_email or "").strip()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE creators
                SET bio = %s,
                    kit_tagline = COALESCE(%s, kit_tagline)
                WHERE id = %s
                """,
                (bio_text, tagline, creator_id),
            )
            # Keep scrape bio in sync so PR-Ready reload shows the saved rewrite
            try:
                cursor.execute(
                    """
                    UPDATE creator_profile_data
                    SET raw_bio = %s,
                        has_collab_email = TRUE,
                        collab_email_extracted = %s
                    WHERE user_id = %s
                    """,
                    (bio_text, email_out, user_id),
                )
            except Exception as sync_err:
                print(f"[pr-ready] scrape bio sync skipped: {sync_err}")
            conn.commit()
            cursor.close()

        report = compute_pr_ready_score(
            scrape if not apply else _load_scrape(conn, user_id) or scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(result.get("bio") if apply else None)
            or (creator or {}).get("bio"),
            creator_profile=creator,
        )
        fixes = report.get("fixes") or []
        checklist = report.get("checklist") or []
        if not is_pro:
            _apply_free_peek_lock(fixes, checklist)

        return jsonify(
            {
                "success": True,
                **result,
                "applied": apply,
                "score": report.get("score"),
                "fixes": fixes,
                "checklist": checklist,
                "manager": report.get("manager"),
                "projected_score": report.get("projected_score"),
                **_score_delta_fields(previous_score, report, "bio optimized"),
            }
        )
    except Exception as e:
        print(f"[pr-ready] rewrite-bio error: {e}")
        return jsonify({"error": "Bio rewrite failed"}), 500
    finally:
        if conn:
            conn.close()


def _extract_collab_email_safe(scrape):
    from services.pr_ready import _extract_collab_email

    if not scrape:
        return None
    return _extract_collab_email(
        scrape.get("raw_bio"),
        scrape.get("collab_email_extracted"),
        scrape.get("biography"),
    )


def _hydrate_bio_from_crawler_if_thin(conn, user_id, scrape):
    """
    Older scrapes often stored display-name-only bios when meta parsing dropped
    multi-line description tags. One crawler pass recovers the real IG bio + email.
    """
    from services.pr_ready import _extract_collab_email

    if not scrape:
        return scrape
    raw = (scrape.get("raw_bio") or "").strip()
    email = scrape.get("collab_email_extracted") or _extract_collab_email(raw)
    # Already have a real bio with contact email — leave alone
    if email and len(raw) >= 40:
        return scrape
    handle = (scrape.get("handle") or "").lstrip("@")
    platform = (scrape.get("primary_platform") or "instagram").lower()
    if not handle or platform != "instagram":
        return scrape
    try:
        from services.inhouse_social_scraper import _ig_from_crawler_html

        user = _ig_from_crawler_html(handle) or {}
        bio = (user.get("biography") or "").strip()
        if not bio or len(bio) <= len(raw):
            return scrape
        recovered_email = _extract_collab_email(bio) or email
        scrape = dict(scrape)
        scrape["raw_bio"] = bio
        if recovered_email:
            scrape["has_collab_email"] = True
            scrape["collab_email_extracted"] = recovered_email
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE creator_profile_data
            SET raw_bio = %s,
                has_collab_email = CASE WHEN %s <> '' THEN TRUE ELSE has_collab_email END,
                collab_email_extracted = CASE
                    WHEN %s <> '' THEN %s
                    ELSE collab_email_extracted
                END
            WHERE user_id = %s
            """,
            (bio, recovered_email or "", recovered_email or "", recovered_email or "", user_id),
        )
        conn.commit()
        cur.close()
        print(f"[pr-ready] hydrated real IG bio for @{handle} (len={len(bio)})")
    except Exception as e:
        print(f"[pr-ready] bio hydrate skipped: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    return scrape


@pr_ready_bp.route("/auto-kit", methods=["POST"])
def auto_kit_route():
    """Pull real posts + engagement stats from scrape into My Kit."""
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    body = request.get_json(silent=True) or {}
    conn = None
    try:
        conn = get_db_connection()
        ensure_recent_posts_column(conn)
        creator = _creator_social(conn, creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")

        scrape = _load_scrape(conn, user_id)
        if not scrape:
            return jsonify({"error": "No scrape data yet. Complete onboarding scrape first."}), 400

        _complete0, _msg0, kit_before = check_media_kit_complete(creator_id)
        prev_report = compute_pr_ready_score(
            scrape,
            kit_before,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )
        previous_score = int(prev_report.get("score") or 0)

        engagement_warning = None
        kit_posts = _recent_posts_for_kit(scrape)

        # Do NOT live-rescrape the whole profile — IG rate limits make it useless and slow.
        # Prefer onboarding recent_posts; if thumbs exist without stats, one user_feed pass recovers likes/views.
        if kit_posts and not _posts_have_engagement(kit_posts):
            recovered = recover_engagement_from_user_feed(scrape, limit=9 if is_pro else 6)
            if recovered:
                kit_posts = _recent_posts_for_kit(scrape)
                try:
                    _persist_scrape_posts(conn, user_id, scrape)
                except Exception as persist_err:
                    print(f"[pr-ready] persist feed-recovered posts failed: {persist_err}")
                    conn.rollback()

        if kit_posts:
            enriched = enrich_scrape_posts_via_embeds(scrape)
            if enriched:
                kit_posts = _recent_posts_for_kit(scrape)
                try:
                    _persist_scrape_posts(conn, user_id, scrape)
                except Exception as persist_err:
                    print(f"[pr-ready] persist enriched posts failed: {persist_err}")
                    conn.rollback()

        thumbs = scrape.get("recent_post_thumbnails") or []
        if not thumbs and not kit_posts:
            return jsonify({
                "error": (
                    "Your last Instagram/TikTok scrape has no recent posts, so your portfolio can’t auto-fill. "
                    "Reconnect Instagram in Settings, wait for a scrape that includes posts, then try again."
                ),
                "code": "scrape_missing_posts",
            }), 400

        if not _posts_have_engagement(kit_posts):
            engagement_warning = (
                "Couldn’t pull live likes/views from Instagram right now. "
                "Portfolio filled from your scraped posts — open Portfolio to tweak stats if needed."
            )
            print(f"[pr-ready] auto-kit proceeding without engagement ({len(kit_posts)} posts)")

        # AI Manager free path: auto-publish when 3+ posts land (kitchen sample)
        result = auto_fill_kit_from_scrape(
            conn,
            creator_id,
            scrape,
            publish=True,
            rewritten_bio=body.get("bio"),
            tagline=body.get("tagline"),
            is_pro=is_pro,
            max_posts=9 if is_pro else 3,
        )
        _complete, _msg, kit_status = check_media_kit_complete(creator_id)
        report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )
        fixes = report.get("fixes") or []
        checklist = report.get("checklist") or []
        if not is_pro:
            _apply_free_peek_lock(fixes, checklist)

        return jsonify(
            {
                "success": True,
                **result,
                "kit": kit_status,
                "score": report["score"],
                "raw_score": report.get("raw_score"),
                "score_capped": bool(report.get("score_capped")),
                "free_score_cap": report.get("free_score_cap"),
                "status": report["status"],
                "checklist": checklist,
                "fixes": fixes,
                "manager": report.get("manager"),
                "projected_score": report.get("projected_score"),
                "is_pro": is_pro,
                "publish_locked": False,
                "free_post_limit": 3,
                "scrape": _scrape_summary_for_tier(scrape, is_pro=is_pro),
                "warning": engagement_warning,
                "engagement_partial": bool(engagement_warning),
                **_score_delta_fields(previous_score, report, "portfolio built"),
            }
        )
    except Exception as e:
        print(f"[pr-ready] auto-kit error: {e}")
        if conn:
            conn.rollback()
        return jsonify({"error": "Failed to auto-fill kit"}), 500
    finally:
        if conn:
            conn.close()


@pr_ready_bp.route("/brand-scores", methods=["POST"])
def brand_scores_route():
    """Per-brand hireability + top-3 needs (Fix 6)."""
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    body = request.get_json(silent=True) or {}
    brands = body.get("brands") or []
    if not isinstance(brands, list) or not brands:
        return jsonify({"success": True, "brands": []})

    conn = None
    try:
        conn = get_db_connection()
        creator = _creator_social(conn, creator_id)
        scrape = _load_scrape(conn, user_id)
        _complete, _msg, kit_status = check_media_kit_complete(creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")
        report = compute_pr_ready_score(
            scrape,
            kit_status,
            is_pro=is_pro,
            creator_bio=(creator or {}).get("bio"),
            creator_profile=creator,
        )
        scored = [
            score_brand_readiness(
                scrape,
                b if isinstance(b, dict) else {},
                checklist=report.get("checklist") or [],
                creator_bio=(creator or {}).get("bio"),
            )
            for b in brands[:8]
        ]
        return jsonify({"success": True, "brands": scored})
    except Exception as e:
        print(f"[pr-ready] brand-scores error: {e}")
        return jsonify({"error": "Failed to score brands"}), 500
    finally:
        if conn:
            conn.close()


@pr_ready_bp.route("/pitch-pack", methods=["POST"])
def pitch_pack_route():
    creator_id = get_creator_id_from_session()
    user_id = _session_user_id()
    if not creator_id or not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conn = None
    try:
        conn = get_db_connection()
        creator = _creator_social(conn, creator_id)
        tier = (creator or {}).get("subscription_tier") or "free"
        is_pro = tier in ("pro", "elite")

        scrape = _load_scrape(conn, user_id)
        if not scrape:
            return jsonify({"error": "No scrape data yet. Refresh profile first."}), 400

        pack = generate_pitch_pack(scrape)

        # Free: teaser week plan (1 shoot) → upgrade for full week
        if not is_pro:
            week = pack.get("weekly_plan") or []
            return jsonify(
                {
                    "success": True,
                    "is_pro": False,
                    "locked": True,
                    "focus": pack.get("focus"),
                    "niche": pack.get("niche"),
                    "weekly_plan": week[:1],
                    "weekly_plan_locked": week[1:4],
                    "pitches": (pack.get("pitches") or [])[:1],
                    "ugc_hooks": (pack.get("ugc_hooks") or [])[:1],
                    "rate_card_tips": [],
                    "upgrade_required": True,
                    "upgrade_message": "Free includes 1 sample shoot. Pro unlocks the full weekly portfolio plan.",
                }
            )

        return jsonify({"success": True, "is_pro": True, "locked": False, **pack})
    except Exception as e:
        print(f"[pr-ready] pitch-pack error: {e}")
        return jsonify({"error": "Failed to generate pitch pack"}), 500
    finally:
        if conn:
            conn.close()
