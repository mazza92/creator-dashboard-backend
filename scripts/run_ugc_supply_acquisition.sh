#!/usr/bin/env bash
# UGC creator acquisition. Scripts only — no agent improvisation.
set -uo pipefail

ROOT="${CREATOR_DASHBOARD_DIR:-/home/hermes/apps/creator_dashboard}"
cd "$ROOT"

if [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

STAMP=$(date -u +%Y%m%d_%H%M)
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOCK="$LOG_DIR/ugc_supply_acq.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "===== SKIP $STAMP locked (another UGC run is active) ====="
  echo "RESULT inserted=0 sent=0 skip=locked"
  exit 0
fi
LOG="$LOG_DIR/ugc_supply_acq_$STAMP.log"
exec > >(tee -a "$LOG") 2>&1

echo "===== START $STAMP ====="
echo "cwd=$ROOT skip_crawl=${UGC_SKIP_CRAWL:-0} dry_run=${UGC_DRY_RUN:-0}"
echo "note: crawl takes 15-40 min through the TikTok proxy; crontab must call this script directly (Hermes terminal timeout 300s will kill it)"
if [[ -n "${TIKTOK_SHOP_PROXY:-}" || -n "${IG_PROXY:-}" ]]; then
  echo "tiktok_proxy=set"
else
  echo "tiktok_proxy=MISSING (VPS IP may be blocked by TikTok — set TIKTOK_SHOP_PROXY or IG_PROXY)"
fi
export PYTHONUNBUFFERED=1
export UGC_PLAYWRIGHT_MAX="${UGC_PLAYWRIGHT_MAX:-80}"

crawl_rc=0
if [[ "${UGC_SKIP_CRAWL:-0}" == "1" ]]; then
  echo "[1/2] crawl SKIPPED"
else
  echo "[1/2] crawl + draft insert"
  WORKERS="${UGC_WORKERS:-2}"
  if [[ -z "${TIKTOK_SHOP_PROXY:-}" && -z "${IG_PROXY:-}" ]]; then
    WORKERS="${UGC_WORKERS:-1}"
  fi
  # SIGTERM at 40m so IncrementalInserter can flush; SIGKILL 60s later if hung.
  timeout --kill-after=60 2400 python -u scripts/crawl_tiktok_ugc.py --daily --save-to-db \
    --quota "${UGC_QUOTA:-20}" --max-handles "${UGC_MAX_HANDLES:-500}" --no-serp --workers "$WORKERS" \
    -o "$LOG_DIR/tiktok_ugc_$STAMP.json"
  crawl_rc=$?
  echo "crawl_exit=$crawl_rc"
fi

echo "[2/2] outreach send (backend API, locked template)"
SEND_ARGS=(--limit "${UGC_MAX_SEND:-20}")
if [[ "${UGC_DRY_RUN:-0}" == "1" ]]; then
  SEND_ARGS+=(--dry-run)
fi
timeout 300 python scripts/run_ugc_supply_send.py "${SEND_ARGS[@]}"
send_rc=$?
echo "send_exit=$send_rc"
echo "===== DONE $STAMP crawl=$crawl_rc send=$send_rc ====="
sleep 1
python - "$LOG" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
inserted = sent = failed = 0
ms = re.findall(r"RESULT_CRAWL inserted=(\d+)", text)
if ms:
    inserted = int(ms[-1])
else:
    m = re.findall(r"Database insert: (\d+) inserted", text)
    if m:
        inserted = int(m[-1])
    else:
        m3 = re.findall(r"done: \d+ profiles, (\d+) qualified", text)
        if m3 and int(m3[-1]) > 0:
            inserted = 0
            print(f"NOTE: crawl reported qualified={m3[-1]} but no Database insert line")
    seeds = re.findall(r"(\d+) seed handles", text)
    if seeds and int(seeds[-1]) <= 2:
        print(f"NOTE: only {seeds[-1]} seed handles — discovery parser/API likely empty")
# Prefer the send script's integer line. Do not match sent=[{...}] list dumps.
ms = re.findall(r"(?m)^sent=(\d+)(?:\s+failed=(\d+))?", text)
if ms:
    sent = int(ms[-1][0])
    if ms[-1][1]:
        failed = int(ms[-1][1])
else:
    m2 = re.search(r'"sent_count":\s*(\d+)', text)
    if m2:
        sent = int(m2.group(1))
print(f"RESULT inserted={inserted} sent={sent} failed={failed} skip=none")
print("NOTE: crawl_exit=0 means the crawl finished, not that zero creators were found.")
PY
if [[ $send_rc -ne 0 ]]; then
  exit 1
fi
exit 0
