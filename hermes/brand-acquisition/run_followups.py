#!/usr/bin/env python3
"""Send every follow-up / nudge the backend pipeline says is due (day 3, day 7, last note). Copy comes from the
backend; test mode applies until BRAND_OUTREACH_LIVE=1."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from brand_outreach_api import due_followups, send_outreach_email, test_connection  # noqa: E402


def main() -> int:
    if not test_connection()["ok"]:
        print("RESULT sent=0 failed=1 (API unreachable)")
        return 1
    sent = failed = 0
    for item in due_followups():
        out = send_outreach_email(item["brand_id"], kind=item["kind"])
        if out["success"]:
            sent += 1
            print(f"SENT {item['kind']} -> {out.get('to')}: {out.get('subject')}")
            time.sleep(2)
        else:
            failed += out["_status"] != 409
            print(f"NOT SENT brand {item['brand_id']}: {out.get('error')}")
    print(f"RESULT sent={sent} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
