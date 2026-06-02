#!/usr/bin/env bash
# Build a shaded-relief (hillshade) raster MBTiles for Vietnam from the public
# Copernicus GLO-90 DEM, served by Martin as /tiles/hillshade and rendered as a
# low-opacity raster layer in the styles.
#
# Heavy-ish one-off (DEM ~465 MB, GDAL warp): run on the VPS off-peak. Output is
# ~150 MB and backed up to R2 (infra/backup.sh). Pure GDAL — no rio-rgbify
# (its transform_bounds path errors with modern rasterio).
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/build-hillshade.sh
set -euo pipefail
cd "$(dirname "$0")/../data"
GDAL="ghcr.io/osgeo/gdal:ubuntu-small-latest"

echo "[1/4] download VN Copernicus GLO-90 DEM tiles (skip ocean 404s)"
mkdir -p dem && cd dem
for lat in $(seq 8 23); do for lon in $(seq 102 109); do
  n=$(printf "Copernicus_DSM_COG_30_N%02d_00_E%03d_00_DEM" "$lat" "$lon")
  [ -s "$n.tif" ] && continue
  curl -fsS "https://copernicus-dem-90m.s3.amazonaws.com/$n/$n.tif" -o "$n.tif" 2>/dev/null || rm -f "$n.tif"
done; done
cd ..

echo "[2/4] merge + warp to EPSG:3857, hillshade (multidirectional)"
nice -n 15 ionice -c3 docker run --rm -v "$(pwd):/data" "$GDAL" bash -c '
  set -e; cd /data
  gdalbuildvrt -q vn_dem.vrt dem/*.tif
  gdalwarp -q -t_srs EPSG:3857 -r bilinear -co COMPRESS=DEFLATE -co BIGTIFF=YES -overwrite vn_dem.vrt vn_dem_3857.tif
  gdaldem hillshade -multidirectional -compute_edges -co COMPRESS=DEFLATE vn_dem_3857.tif hillshade.tif
  rm -f hillshade.mbtiles
  gdal_translate -of MBTILES -co TILE_FORMAT=PNG hillshade.tif hillshade.mbtiles
  gdaladdo -r average hillshade.mbtiles 2 4 8 16 32 64 128
'

echo "[3/4] clean intermediates"
rm -rf dem vn_dem.vrt vn_dem_3857.tif hillshade.tif

echo "[4/4] done -> data/hillshade.mbtiles ; restart Martin to serve /tiles/hillshade"
ls -lh hillshade.mbtiles
echo "Run: docker compose -f docker-compose.yml up -d martin   (and infra/backup.sh)"
