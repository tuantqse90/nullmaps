#!/usr/bin/env bash
# Refresh NullMaps map data from a fresh OSM extract, then back up + restart.
# Resource-aware: single-instance lock, nice/ionice, runs off-peak from cron.
# Rebuilds the geocoder index + Valhalla graph by default; add REFRESH_TILES=1
# to also rebuild the PMTiles (Planetiler is the RAM-heavy step).
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/refresh.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOG=/var/log/nullmaps-refresh.log
exec 9>/tmp/nullmaps-refresh.lock; flock -n 9 || { echo "refresh already running"; exit 0; }
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"
N="nice -n 15 ionice -c3"
set -a; [ -f .env ] && . ./.env; set +a
URL="${OSM_EXTRACT_URL:-https://download.geofabrik.de/asia/vietnam-latest.osm.pbf}"

log "refresh START"
mkdir -p data/raw
$N curl -fSL -o data/raw/vietnam-latest.osm.pbf.new "$URL" && \
  mv data/raw/vietnam-latest.osm.pbf.new data/raw/vietnam-latest.osm.pbf
log "pbf updated"

# --- geocoder index (rebuild on container fs, then swap the file) ---
$N docker run --rm -v "$ROOT/data/raw:/raw:ro" -v "$ROOT/services/geocoder/data:/data" \
  nullmaps-geocoder sh -c "python importer.py /raw/vietnam-latest.osm.pbf /build/geocoder.db && mv /build/geocoder.db /data/geocoder.db" \
  && docker restart nullmaps-geocoder-1 >/dev/null && log "geocoder index rebuilt"

# --- Valhalla graph (force a rebuild from the new pbf) ---
ln -f data/raw/vietnam-latest.osm.pbf services/routing/custom_files/vietnam-latest.osm.pbf 2>/dev/null || true
rm -f services/routing/custom_files/valhalla_tiles.tar
rm -rf services/routing/custom_files/valhalla_tiles
$N docker compose -f docker-compose.yml up -d --force-recreate valhalla >/dev/null && log "valhalla rebuilding graph"

# --- tiles (optional, heavy) ---
if [ "${REFRESH_TILES:-0}" = "1" ]; then
  $N docker run --rm -v "$ROOT/data:/data" ghcr.io/onthegomap/planetiler:latest \
    --osm-path=/data/raw/vietnam-latest.osm.pbf --download --output=/data/vietnam.pmtiles --force \
    && docker restart nullmaps-martin-1 >/dev/null && log "tiles rebuilt"
fi

# --- back up the fresh artifacts ---
bash infra/backup.sh >>"$LOG" 2>&1 && log "backed up to R2"
log "refresh DONE"
