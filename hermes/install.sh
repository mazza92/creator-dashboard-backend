#!/usr/bin/env bash
# One-time setup on the VPS (as the hermes user). Links Hermes's brand-acquisition skills to the repo copy and
# keeps the repo in sync every 15 minutes, so what's merged on GitHub is what Hermes runs. Safe to re-run.
set -euo pipefail
REPO="${REPO:-/home/hermes/apps/creator_dashboard}"
SKILLS="${SKILLS:-/home/hermes/.hermes/profiles/brand-acquisition/skills}"
SRC="$REPO/hermes/brand-acquisition"

git -C "$REPO" pull --ff-only
mkdir -p "$SKILLS"
BACKUP="$SKILLS/.backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
for f in "$SRC"/*; do
  name="$(basename "$f")"
  if [ -e "$SKILLS/$name" ] && [ ! -L "$SKILLS/$name" ]; then mv "$SKILLS/$name" "$BACKUP/"; fi
  ln -sfn "$f" "$SKILLS/$name"
done
# Any other old outreach copy in the profile: move it out of the way so nothing can send the old template.
grep -rlI "PR + UGC swap" "$SKILLS" --exclude-dir=".backup-*" 2>/dev/null | while read -r old; do
  [ -L "$old" ] || mv "$old" "$BACKUP/"
done
rmdir "$BACKUP" 2>/dev/null || echo "Old files moved to $BACKUP"

CRON="*/15 * * * * git -C $REPO pull --ff-only -q >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -vF "git -C $REPO pull" ; echo "$CRON" ) | crontab -
echo "Linked: $(ls -1 "$SRC" | tr '\n' ' ')"
python3 "$SKILLS/run_meta_ads_outreach_send.py" --preview || true
