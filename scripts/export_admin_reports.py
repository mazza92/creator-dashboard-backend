#!/usr/bin/env python3
"""
Export full Creator usage analytics & monetization insights from Admin Reports API.

Usage:
  python scripts/export_admin_reports.py
  API_BASE=https://api.newcollab.co python scripts/export_admin_reports.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_BASE = os.getenv("API_BASE", "http://localhost:5000").rstrip("/")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "pr-hunter-admin-2026")
DAYS = int(os.getenv("REPORT_DAYS", "90"))
TOP_LIMIT = int(os.getenv("REPORT_LIMIT", "500"))
BRAND_LIMIT = int(os.getenv("REPORT_BRAND_LIMIT", "100"))
OUTPUT_DIR = Path(
    os.getenv(
        "REPORT_OUTPUT_DIR",
        str(Path.home() / "Desktop" / "newcollab-admin-reports-export"),
    )
)

HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def fetch(path: str, params: dict | None = None) -> Any:
    url = f"{API_BASE}{path}"
    resp = requests.get(url, headers=HEADERS, params=params or {}, timeout=180)
    resp.raise_for_status()
    return resp.json()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in keys})


def flatten_overview(data: dict) -> list[dict]:
    rows = [{"metric": k, "value": v} for k, v in data.items() if not isinstance(v, (dict, list))]
    if data.get("subscription_breakdown"):
        for tier, count in data["subscription_breakdown"].items():
            rows.append({"metric": f"subscription_{tier}", "value": count})
    return rows


def collect_all() -> dict[str, Any]:
    day_params = {"days": DAYS}
    return {
        "meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "period_days": DAYS,
            "api_base": API_BASE,
        },
        "overview": fetch("/api/admin/reports/overview"),
        "today": fetch("/api/admin/reports/today"),
        "signups": fetch("/api/admin/reports/signups", day_params),
        "dau": fetch("/api/admin/reports/dau", day_params),
        "engagement": fetch("/api/admin/reports/engagement", day_params),
        "funnel": fetch("/api/admin/reports/funnel", day_params),
        "top_users": fetch(
            "/api/admin/reports/top-users",
            {**day_params, "limit": TOP_LIMIT},
        ),
        "quota_hits": fetch("/api/admin/reports/quota-hits"),
        "activity_heatmap": fetch("/api/admin/reports/activity-heatmap", day_params),
        "retention": fetch("/api/admin/reports/retention"),
        "popular_brands": fetch(
            "/api/admin/reports/popular-brands",
            {**day_params, "limit": BRAND_LIMIT},
        ),
        "brand_analytics": fetch(
            "/api/admin/reports/brand-analytics",
            {**day_params, "limit": BRAND_LIMIT},
        ),
        "pitch_analytics": fetch("/api/admin/reports/pitch-analytics", day_params),
    }


def build_csv_files(out_dir: Path, bundle: dict[str, Any]) -> list[Path]:
    files: list[Path] = []

    def add(name: str, rows: list[dict]) -> None:
        p = out_dir / f"{name}.csv"
        write_csv(p, rows)
        files.append(p)

    overview = bundle.get("overview") or {}
    add("01_overview_kpis", flatten_overview(overview))

    today = bundle.get("today") or {}
    add("02_today_snapshot", [{"metric": k, "value": v} for k, v in today.items() if not isinstance(v, (dict, list))])

    signups = bundle.get("signups") or {}
    add("03_daily_signups", signups.get("daily") or signups.get("signups") or [])

    dau = bundle.get("dau") or {}
    add("04_daily_active_users", dau.get("daily") or dau.get("dau") or [])

    engagement = bundle.get("engagement") or {}
    add("05_daily_engagement", engagement.get("daily_engagement") or [])
    activation = engagement.get("activation") or {}
    if activation:
        add("05_activation_summary", [activation])
    segments = engagement.get("user_segments") or {}
    if segments:
        add("05_user_segments", [{"segment": k, "count": v} for k, v in segments.items()])

    funnel = bundle.get("funnel") or {}
    steps = funnel.get("steps") or funnel.get("funnel") or []
    if steps:
        add("06_conversion_funnel", steps if isinstance(steps[0], dict) else [{"step": s} for s in steps])

    top_users = bundle.get("top_users") or {}
    add("07_top_creators_by_activity", top_users.get("users") or [])

    quota = bundle.get("quota_hits") or {}
    add("08_quota_hits_summary", [{"metric": k, "value": v} for k, v in quota.items() if not isinstance(v, list)])
    add("08_recent_quota_limit_hits", quota.get("recent_limit_hits") or [])

    heatmap = bundle.get("activity_heatmap") or {}
    add("09_activity_heatmap", heatmap.get("heatmap") or heatmap.get("data") or [])

    retention = bundle.get("retention") or {}
    add("10_retention_cohorts", retention.get("cohorts") or retention.get("weekly") or [])

    popular = bundle.get("popular_brands") or {}
    add("11_popular_brands", popular.get("brands") or [])

    brand = bundle.get("brand_analytics") or {}
    add("12_brand_unlocks_top", brand.get("top_unlocked_brands") or [])
    add("12_brand_unlocks_with_form", brand.get("top_brands_with_form") or [])
    add("12_brand_unlocks_email_only", brand.get("top_brands_email_only") or [])
    add("12_unlocks_by_category", brand.get("unlocks_by_category") or [])
    add("12_recent_unlocks", brand.get("recent_unlocks") or [])

    pitch = bundle.get("pitch_analytics") or {}
    add("13_pitch_daily_trend", pitch.get("daily") or [])
    add("13_pitch_top_users", pitch.get("top_users") or [])
    add("13_pitch_top_brands", pitch.get("top_brands") or [])
    add("13_pitch_recent", pitch.get("recent_pitches") or [])
    add("13_pitch_users_at_limit", pitch.get("users_at_limit") or [])

    return files


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUTPUT_DIR / f"creator_reports_{DAYS}d_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {DAYS}-day admin reports from {API_BASE} ...")
    try:
        bundle = collect_all()
    except requests.RequestException as exc:
        print(f"ERROR: API request failed: {exc}", file=sys.stderr)
        return 1

    json_path = out_dir / "full_report_bundle.json"
    write_json(json_path, bundle)
    print(f"Wrote {json_path}")

    csv_files = build_csv_files(out_dir, bundle)
    print(f"Wrote {len(csv_files)} CSV files")

    zip_path = out_dir.parent / f"{out_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=f"{out_dir.name}/full_report_bundle.json")
        for csv_file in csv_files:
            zf.write(csv_file, arcname=f"{out_dir.name}/{csv_file.name}")

    readme = out_dir / "README.txt"
    readme.write_text(
        f"NewCollab Admin Reports Export\n"
        f"Period: last {DAYS} days\n"
        f"Generated: {bundle['meta']['exported_at']}\n"
        f"Source: {API_BASE}\n\n"
        f"Files:\n"
        f"- full_report_bundle.json (complete API payload)\n"
        f"- *.csv (tabular slices for spreadsheets)\n"
        f"- ../{zip_path.name} (zip of all files)\n",
        encoding="utf-8",
    )

    print(f"\nExport complete:")
    print(f"  Folder: {out_dir}")
    print(f"  Zip:    {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
