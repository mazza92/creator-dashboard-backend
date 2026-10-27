"""Brand workspace: logged-in brand accounts on top of the existing users/brands tables.

A brand signs up from /for-brands (email + password or Google), lands in /brand/workspace and waits for
admin approval, like creators do. Approved brands get Brand Scan limits, scan history, saved creators,
and their live gifted roster (the existing brand_pr_campaigns token roster) inside the workspace.

brands rows predate approval: a NULL approval_status is a legacy account and counts as approved.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

FREE_SCANS_PER_DAY = 3
APPROVED = ("approved",)
PENDING, REJECTED = "pending", "rejected"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_schema_ready = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema(conn) -> None:
    """Idempotent, once per process. ADD COLUMN IF NOT EXISTS never rewrites the brands table."""
    global _schema_ready
    if _schema_ready:
        return
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE brands
          ADD COLUMN IF NOT EXISTS approval_status TEXT,
          ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
          ADD COLUMN IF NOT EXISTS pr_brand_id INTEGER,
          ADD COLUMN IF NOT EXISTS opportunity_id INTEGER,
          ADD COLUMN IF NOT EXISTS signup_source TEXT,
          ADD COLUMN IF NOT EXISTS signup_niche TEXT,
          ADD COLUMN IF NOT EXISTS workspace_created_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS app_url TEXT,
          ADD COLUMN IF NOT EXISTS profile_completed_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS signup_method TEXT,
          ADD COLUMN IF NOT EXISTS free_scan_brand TEXT,
          ADD COLUMN IF NOT EXISTS outreach_ref TEXT
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_workspace_scans (
          id SERIAL PRIMARY KEY,
          brand_id INTEGER NOT NULL,
          scanned_key TEXT NOT NULL,
          scanned_name TEXT NOT NULL,
          scan_id INTEGER,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS brand_workspace_scans_brand_idx "
                "ON brand_workspace_scans (brand_id, created_at DESC)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_saved_creators (
          brand_id INTEGER NOT NULL,
          creator_ref TEXT NOT NULL,
          card JSONB NOT NULL DEFAULT '{}'::jsonb,
          from_scan TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (brand_id, creator_ref)
        )""")
    for table in ("brand_workspace_scans", "brand_saved_creators"):
        cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    conn.commit()
    # Which roster a campaign submission minted. Checked first: ALTER on opportunities locks a hot table.
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'opportunities' AND column_name = 'roster_campaign_id'""")
    if not cur.fetchone():
        try:
            cur.execute("SET LOCAL lock_timeout = '2s'")
            cur.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS roster_campaign_id INTEGER")
            conn.commit()
        except Exception as err:
            conn.rollback()
            print(f"[brand-workspace] roster_campaign_id column skipped: {err}")
            return
    _schema_ready = True


# ----- accounts ---------------------------------------------------------------------------

def clean_email(raw: Any) -> str:
    email = str(raw or "").strip().lower()[:254]
    return email if EMAIL_RE.match(email) else ""


def clean_text(raw: Any, limit: int = 200) -> str:
    return " ".join(str(raw or "").split())[:limit]


def clean_website(raw: Any) -> str:
    site = clean_text(raw, 300)
    if site and not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    return site


def is_approved(status: Optional[str]) -> bool:
    """NULL is a legacy brand account (created before approval existed): treat as approved."""
    return status is None or status in APPROVED


def find_user(conn, email: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, role, password FROM users WHERE LOWER(email) = LOWER(%s)
                   ORDER BY (role = 'brand') DESC, id LIMIT 1""", (email,))
    row = cur.fetchone()
    return dict(row) if row else None


def brand_for_user(conn, user_id: int) -> Optional[Dict[str, Any]]:
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT id, name, website, app_url, category, logo, approval_status, pr_brand_id, opportunity_id,
                          signup_niche, profile_completed_at, free_scan_brand, signup_source, outreach_ref
                   FROM brands WHERE user_id = %s
                   ORDER BY (approval_status IS NULL OR approval_status = 'approved') DESC,
                            (approval_status = 'pending') DESC, id DESC
                   LIMIT 1""", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def provisional_name(email: str) -> str:
    """'ana@glow-co.com' -> 'Glow Co' until the brand fills in step 2. Free-mail domains give ''."""
    domain = email.split("@")[-1].split(".")[0] if "@" in email else ""
    if domain in FREE_MAIL:
        return ""
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", domain) if w)


FREE_MAIL = {"gmail", "googlemail", "yahoo", "hotmail", "outlook", "live", "icloud", "me", "aol", "proton",
             "protonmail", "gmx", "mail", "yandex"}


def create_brand_account(conn, *, email: str, password_hash: Optional[str], first_name: str = "", last_name: str = "",
                         brand_name: str = "", website: str = "", category: str = "", niche: str = "",
                         source: str = "for-brands", country: str = "", method: str = "email") -> Dict[str, int]:
    """users row (role brand) + brands row (pending). Caller checks the email is free first. The brand
    details can come later (signup step 2): brands.name is NOT NULL, so it starts from the email domain."""
    ensure_schema(conn)
    brand_name = brand_name or provisional_name(email) or "New brand"
    cur = conn.cursor()
    cur.execute("""INSERT INTO users (first_name, last_name, email, phone, country, password, role, is_verified,
                                      last_login)
                   VALUES (%s, %s, %s, '', %s, %s, 'brand', TRUE, NOW()) RETURNING id""",
                (first_name, last_name, email, country or "", password_hash))
    user_id = int(cur.fetchone()[0])
    cur.execute("""INSERT INTO brands (name, description, category, website, user_id, approval_status,
                                       signup_source, signup_niche, signup_method, workspace_created_at)
                   VALUES (%s, '', %s, %s, %s, 'pending', %s, %s, %s, NOW()) RETURNING id""",
                (brand_name, category or None, website or None, user_id, source[:40], niche[:40] or None,
                 method[:20]))
    brand_id = int(cur.fetchone()[0])
    conn.commit()
    return {"user_id": user_id, "brand_id": brand_id}


def outreach_mark(conn, ref: str, event: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """Credit a cold email (its ref code) with a funnel step. Never raises into the caller."""
    if not ref:
        return
    try:
        from services import outreach_attribution

        outreach_attribution.mark(conn.cursor(cursor_factory=RealDictCursor), ref, event, meta)
        conn.commit()
    except Exception as err:
        conn.rollback()
        print(f"[brand-workspace] outreach mark skipped ({event}): {type(err).__name__}: {err}")


def attach_outreach_ref(conn, brand_id: int, ref: str) -> None:
    ref = clean_text(ref, 40)
    if not ref:
        return
    cur = conn.cursor()
    cur.execute("UPDATE brands SET outreach_ref = COALESCE(outreach_ref, %s) WHERE id = %s", (ref, brand_id))
    conn.commit()
    outreach_mark(conn, ref, "signed_up", {"workspace_brand_id": brand_id})


def update_profile(conn, brand_id: int, *, name: str, website: str = "", app_url: str = "",
                   category: str = "") -> None:
    """Signup step 2: brand name, website or app store link, and the category we match creator niches on."""
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""UPDATE brands SET name = %s, website = COALESCE(%s, website), app_url = %s,
                     category = COALESCE(%s, category), signup_niche = COALESCE(%s, signup_niche),
                     profile_completed_at = NOW()
                   WHERE id = %s""",
                (name, website or None, app_url or None, category or None, category or None, brand_id))
    conn.commit()


def clean_app_url(raw: Any) -> str:
    """App Store / Google Play links only."""
    url = clean_website(raw)
    return url if re.search(r"(apps\.apple\.com|play\.google\.com)", url, re.I) else ""


def import_free_scan(conn, brand_id: int, target: Optional[Dict[str, Any]], scan_id: Optional[int] = None) -> bool:
    """The scan a visitor ran on the landing page becomes the first scan in their workspace."""
    if not target:
        return False
    ensure_schema(conn)
    record_scan(conn, brand_id, target["key"], target["name"], scan_id)
    cur = conn.cursor()
    cur.execute("UPDATE brands SET free_scan_brand = %s WHERE id = %s", (target["name"][:80], brand_id))
    conn.commit()
    return True


def scanned_keys(conn, brand_id: int) -> List[str]:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT scanned_key FROM brand_workspace_scans WHERE brand_id = %s", (brand_id,))
    return [r[0] for r in cur.fetchall()]


def pending_scan_allowed(conn, brand_id: int, key: str) -> bool:
    """Brands awaiting approval get one competitor scan: the one they ran (or carried over), any number of times."""
    keys = [k for k in scanned_keys(conn, brand_id) if not k.startswith("insight:")]
    return not keys or key in keys


def link_opportunity(conn, brand_id: int, opportunity_id: int, email: str) -> bool:
    """Attach the /for-brands campaign submission, only when it was sent from this account's email."""
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""UPDATE brands SET opportunity_id = %s
                   WHERE id = %s AND EXISTS (SELECT 1 FROM opportunities o WHERE o.id = %s
                                             AND LOWER(o.brand_email) = LOWER(%s))""",
                (opportunity_id, brand_id, opportunity_id, email))
    ok = cur.rowcount > 0
    conn.commit()
    return ok


def _host(url: Any) -> str:
    host = str(url or "").lower().split("//")[-1].split("/")[0]
    return host[4:] if host.startswith("www.") else host


def resolve_pr_brand(conn, brand: Dict[str, Any], email: str) -> Optional[int]:
    """The directory/roster brand (pr_brands) behind this account: stored link, the campaign submission's
    published brand, the contact email, then the website host. Stores what it finds."""
    if brand.get("pr_brand_id"):
        return int(brand["pr_brand_id"])
    cur = conn.cursor()
    found = None
    if brand.get("opportunity_id"):
        cur.execute("SELECT pr_brand_id FROM opportunities WHERE id = %s", (brand["opportunity_id"],))
        row = cur.fetchone()
        found = row[0] if row and row[0] else None
    if not found and email:
        cur.execute("SELECT id FROM pr_brands WHERE LOWER(contact_email) = LOWER(%s) ORDER BY id LIMIT 1", (email,))
        row = cur.fetchone()
        found = row[0] if row else None
    host = _host(brand.get("website"))
    if not found and host and "." in host:
        cur.execute("""SELECT id FROM pr_brands WHERE LOWER(website) LIKE %s ORDER BY id LIMIT 1""",
                    (f"%{host}%",))
        row = cur.fetchone()
        found = row[0] if row else None
    if found:
        cur.execute("UPDATE brands SET pr_brand_id = %s WHERE id = %s", (found, brand["id"]))
        conn.commit()
        return int(found)
    return None


def ensure_pr_brand(conn, brand: Dict[str, Any], email: str) -> int:
    """The pr_brands row billing hangs off. A brand that hasn't had a campaign published yet gets a private draft
    row (not listed, not accepting applications) so it can subscribe right away. Its campaigns point at that row,
    so publishing one later fills in this same row instead of creating a second one."""
    found = resolve_pr_brand(conn, brand, email)
    if found:
        return found
    cur = conn.cursor()
    name = brand.get("name") or provisional_name(email) or "Brand"
    # No slug: it stays out of the sitemap and public pages until publishing gives it one.
    cur.execute("""INSERT INTO pr_brands (brand_name, website, contact_email, category, status, accepting_pr,
                                          open_pr_featured, notes, created_at)
                   VALUES (%s, %s, %s, %s, 'draft', FALSE, FALSE, 'Brand workspace account (billing)', NOW())
                   RETURNING id""",
                (name, brand.get("website") or brand.get("app_url"), email or None, brand.get("category")))
    pr_id = int(cur.fetchone()[0])
    cur.execute("UPDATE brands SET pr_brand_id = %s WHERE id = %s", (pr_id, brand["id"]))
    cur.execute("""UPDATE opportunities SET pr_brand_id = %s
                   WHERE pr_brand_id IS NULL AND (id = %s OR LOWER(brand_email) = LOWER(%s))""",
                (pr_id, brand.get("opportunity_id") or 0, email or ""))
    conn.commit()
    return pr_id


def live_roster(conn, brand: Dict[str, Any], email: str) -> Optional[Dict[str, Any]]:
    """The roster of this account's own campaigns (in progress first), never another account's on the same
    directory brand."""
    items = [c for c in campaigns(conn, brand, email) if c.get("roster_token")]
    items.sort(key=lambda c: c["status"] not in ("active", "locked"))
    if not items:
        # Brands that ran a roster before the workspace existed: only when the directory brand is theirs by
        # contact email, and only an in-progress roster.
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT c.token, c.status, c.title FROM brand_pr_campaigns c JOIN pr_brands b ON b.id = c.brand_id
                       WHERE LOWER(b.contact_email) = LOWER(%s) AND c.status IN ('active', 'locked')
                       ORDER BY c.id DESC LIMIT 1""", (email or "",))
        row = cur.fetchone()
        return dict(row) if row and email else None
    return {"token": items[0]["roster_token"], "status": items[0]["status"], "title": items[0].get("product_name")}


def billing(conn, pr_brand_id: Optional[int]) -> Dict[str, Any]:
    if not pr_brand_id:
        return {"subscribed": False, "status": "none"}
    try:
        from services.brand_billing import billing_public_summary
        summary = billing_public_summary(conn.cursor(cursor_factory=RealDictCursor), pr_brand_id)
        conn.commit()
        return summary or {"subscribed": False, "status": "none"}
    except Exception as err:
        print(f"[brand-workspace] billing lookup skipped: {type(err).__name__}: {err}")
        conn.rollback()
        return {"subscribed": False, "status": "none"}


# ----- campaigns ------------------------------------------------------------------------------

def campaigns(conn, brand: Dict[str, Any], email: str) -> List[Dict[str, Any]]:
    """The brand's campaign submissions (newest first) with their live roster when published."""
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    # The roster this submission minted. Older submissions (before roster_campaign_id) fall back to an
    # in-progress roster of the same brand created after the submission, never an earlier, finished one.
    cur.execute("""
        SELECT o.id, o.product_name, o.status, o.created_at, o.pr_brand_id, o.campaign_description,
               o.creator_count_range, o.pr_value_usd,
               c.token AS roster_token, c.status AS roster_status, c.locked_at, c.shipped_at
        FROM opportunities o
        LEFT JOIN LATERAL (SELECT token, status, locked_at, shipped_at FROM brand_pr_campaigns
                           WHERE (o.roster_campaign_id IS NOT NULL AND id = o.roster_campaign_id)
                              OR (o.roster_campaign_id IS NULL AND brand_id = o.pr_brand_id
                                  AND status IN ('active', 'locked')
                                  AND created_at >= o.created_at - INTERVAL '1 day')
                           ORDER BY id DESC LIMIT 1) c ON TRUE
        WHERE o.id = %s OR LOWER(o.brand_email) = LOWER(%s)
        ORDER BY o.created_at DESC NULLS LAST, o.id DESC
        LIMIT 20""", (brand.get("opportunity_id") or 0, email))
    out = []
    for r in cur.fetchall():
        r = dict(r)
        if r["status"] == "pending":  # a campaign in review can share pr_brand_id with an earlier live one
            r["roster_token"] = r["roster_status"] = None
        state = r["roster_status"] if r.get("roster_token") else ("in_review" if r["status"] == "pending" else r["status"])
        out.append({"id": r["id"], "product_name": r["product_name"], "status": state,
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "roster_token": r.get("roster_token"), "creator_count_range": r.get("creator_count_range"),
                    "pr_value_usd": r.get("pr_value_usd"), "description": r.get("campaign_description")})
    return out


LIVE = ("active", "locked")


def campaign_gate(approved: bool, subscribed: bool, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Can this brand start another campaign? The first one is free; any more needs the $299 plan."""
    if not approved:
        return {"can_create": False, "reason": "pending"}
    if subscribed:
        return {"can_create": True, "reason": None}
    if not items:
        return {"can_create": True, "reason": "first_free"}
    has_roster = any(c.get("roster_token") for c in items)
    return {"can_create": False, "reason": "paywall" if has_roster else "first_in_review"}


# ----- scan allowance ------------------------------------------------------------------------

def scans_today(conn, brand_id: int) -> List[str]:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT scanned_key FROM brand_workspace_scans
                   WHERE brand_id = %s AND created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
                (brand_id,))
    return [r[0] for r in cur.fetchall()]


def scan_allowance(conn, brand_id: int, key: str, subscribed: bool) -> Dict[str, Any]:
    """Approved brands: 3 different brands a day, unlimited once subscribed. Re-scanning a brand already
    scanned today is always free (it's served from the cache)."""
    today = scans_today(conn, brand_id)
    if subscribed:
        return {"allowed": True, "used": len(today), "limit": None}
    allowed = key in today or len(today) < FREE_SCANS_PER_DAY
    return {"allowed": allowed, "used": len(today), "limit": FREE_SCANS_PER_DAY}


def record_scan(conn, brand_id: int, key: str, name: str, scan_id: Optional[int]) -> None:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""INSERT INTO brand_workspace_scans (brand_id, scanned_key, scanned_name, scan_id)
                   VALUES (%s, %s, %s, %s)""", (brand_id, key, name[:80], scan_id))
    conn.commit()


def scan_history(conn, brand_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT DISTINCT ON (scanned_key) scanned_key AS key, scanned_name AS name, scan_id, created_at
                   FROM brand_workspace_scans WHERE brand_id = %s
                   ORDER BY scanned_key, created_at DESC""", (brand_id,))
    rows = sorted((dict(r) for r in cur.fetchall()), key=lambda r: r["created_at"], reverse=True)[:limit]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return rows


# ----- saved creators --------------------------------------------------------------------------

def creator_ref(card: Dict[str, Any]) -> str:
    return clean_text(card.get("handle") or card.get("preview_url") or "", 200).lower()


def save_creator(conn, brand_id: int, card: Dict[str, Any], from_scan: str = "") -> bool:
    ensure_schema(conn)
    ref = creator_ref(card)
    if not ref:
        return False
    keep = {k: card.get(k) for k in ("handle", "preview_url", "avatar_url", "followers", "niches", "country",
                                     "platform", "thumbnails", "avg_views", "engagement_rate")
            if card.get(k) is not None}
    cur = conn.cursor()
    cur.execute("""INSERT INTO brand_saved_creators (brand_id, creator_ref, card, from_scan) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (brand_id, creator_ref) DO UPDATE SET card = EXCLUDED.card""",
                (brand_id, ref, json.dumps(keep), clean_text(from_scan, 80) or None))
    conn.commit()
    return True


def unsave_creator(conn, brand_id: int, ref: str) -> None:
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("DELETE FROM brand_saved_creators WHERE brand_id = %s AND creator_ref = %s",
                (brand_id, clean_text(ref, 200).lower()))
    conn.commit()


def saved_creators(conn, brand_id: int) -> List[Dict[str, Any]]:
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT creator_ref, card, from_scan, created_at FROM brand_saved_creators
                   WHERE brand_id = %s ORDER BY created_at DESC""", (brand_id,))
    out = []
    for r in cur.fetchall():
        card = r["card"] if isinstance(r["card"], dict) else json.loads(r["card"] or "{}")
        out.append({**card, "ref": r["creator_ref"], "from_scan": r["from_scan"],
                    "saved_at": r["created_at"].isoformat()})
    return out


# ----- admin --------------------------------------------------------------------------------

def list_accounts(conn, status: str = PENDING, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    where = "b.approval_status = %s" if status in (PENDING, REJECTED, "approved") else "b.approval_status IS NOT NULL"
    args = (status,) if status in (PENDING, REJECTED, "approved") else ()
    cur.execute(f"""
        SELECT b.id, b.name, b.website, b.app_url, b.category, b.approval_status, b.signup_source, b.signup_niche,
               b.signup_method, b.free_scan_brand, b.profile_completed_at,
               b.opportunity_id, b.pr_brand_id, b.workspace_created_at, b.approved_at, b.rejection_reason,
               u.id AS user_id, u.email, u.first_name, u.last_name, u.last_login,
               o.product_name, o.status AS opportunity_status, o.campaign_description, o.pr_value_usd,
               o.creator_count_range, o.shipping_regions, o.follower_ranges, o.content_types,
               o.application_deadline, o.additional_notes,
               (SELECT COUNT(*) FROM brand_workspace_scans w WHERE w.brand_id = b.id) AS scans_total,
               (SELECT ARRAY_AGG(DISTINCT w.scanned_name) FROM brand_workspace_scans w WHERE w.brand_id = b.id
                  AND w.scanned_key NOT LIKE 'insight:%%') AS scanned_brands
        FROM brands b JOIN users u ON u.id = b.user_id
        LEFT JOIN opportunities o ON o.id = b.opportunity_id
        WHERE {where}
        ORDER BY COALESCE(b.workspace_created_at, b.approved_at) DESC NULLS LAST, b.id DESC
        LIMIT %s""", args + (limit,))
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        for k in ("workspace_created_at", "approved_at", "profile_completed_at", "last_login", "application_deadline"):
            r[k] = r[k].isoformat() if r.get(k) else None
        rows.append(r)
    plans = _billing_status(conn, [r["pr_brand_id"] for r in rows if r.get("pr_brand_id")])
    for r in rows:
        r["plan"] = "pro" if plans.get(r.get("pr_brand_id")) == "active" else "free"
    return rows


def _billing_status(conn, pr_ids: List[int]) -> Dict[int, str]:
    if not pr_ids:
        return {}
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('brand_billing')")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("SELECT brand_id, status FROM brand_billing WHERE brand_id = ANY(%s)", (pr_ids,))
    return {int(b): s for b, s in cur.fetchall()}


def set_plan(conn, brand_id: int, pro: bool) -> Optional[str]:
    """Admin comp: put a brand account on Pro (or back to free) without Stripe. Returns an error or None."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT b.*, u.email FROM brands b JOIN users u ON u.id = b.user_id WHERE b.id = %s", (brand_id,))
    row = cur.fetchone()
    if not row:
        return "Brand account not found"
    brand = dict(row)
    from services.brand_billing import get_or_create_brand_billing

    pr_id = ensure_pr_brand(conn, brand, brand.get("email") or "")
    bill = get_or_create_brand_billing(conn.cursor(cursor_factory=RealDictCursor), pr_id, email=brand.get("email"))
    if not pro and bill.get("stripe_subscription_id") and bill.get("status") == "active":
        conn.rollback()
        return "This brand pays through Stripe. Cancel it in Stripe instead."
    cur = conn.cursor()
    cur.execute("UPDATE brand_billing SET status = %s, updated_at = NOW() WHERE brand_id = %s",
                ("active" if pro else "none", pr_id))
    conn.commit()
    return None


def decide(conn, brand_id: int, approve: bool, reason: str = "") -> Optional[Dict[str, Any]]:
    """Approve or reject. Returns {email, name, first_name} for the decision email, or None if not found."""
    ensure_schema(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if approve:
        cur.execute("""UPDATE brands SET approval_status = 'approved', approved_at = NOW(), rejected_at = NULL,
                         rejection_reason = NULL WHERE id = %s RETURNING user_id, name""", (brand_id,))
    else:
        cur.execute("""UPDATE brands SET approval_status = 'rejected', rejected_at = NOW(), rejection_reason = %s
                       WHERE id = %s RETURNING user_id, name""", (clean_text(reason, 500) or None, brand_id))
    row = cur.fetchone()
    if not row:
        conn.rollback()
        return None
    cur.execute("SELECT email, first_name FROM users WHERE id = %s", (row["user_id"],))
    user = cur.fetchone() or {}
    if approve and user.get("email"):
        # The same email can hold more than one pending brand row (signed up twice, or with different capitals).
        # Approve them all, so whichever one the person logs into is approved.
        cur.execute("""UPDATE brands SET approval_status = 'approved', approved_at = NOW()
                       WHERE approval_status = 'pending'
                         AND user_id IN (SELECT id FROM users WHERE LOWER(email) = LOWER(%s))""", (user["email"],))
    conn.commit()
    return {"email": user.get("email"), "first_name": user.get("first_name"), "name": row["name"]}


def campaign_published(conn, brand: Dict[str, Any], email: str) -> bool:
    """True when an admin already published this brand's campaign (status live, or a roster exists)."""
    cur = conn.cursor()
    cur.execute("""SELECT 1 FROM opportunities
                   WHERE (id = %s OR LOWER(brand_email) = LOWER(%s))
                     AND status = 'live'
                   LIMIT 1""", (brand.get("opportunity_id") or 0, email or ""))
    return cur.fetchone() is not None


def approve_for_opportunity(conn, opportunity_id: int, email: str) -> int:
    """Publishing a brand's campaign means the admin vetted the brand: approve its pending workspace
    account (linked by the campaign, or by the campaign's email). Returns how many rows were approved."""
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""UPDATE brands SET approval_status = 'approved', approved_at = NOW()
                   WHERE approval_status = 'pending'
                     AND (opportunity_id = %s
                          OR user_id IN (SELECT id FROM users WHERE LOWER(email) = LOWER(%s)))""",
                (opportunity_id, email or ""))
    n = cur.rowcount
    conn.commit()
    return n


# ----- emails -------------------------------------------------------------------------------

WORKSPACE_URL = "https://app.newcollab.co/brand/workspace"


def _send(to: str, subject: str, html: str, kind: str) -> None:
    if not to:
        return
    from services.resend_mail import send_resend_email
    try:
        send_resend_email(to, subject, html, tags=[{"name": "type", "value": kind}],
                          reply_to=(os.getenv("BRAND_SCAN_NOTIFY_EMAIL") or "team@newcollab.co").strip())
    except Exception as err:
        print(f"[brand-workspace] {kind} email skipped: {type(err).__name__}: {err}")


def _wrap(body: str) -> str:
    return ("<div style=\"max-width:560px;margin:0 auto;padding:24px;font-family:-apple-system,Segoe UI,Roboto,"
            "Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#2b2d33\">" + body + "</div>")


def _button(href: str, label: str) -> str:
    return (f"<a href=\"{href}\" style=\"display:inline-block;background:#e8395f;color:#fff;text-decoration:none;"
            f"font-weight:600;padding:12px 18px;border-radius:10px\">{label}</a>")


def notify_team_signup(email: str, brand_name: str, website: str, source: str) -> None:
    from html import escape
    to = (os.getenv("BRAND_SCAN_NOTIFY_EMAIL") or "team@newcollab.co").strip()
    _send(to, f"New brand account to approve: {brand_name}",
          _wrap(f"<p><b>{escape(brand_name)}</b> ({escape(email)}) created a brand workspace account.</p>"
                f"<p>Website: {escape(website or '-')}<br>Source: {escape(source or '-')}</p>"
                "<p>Approve it in Admin → Opportunities → Brand accounts.</p>"), "brand_account_signup")


def decision_email(info: Dict[str, Any], approve: bool) -> Dict[str, str]:
    """Approval / rejection email on the same branded template as the campaign and roster emails."""
    from html import escape

    from services.brand_campaign_emails import _hi, _link
    from services.roster_delivery import _p, _steps, _wrap as branded
    from services.roster_waiting_email import BRAND_FOOTER_NOTE, safe_brand_name

    brand = safe_brand_name(info.get("name"))
    hi = _p(_hi(info.get("first_name"), brand))
    if approve:
        subject = "Your Newcollab workspace is ready"
        body = hi + _p(f"<b>{escape(brand)}</b> is approved. Your workspace is open:") + _steps([
            "<b>Scan your competitors</b> to see the TikTok creators posting for them and the hooks they reuse.",
            "<b>Run your first gifted campaign free</b> and pick the creators you want.",
            "<b>Manage your roster</b> and collect ad-ready videos in one place.",
        ])
        html = branded(subject, "Scan competitors and run your first campaign free.", body,
                       _link("/brand/workspace", "account_approved"), "Open my workspace", BRAND_FOOTER_NOTE)
    else:
        subject = "Your Newcollab brand account"
        body = hi + _p(f"Thanks for signing up {escape(brand)}. We can't open a workspace for this brand right now. "
                       "Reply to this email and we'll take another look.")
        html = branded(subject, "About your Newcollab account.", body, "https://newcollab.co", "Visit Newcollab",
                       BRAND_FOOTER_NOTE)
    return {"subject": subject, "html": html}


def send_decision(info: Dict[str, Any], approve: bool) -> None:
    mail = decision_email(info, approve)
    _send(info.get("email"), mail["subject"], mail["html"], "brand_account_approved" if approve else "brand_account_rejected")
