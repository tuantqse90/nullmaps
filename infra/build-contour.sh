#!/usr/bin/env bash
# Build contour-line vector tiles for Vietnam from the Copernicus GLO-90 DEM,
# served by Martin as /tiles/contours and drawn as contour-line/index/label
# layers in the styles. 100 m interval, index every 500 m.
#
# Pipeline: DEM -> gdal_contour (gpkg) -> GeoJSONSeq -> tippecanoe (mbtiles).
# tippecanoe (not ogr2ogr -f MVT, which is pathologically slow here). One-off,
# run on the VPS off-peak; output ~190 MB, backed up to R2.
set -euo pipefail
cd "$(dirname "$0")/../data"
GDAL="ghcr.io/osgeo/gdal:ubuntu-small-latest"
TIPPE="klokantech/tippecanoe:latest"

echo "[1/4] download VN Copernicus GLO-90 DEM (skip ocean 404s)"
mkdir -p dem && cd dem
for lat in $(seq 8 23); do for lon in $(seq 102 109); do
  n=$(printf "Copernicus_DSM_COG_30_N%02d_00_E%03d_00_DEM" "$lat" "$lon")
  [ -s "$n.tif" ] && continue
  curl -fsS "https://copernicus-dem-90m.s3.amazonaws.com/$n/$n.tif" -o "$n.tif" 2>/dev/null || rm -f "$n.tif"
done; done
cd ..

echo "[2/4] gdal_contour (100 m) + export GeoJSONSeq"
nice -n 15 ionice -c3 docker run --rm -v "$(pwd):/data" "$GDAL" bash -c '
  set -e; cd /data
  gdalbuildvrt -q vn_dem.vrt dem/*.tif
  gdal_contour -q -i 100 -a elev vn_dem.vrt contours.gpkg
  ogr2ogr -f GeoJSONSeq contours.geojsonl contours.gpkg
'

echo "[3/4] tippecanoe -> contours.mbtiles (z9-13)"
nice -n 15 ionice -c3 docker run --rm -v "$(pwd):/data" "$TIPPE" \
  tippecanoe -o /data/contours.mbtiles -Z9 -z13 -l contour \
    --drop-densest-as-needed --simplification=4 --force /data/contours.geojsonl

echo "[4/4] clean intermediates"
rm -rf dem vn_dem.vrt contours.gpkg contours.geojsonl
ls -lh contours.mbtiles
echo "Restart Martin to serve /tiles/contours, then infra/backup.sh"
