#!/usr/bin/env bash
# Back up NullMaps built artifacts (tiles + geocoder index + Valhalla graph) to R2.
# These take ~15 min to rebuild, so a backup makes box rebuilds fast.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/backup.sh
# Pairs with infra/restore.sh. R2 remote `r2:` is configured in rclone on the box;
# bucket-scoped token => pass --s3-no-check-bucket (can't ListBuckets).
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
F="--s3-no-check-bucket"

echo "[backup] -> $DEST  ($(date -u +%FT%TZ))"
rclone copy $F data/vietnam.pmtiles                          "$DEST/"
[ -s data/hillshade.mbtiles ] && rclone copy $F data/hillshade.mbtiles "$DEST/"
rclone copy $F services/geocoder/data/geocoder.db            "$DEST/"
rclone copy $F services/routing/custom_files/valhalla_tiles.tar "$DEST/"
rclone copy $F services/routing/custom_files/valhalla.json   "$DEST/"
rclone sync $F services/routing/custom_files/admin_data      "$DEST/admin_data/"
echo "[backup] done ($(date -u +%FT%TZ))"
