#!/usr/bin/env bash
# Install the NullMaps cron schedule into the current user's crontab (idempotent).
set -euo pipefail
SNIP="$(cd "$(dirname "$0")" && pwd)/crontab.snippet"
if systemctl list-timers 'nullmaps-*' 2>/dev/null | grep -q nullmaps; then
  echo "WARNING: nullmaps systemd timers are active — you may be double-scheduling. Remove them first." >&2
fi
tmp="$(mktemp)"
(crontab -l 2>/dev/null || true) | sed '/# nullmaps/,/# end nullmaps/d' > "$tmp"   # drop any prior block; || true handles no existing crontab
cat "$SNIP" >> "$tmp"
crontab "$tmp"
rm -f "$tmp"
echo "Installed nullmaps cron block:"; crontab -l | sed -n '/# nullmaps/,/# end nullmaps/p'
