#!/usr/bin/env bash
# Verify the latest R2 backup is restorable: pull geocoder.db + vietnam.pmtiles to a temp
# dir, run sqlite integrity_check and a PMTiles magic-byte check. Non-destructive.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/restore-test.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export LOG=/var/log/nullmaps-backup.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"

SRC="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
F="--s3-no-check-bucket"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rc=0

log "restore-test START <- $SRC"
rclone copy $F "$SRC/geocoder.db"     "$TMP/" || { alert "restore-test: cannot pull geocoder.db"; exit 1; }
rclone copy $F "$SRC/vietnam.pmtiles" "$TMP/" || { alert "restore-test: cannot pull vietnam.pmtiles"; exit 1; }

# 1) sqlite integrity (use the host sqlite3 if present, else the geocoder image)
if command -v sqlite3 >/dev/null 2>&1; then
  res="$(sqlite3 "$TMP/geocoder.db" 'PRAGMA integrity_check;' 2>&1 | head -1)"
else
  res="$(docker run --rm -v "$TMP:/t" nullmaps-geocoder \
    python -c "import sqlite3; print(sqlite3.connect('/t/geocoder.db').execute('PRAGMA integrity_check').fetchone()[0])" 2>&1 | tail -1)"
fi
if [ "$res" = "ok" ]; then log "geocoder.db integrity: ok"; else alert "restore-test: geocoder.db integrity=$res"; rc=1; fi

# 2) PMTiles magic bytes (file starts with the 7-byte ASCII "PMTiles")
magic="$(head -c 7 "$TMP/vietnam.pmtiles" 2>/dev/null)"
if [ "$magic" = "PMTiles" ]; then log "vietnam.pmtiles magic: ok"; else alert "restore-test: pmtiles magic='$magic'"; rc=1; fi

if [ "$rc" -eq 0 ]; then log "restore-test PASS"; else log "restore-test FAIL"; fi
exit "$rc"
