"""One gate every creator pitch passes before it leaves their Gmail: Autopilot pitches, Autopilot follow-ups and
"Send for me" (single and batch).

Why (Nov 2026): a Pro creator wrote "a lot of the emails that were sent are coming back as the addresses blocked or
no longer exist". 48% of Autopilot sends in two weeks had never been verified: pitches sent before verification
existed (Nov 3) kept getting follow-ups in the same thread, follow-ups never re-checked the inbox (JOVS was marked
invalid and still got one), and the check failed open whenever Hunter couldn't answer. Autopilot only has the
gmail.send scope, so we can't read bounces from the creator's inbox: the only reliable fix is to stop bad inboxes
before sending.

Rules:
1. the address must look like an email and its domain must receive mail (MX, or A as the SMTP fallback), checked
   for free over DNS-over-HTTPS; a dead domain is blocked and flagged for every creator;
2. an inbox already known bad (bounced, invalid, swapped out) is replaced by a verified one or blocked;
3. otherwise contact_repair.ensure_deliverable verifies it (Hunter, cached 90 days) and repairs it when invalid;
4. "valid" and "catch-all" go out. "unknown" or "couldn't verify" never go out on Autopilot: the send is deferred and
   retried (up to MAX_DEFERS runs), then skipped. A creator sending by hand still can, when the domain is live.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, Optional

import requests

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
DOH_URLS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
DNS_TTL = 6 * 3600
MAX_DEFERS = 3
OK_STATUSES = ("valid", "catch-all")

# Swappable in tests so nothing reaches the network.
HTTP: Callable = requests.get
_dns_cache: Dict[str, Any] = {}


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in (email or "") else ""


def _doh(name: str, rtype: str, http: Callable) -> Optional[bool]:
    """True when the record exists, False when the domain answers without it, None when DNS couldn't be asked."""
    for url in DOH_URLS:
        try:
            resp = http(url, params={"name": name, "type": rtype}, headers={"accept": "application/dns-json"}, timeout=4)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("Status") == 3:  # NXDOMAIN
                return False
            want = 15 if rtype == "MX" else 1
            return any(int(a.get("type") or 0) == want for a in (data.get("Answer") or []))
        except (requests.RequestException, ValueError):
            continue
    return None


def domain_receives_mail(domain: str, http: Optional[Callable] = None) -> Optional[bool]:
    """MX present (or an A record, which SMTP falls back to). None when DNS is unreachable: never block on that."""
    if not domain:
        return False
    hit = _dns_cache.get(domain)
    if hit and hit[1] > time.time():
        return hit[0]
    http = http or HTTP
    mx = _doh(domain, "MX", http)
    if mx is None:
        return None
    ok = mx or bool(_doh(domain, "A", http))
    _dns_cache[domain] = (ok, time.time() + DNS_TTL)
    return ok


def _flag_dead_domain(conn, brand_id: Optional[int], email: str, creator_id: Optional[int]) -> None:
    if not brand_id:
        return
    try:
        from services.contact_repair import _log
        cur = conn.cursor()
        cur.execute("""UPDATE pr_brands SET email_status = 'invalid', email_verification_source = 'dns',
                              email_verified_at = NOW()
                       WHERE id = %s AND LOWER(COALESCE(contact_email, '')) = LOWER(%s)""", (int(brand_id), email))
        _log(conn, int(brand_id), email, None, "dns_dead_domain", creator_id)
        conn.commit()
    except Exception as err:
        print(f"[deliverability] dead-domain flag skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass


def gate(conn, brand_id: Optional[int], email: Optional[str], *, creator_id: Optional[int] = None,
         manual: bool = False, http: Optional[Callable] = None, verify_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Decide one send. Returns {"send": bool, "email": str|None, "status": str, "changed": bool, "defer": bool,
    "reason": str}. ``defer`` means "ask again on the next run" (Autopilot only)."""
    email = (email or "").strip().lower()
    out = {"send": False, "email": email or None, "status": "invalid", "changed": False, "defer": False,
           "reason": ""}
    if not EMAIL_RE.match(email):
        return {**out, "reason": "bad_format"}
    live = domain_receives_mail(_domain(email), http=http)
    if live is False:
        _flag_dead_domain(conn, brand_id, email, creator_id)
        # The brand may have a working inbox on another domain: let repair look for it.
    if verify_fn is None:
        from services.contact_repair import ensure_deliverable as verify_fn
    check = verify_fn(conn, brand_id, email, creator_id=creator_id) if brand_id else {
        "email": email, "status": "unchecked", "changed": False}
    final = (check.get("email") or "").strip().lower() or None
    status = check.get("status") or "unchecked"
    changed = bool(check.get("changed")) and bool(final)
    if not final:
        return {**out, "email": None, "reason": "no_deliverable_contact"}
    if changed and final != email:
        live = domain_receives_mail(_domain(final), http=http)
    if live is False:
        return {**out, "email": final, "reason": "domain_receives_no_mail"}
    if status in OK_STATUSES:
        return {"send": True, "email": final, "status": status, "changed": changed, "defer": False, "reason": "ok"}
    if status in ("invalid", "bounced"):
        return {**out, "email": final, "reason": "invalid"}
    # unknown / unchecked: Autopilot waits for a real answer, a creator sending by hand may go ahead on a live domain.
    if manual and live:
        return {"send": True, "email": final, "status": status, "changed": changed, "defer": False,
                "reason": "unverified_manual"}
    return {**out, "email": final, "status": status, "defer": True, "reason": "not_verified_yet"}


def defer_count(error: Optional[str]) -> int:
    m = re.match(r"^verify_pending:(\d+)$", error or "")
    return int(m.group(1)) if m else 0
