#!/usr/bin/env python3
"""Cold outreach run for Hermes: pick ready brands from the backend queue and send the pipeline's email.

  python run_meta_ads_outreach_send.py --limit 25              # scheduled run (test mode until live)
  python run_meta_ads_outreach_send.py --test-to me@x.com      # one brand's real email to that inbox
  python run_meta_ads_outreach_send.py --preview               # print one brand's email, send nothing

The copy comes from the backend. Never paste or edit email text here.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from brand_outreach_api import (preview_outreach_email, ready_brands, send_bulk_ugc_outreach,  # noqa: E402
                                test_connection)

MAX_SEND = 25


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=MAX_SEND)
    ap.add_argument("--test-to", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    conn = test_connection()
    print(f"API {conn['api']} ok={conn['ok']} live={conn['live']} test_inbox={conn['test_email']}")
    if not conn["ok"]:
        print(f"RESULT sent=0 failed=1 error={conn['error']}")
        return 1
    limit = 1 if (args.test_to or args.preview) else max(1, min(args.limit, MAX_SEND))
    brands = ready_brands(limit=limit)
    if not brands:
        print("RESULT sent=0 failed=0 (no ready brands)")
        return 0
    if args.preview:
        out = preview_outreach_email(brands[0]["brand_id"])
        print(f"PREVIEW {brands[0]['brand_name']} ({out.get('to')}) variant={out.get('variant')}\n"
              f"Subject: {out.get('subject')}\n\n{out.get('text')}")
        return 0
    res = send_bulk_ugc_outreach([b["brand_id"] for b in brands], delay_seconds=args.delay, test_to=args.test_to)
    print(f"RESULT sent={len(res['sent'])} skipped={len(res['skipped'])} failed={len(res['failed'])}")
    for r in res["sent"]:
        print(f"SENT -> {r['to']}{' (TEST)' if r['test'] else ''}: {r['subject']}")
    for r in res["skipped"] + res["failed"]:
        print(f"NOT SENT brand {r['brand_id']}: {r['error']}")
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
