#!/usr/bin/env bash
# Build a Mapbox terrain-RGB MBTiles for Vietnam from the public Copernicus GLO-90 DEM,
# served by Martin as /tiles/terrain and used by style-terrain.json (raster-dem + 3D terrain).
#
# GDAL-only (no rio-rgbify, matching build-hillshade.sh). CRITICAL: overviews use
# `gdaladdo -r nearest` — averaging RGB-encoded elevation corrupts it. Heavy one-off
# (DEM ~465 MB), run on the VPS off-peak; output backed up to R2 (infra/backup.sh).
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/build-terrain.sh
set -euo pipefail
cd "$(dirname "$0")/../data"
GDAL="ghcr.io/osgeo/gdal:ubuntu-small-latest"

echo "[1/4] download VN Copernicus GLO-90 DEM (skip ocean 404s)"
mkdir -p dem && cd dem
for lat in $(seq 8 23); do for lon in $(seq 102 109); do
  n=$(printf "Copernicus_DSM_COG_30_N%02d_00_E%03d_00_DEM" "$lat" "$lon")
  [ -s "$n.tif" ] && continue
  curl -fsS "https://copernicus-dem-90m.s3.amazonaws.com/$n/$n.tif" -o "$n.tif" 2>/dev/null || rm -f "$n.tif"
done; done
cd ..

echo "[2/4] merge + warp to EPSG:3857, encode Mapbox terrain-RGB"
nice -n 15 ionice -c3 docker run --rm -v "$(pwd):/data" "$GDAL" bash -c '
  set -e; cd /data
  gdalbuildvrt -q vn_dem.vrt dem/*.tif
  gdalwarp -q -t_srs EPSG:3857 -r bilinear -dstnodata 0 -co COMPRESS=DEFLATE -co BIGTIFF=YES -overwrite vn_dem.vrt vn_dem_3857.tif
  gdal_calc.py -A vn_dem_3857.tif --outfile=R.tif --calc="(numpy.floor((A+10000)/0.1/65536)).astype(numpy.uint8)" --type=Byte --quiet
  gdal_calc.py -A vn_dem_3857.tif --outfile=G.tif --calc="(numpy.floor((A+10000)/0.1/256)%256).astype(numpy.uint8)" --type=Byte --quiet
  gdal_calc.py -A vn_dem_3857.tif --outfile=B.tif --calc="(numpy.floor((A+10000)/0.1)%256).astype(numpy.uint8)" --type=Byte --quiet
  gdal_merge.py -separate -co COMPRESS=DEFLATE -o terrain_rgb.tif R.tif G.tif B.tif
'

echo "[3/4] -> terrain.mbtiles (PNG) + nearest overviews"
nice -n 15 ionice -c3 docker run --rm -v "$(pwd):/data" "$GDAL" bash -c '
  set -e; cd /data
  rm -f terrain.mbtiles
  gdal_translate -q -of MBTILES -co TILE_FORMAT=PNG terrain_rgb.tif terrain.mbtiles
  gdaladdo -r nearest terrain.mbtiles 2 4 8 16 32 64
'

echo "[4/4] clean intermediates"
rm -rf dem vn_dem.vrt vn_dem_3857.tif R.tif G.tif B.tif terrain_rgb.tif
ls -lh terrain.mbtiles
echo "Restart Martin to serve /tiles/terrain, then infra/backup.sh"
