"""Hermes brand-acquisition client for the Newcollab backend. Lives in the backend repo (hermes/brand-acquisition/)
and is symlinked into the Hermes profile by hermes/install.sh, so the VPS always runs the repo version.

Hermes never writes email copy. The backend renders every cold email (services/brand_pipeline.py), applies test
mode (BRAND_OUTREACH_LIVE) and tracks the sequence. These helpers only choose who to email and call the API.

Env (loaded from the profile .env, then /home/hermes/apps/creator_dashboard/.env):
  NEWCOLLAB_API_URL   default https://api.newcollab.co
  ADMIN_TOKEN         the backend admin token (NEWCOLLAB_ADMIN_TOKEN also accepted)
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests

PROFILE_ENV = "/home/hermes/.hermes/profiles/brand-acquisition/.env"
REPO_ENV = "/home/hermes/apps/creator_dashboard/.env"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (PROFILE_ENV, REPO_ENV):
        if os.path.exists(path):
            load_dotenv(path, override=False)


_load_env()
API = (os.getenv("NEWCOLLAB_API_URL") or "https://api.newcollab.co").rstrip("/")


def _headers() -> Dict[str, str]:
    token = os.getenv("ADMIN_TOKEN") or os.getenv("NEWCOLLAB_ADMIN_TOKEN") or ""
    if not token:
        raise RuntimeError("ADMIN_TOKEN is not set for the brand-acquisition profile")
    return {"X-Admin-Token": token, "Content-Type": "application/json"}


def _call(method: str, path: str, **kw) -> Dict[str, Any]:
    resp = requests.request(method, f"{API}{path}", headers=_headers(), timeout=kw.pop("timeout", 60), **kw)
    try:
        data = resp.json()
    except ValueError:
        data = {"error": resp.text[:300]}
    data["_status"] = resp.status_code
    return data


def test_connection() -> Dict[str, Any]:
    rep = _call("GET", "/api/admin/brand-pipeline/report", params={"hours": 24})
    return {"ok": rep["_status"] == 200, "api": API, "live": rep.get("live"), "test_email": rep.get("test_email"),
            "status": rep["_status"], "error": rep.get("error")}


def ready_brands(limit: int = 25) -> List[Dict[str, Any]]:
    """Prospects the pipeline will accept: never contacted, not closed, deduped by domain, with a contact email."""
    data = _call("GET", "/api/admin/brand-pipeline/queue/new", params={"limit": max(1, min(int(limit) * 2, 150))})
    rows = [b for b in data.get("brands") or [] if (b.get("contact_email") or "").strip()]
    return rows[:limit]


def preview_outreach_email(brand_id: int, kind: str = "initial") -> Dict[str, Any]:
    """The exact subject + text the brand would get. Sends nothing."""
    return _call("POST", "/api/admin/brand-pipeline/send", json={"brand_id": int(brand_id), "kind": kind, "dry_run": True})


render_outreach_email = preview_outreach_email


def send_outreach_email(brand_id: int, kind: str = "initial", test_to: str = "") -> Dict[str, Any]:
    """Send one pipeline email. test_to: send the real email for this brand to that inbox instead (no state change).
    While BRAND_OUTREACH_LIVE is off on the backend, every send is a test to the team inbox anyway."""
    body: Dict[str, Any] = {"brand_id": int(brand_id), "kind": kind}
    if test_to:
        body["test_to"] = test_to
    out = _call("POST", "/api/admin/brand-pipeline/send", json=body)
    out["success"] = bool(out.get("ok"))
    return out


send_ugc_outreach_email = send_outreach_email


def send_bulk_ugc_outreach(brand_ids: List[int], delay_seconds: float = 2.0, test_to: str = "") -> Dict[str, Any]:
    sent, skipped, failed = [], [], []
    for bid in brand_ids:
        out = send_outreach_email(bid, test_to=test_to)
        row = {"brand_id": bid, "to": out.get("to"), "subject": out.get("subject"), "test": out.get("test"),
               "error": out.get("error")}
        if out["success"]:
            sent.append(row)
            time.sleep(max(1.5, float(delay_seconds)))
        elif out["_status"] == 409:
            skipped.append(row)
        else:
            failed.append(row)
    return {"sent": sent, "skipped": skipped, "failed": failed}


def due_followups(limit: int = 100) -> List[Dict[str, Any]]:
    return _call("GET", "/api/admin/brand-pipeline/queue/followups", params={"limit": limit}).get("items") or []


def get_outreach_stats(hours: int = 24) -> Dict[str, Any]:
    return _call("GET", "/api/admin/brand-pipeline/report", params={"hours": hours})
