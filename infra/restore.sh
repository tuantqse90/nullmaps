#!/usr/bin/env bash
# Restore NullMaps built artifacts from R2 onto a fresh box, so you skip the
# ~15 min tiles/graph/index rebuild. Then `docker compose -f docker-compose.yml up -d`.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/restore.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
F="--s3-no-check-bucket"

mkdir -p data services/geocoder/data services/routing/custom_files/admin_data
echo "[restore] <- $SRC"
rclone copy $F "$SRC/vietnam.pmtiles"     data/
rclone copy $F "$SRC/hillshade.mbtiles"   data/ 2>/dev/null || true
rclone copy $F "$SRC/contours.mbtiles"    data/ 2>/dev/null || true
rclone copy $F "$SRC/terrain.mbtiles"     data/ 2>/dev/null || true
rclone copy $F "$SRC/geocoder.db"         services/geocoder/data/
rclone copy $F "$SRC/valhalla_tiles.tar"  services/routing/custom_files/
rclone copy $F "$SRC/valhalla.json"       services/routing/custom_files/
rclone copy $F "$SRC/fleet.db"            data/fleet/ 2>/dev/null || true   # telemetry + zones
rclone copy $F "$SRC/admin_data/"         services/routing/custom_files/admin_data/
echo "[restore] done — now run: docker compose -f docker-compose.yml up -d"
