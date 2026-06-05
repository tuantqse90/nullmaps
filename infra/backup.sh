#!/usr/bin/env bash
# Back up NullMaps built artifacts (tiles + geocoder index + Valhalla graph) to R2.
# Verifies each upload, keeps a flat "latest" set + 4 weekly snapshots, alerts on failure.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/backup.sh
# R2 remote `r2:` configured in rclone; bucket-scoped token => --s3-no-check-bucket.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export LOG=/var/log/nullmaps-backup.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"

DEST="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
WEEKLY="$DEST/weekly/$(date -u +%G-W%V)"
F="--s3-no-check-bucket"

ARTIFACTS=(
  "data/vietnam.pmtiles"
  "data/hillshade.mbtiles"
  "data/contours.mbtiles"
  "data/terrain.mbtiles"
  "services/geocoder/data/geocoder.db"
  "services/routing/custom_files/valhalla_tiles.tar"
  "services/routing/custom_files/valhalla.json"
  "data/fleet/fleet.db"   # NOT regenerable: live GPS telemetry + operator geofence zones
)

# put <localfile> <remote_dir> — copy then checksum-verify; alert+fail on mismatch.
put() {
  local src="$1" dstdir="$2" base
  base="$(basename "$src")"
  [ -s "$src" ] || { log "skip (missing/empty): $src"; return 0; }
  rclone copy $F "$src" "$dstdir/" || { alert "backup copy failed: $src"; return 1; }
  rclone check $F --one-way --include "$base" "$(dirname "$src")" "$dstdir" >/dev/null 2>&1 \
    || { alert "backup verify failed: $src"; return 1; }
  return 0
}

log "backup START -> $DEST"
rc=0
# Flush the fleet WAL into the main .db so the copied file is current (best-effort).
if [ -f data/fleet/fleet.db ]; then
  docker exec nullmaps-adapter-1 python3 -c \
    "import sqlite3; sqlite3.connect('/fleet/fleet.db').execute('PRAGMA wal_checkpoint(TRUNCATE)')" \
    >/dev/null 2>&1 || log "fleet WAL checkpoint skipped"
fi
for a in "${ARTIFACTS[@]}"; do
  put "$a" "$DEST"   || rc=1   # flat "latest" (restore.sh reads this)
  put "$a" "$WEEKLY" || rc=1   # retained weekly snapshot
done
rclone sync $F services/routing/custom_files/admin_data "$DEST/admin_data/" \
  || { alert "backup admin_data sync failed"; rc=1; }

# retention: keep only the 4 most recent weekly snapshots
mapfile -t weeks < <(rclone lsf $F "$DEST/weekly/" 2>/dev/null | sed 's#/##' | sort)
if [ "${#weeks[@]}" -gt 4 ]; then
  for old in "${weeks[@]:0:${#weeks[@]}-4}"; do
    rclone purge $F "$DEST/weekly/$old" >/dev/null 2>&1 && log "pruned old weekly: $old"
  done
fi

if [ "$rc" -eq 0 ]; then log "backup DONE"; else alert "backup completed with errors"; fi
exit "$rc"
