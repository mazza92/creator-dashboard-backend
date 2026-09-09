#!/usr/bin/env bash
# Exact UGC supply crawler Mahery runs. Do not replace with a custom scraper.
set -euo pipefail
cd "$(dirname "$0")/.."
source venv/bin/activate
export PYTHONUNBUFFERED=1
QUOTA="${1:-15}"
STAMP="$(date -u +%Y%m%d_%H%M)"
mkdir -p logs
exec python -u scripts/crawl_tiktok_ugc.py --daily --save-to-db \
  --quota "$QUOTA" --max-handles 400 --serp-pages 5 --workers 4 \
  -o "logs/tiktok_ugc_${STAMP}.json"
