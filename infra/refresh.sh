#!/usr/bin/env bash
# Refresh NullMaps map data from a fresh OSM extract, then back up + restart.
# Resource-aware: single-instance lock, nice/ionice, runs off-peak (systemd timer / cron).
# Rebuilds geocoder index + Valhalla graph by default; REFRESH_TILES=1 also rebuilds PMTiles.
# Health-gated with rollback: a failed index/graph rebuild restores the previous artifact.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/refresh.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
LOG=/var/log/nullmaps-refresh.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"
exec 9>/tmp/nullmaps-refresh.lock; flock -n 9 || { echo "refresh already running"; exit 0; }
N="nice -n 15 ionice -c3"
URL="${OSM_EXTRACT_URL:-https://download.geofabrik.de/asia/vietnam-latest.osm.pbf}"
ERRORS=0

log "refresh START"
mkdir -p data/raw

# --- fresh OSM extract (atomic swap) ---
if $N curl -fSL -o data/raw/vietnam-latest.osm.pbf.new "$URL"; then
  mv data/raw/vietnam-latest.osm.pbf.new data/raw/vietnam-latest.osm.pbf
  log "pbf updated"
else
  alert "pbf download failed — keeping previous extract, aborting refresh"
  exit 1
fi

# --- geocoder index (rebuild on container fs, swap the file, health-gate + rollback) ---
GEO_DB="services/geocoder/data/geocoder.db"
[ -f "$GEO_DB" ] && cp -f "$GEO_DB" "$GEO_DB.bak"
if $N docker run --rm -v "$ROOT/data/raw:/raw:ro" -v "$ROOT/services/geocoder/data:/data" \
     nullmaps-geocoder sh -c "mkdir -p /build && python importer.py /raw/vietnam-latest.osm.pbf /build/geocoder.db && mv /build/geocoder.db /data/geocoder.db"; then
  $COMPOSE restart geocoder >/dev/null
  if wait_healthy geocoder 90; then
    log "geocoder index rebuilt"
  else
    alert "geocoder unhealthy after rebuild — rolling back"
    [ -f "$GEO_DB.bak" ] && mv -f "$GEO_DB.bak" "$GEO_DB" && $COMPOSE restart geocoder >/dev/null
    ERRORS=$((ERRORS + 1))
  fi
else
  alert "geocoder index build failed — keeping previous index"
  ERRORS=$((ERRORS + 1))
fi

# --- Valhalla graph (force rebuild from the new pbf, health-gate + rollback) ---
ln -f data/raw/vietnam-latest.osm.pbf services/routing/custom_files/vietnam-latest.osm.pbf 2>/dev/null || true
TAR="services/routing/custom_files/valhalla_tiles.tar"
[ -f "$TAR" ] && mv -f "$TAR" "$TAR.bak"
rm -rf services/routing/custom_files/valhalla_tiles
if $N $COMPOSE up -d --force-recreate valhalla >/dev/null && wait_healthy valhalla 600; then
  log "valhalla graph rebuilt"
else
  alert "valhalla rebuild failed — rolling back to previous graph"
  if [ -f "$TAR.bak" ]; then
    mv -f "$TAR.bak" "$TAR"
    rm -rf services/routing/custom_files/valhalla_tiles
    $N $COMPOSE up -d --force-recreate valhalla >/dev/null
    wait_healthy valhalla 600 || alert "valhalla still unhealthy after rollback — manual attention needed"
  fi
  ERRORS=$((ERRORS + 1))
fi

# --- tiles (optional, heavy) ---
if [ "${REFRESH_TILES:-0}" = "1" ]; then
  if $N docker run --rm -v "$ROOT/data:/data" ghcr.io/onthegomap/planetiler:latest \
       --osm-path=/data/raw/vietnam-latest.osm.pbf --download --output=/data/vietnam.pmtiles --force; then
    if $COMPOSE restart martin >/dev/null && wait_healthy martin 60; then
      log "tiles rebuilt"
    else
      alert "martin unhealthy after tiles rebuild — manual attention needed"
      ERRORS=$((ERRORS + 1))
    fi
  else
    alert "tiles rebuild failed — keeping previous PMTiles"
    ERRORS=$((ERRORS + 1))
  fi
fi

# --- back up the fresh artifacts ---
if bash infra/backup.sh >>"$LOG" 2>&1; then
  log "backed up to R2"
else
  alert "backup failed after refresh"
  ERRORS=$((ERRORS + 1))
fi

log "refresh DONE (errors: $ERRORS)"
[ "$ERRORS" -eq 0 ] || exit 1
