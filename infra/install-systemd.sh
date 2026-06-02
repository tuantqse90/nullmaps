#!/usr/bin/env bash
# Install + enable the NullMaps systemd timers (run as root on the VPS).
set -euo pipefail
SRC="$(cd "$(dirname "$0")/systemd" && pwd)"
DST=/etc/systemd/system
if crontab -l 2>/dev/null | grep -q '# nullmaps'; then
  echo "WARNING: a nullmaps cron block exists (infra/install-cron.sh) — you may be double-scheduling. Remove it first." >&2
fi
for u in nullmaps-monitor nullmaps-refresh nullmaps-backup; do
  install -m 0644 "$SRC/$u.service" "$DST/$u.service"
  install -m 0644 "$SRC/$u.timer"   "$DST/$u.timer"
done
systemctl daemon-reload
for u in nullmaps-monitor nullmaps-refresh nullmaps-backup; do
  systemctl enable --now "$u.timer"
done
systemctl list-timers 'nullmaps-*' --no-pager || true
echo "Installed. Adjust OnCalendar in $DST/nullmaps-*.timer then: systemctl daemon-reload"
