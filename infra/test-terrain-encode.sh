#!/usr/bin/env bash
# Verify the Mapbox terrain-RGB encode recipe on a tiny synthetic DEM (no 465 MB download).
# Builds a 2x2 float GeoTIFF with known elevations, runs the SAME gdal_calc expressions as
# build-terrain.sh, decodes R/G/B and asserts the round-trip is within 1 m. Requires docker.
#
# Usage:  bash infra/test-terrain-encode.sh
set -euo pipefail
cd "$(dirname "$0")/.."
GDAL="ghcr.io/osgeo/gdal:ubuntu-small-latest"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

docker run --rm -i -v "$TMP:/t" "$GDAL" python3 - <<'PY'
from osgeo import gdal, osr
import numpy as np
elevs = np.array([[0.0, 100.0], [1000.0, 8000.0]], dtype=np.float32)
ds = gdal.GetDriverByName("GTiff").Create("/t/dem.tif", 2, 2, 1, gdal.GDT_Float32)
ds.SetGeoTransform((0, 1, 0, 2, 0, -1))
srs = osr.SpatialReference(); srs.ImportFromEPSG(3857); ds.SetProjection(srs.ExportToWkt())
ds.GetRasterBand(1).WriteArray(elevs)
ds = None
PY

docker run --rm -v "$TMP:/t" "$GDAL" bash -c '
  set -e; cd /t
  gdal_calc.py -A dem.tif --outfile=R.tif --calc="(numpy.floor((A+10000)/0.1/65536)).astype(numpy.uint8)" --type=Byte --quiet
  gdal_calc.py -A dem.tif --outfile=G.tif --calc="(numpy.floor((A+10000)/0.1/256)%256).astype(numpy.uint8)" --type=Byte --quiet
  gdal_calc.py -A dem.tif --outfile=B.tif --calc="(numpy.floor((A+10000)/0.1)%256).astype(numpy.uint8)" --type=Byte --quiet
  gdal_merge.py -separate -o rgb.tif R.tif G.tif B.tif
'

docker run --rm -i -v "$TMP:/t" "$GDAL" python3 - <<'PY'
from osgeo import gdal
import numpy as np
want = np.array([[0.0, 100.0], [1000.0, 8000.0]])
ds = gdal.Open("/t/rgb.tif")
R = ds.GetRasterBand(1).ReadAsArray().astype(float)
G = ds.GetRasterBand(2).ReadAsArray().astype(float)
B = ds.GetRasterBand(3).ReadAsArray().astype(float)
got = -10000 + (R * 65536 + G * 256 + B) * 0.1
err = float(np.abs(got - want).max())
print("want:", want.tolist())
print("got :", got.tolist())
print("max err (m):", err)
assert err <= 1.0, f"terrain-RGB round-trip error too large: {err}"
print("OK: terrain-RGB encode round-trips within 1 m")
PY
echo "terrain-encode fixture: PASS"
