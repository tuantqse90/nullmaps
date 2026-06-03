# 3D Terrain — Design Spec

**Date:** 2026-06-03
**Sub-project:** ④b of the NullMaps upgrade program — the FINAL track (①✅ ②✅ ③a✅ ③b✅ ④a✅ → **④b 3D terrain**).
**Branch:** `feat/3d-terrain`
**Status:** Approved design → ready for implementation plan

## Context

Reuse the Copernicus GLO-90 DEM (already downloaded/processed for hillshade + contours) to ship real 3D
terrain as an **opt-in** style variant, keeping the default 2D maps fast for dispatch.

**Confirmed from code (read, not assumed):**

- `infra/build-hillshade.sh` / `infra/build-contour.sh` are the build pattern: download VN DEM tiles
  (lat 8-23, lon 102-109) from `copernicus-dem-90m.s3.amazonaws.com` → `gdalbuildvrt` →
  process in the `ghcr.io/osgeo/gdal:ubuntu-small-latest` container → MBTiles → clean → backup to R2.
  build-hillshade explicitly **avoids rio-rgbify** ("its transform_bounds path errors with modern rasterio").
- `docker-compose.yml:15` martin `command` ends `… "/data/hillshade.mbtiles", "/data/contours.mbtiles"]`.
- `services/tiles/style/style.json` sources (lines 8-56): `vn` (vector), `sovereignty`, `hillshade`
  (raster), `contours` (vector). No `raster-dem` source, no `terrain`/`sky` yet.
- `infra/backup.sh` ARTIFACTS (lines 20-21) include `data/hillshade.mbtiles` + `data/contours.mbtiles`;
  `infra/restore.sh:15-16` pull them.
- `infra/gateway/Caddyfile:62` whitelists `@style path /style.json /style-dark.json`.
- `services/tiles/check-icons.mjs:9` `STYLES = ["style/style.json", "style/style-dark.json"]`; `make
  style-lint` (Makefile) + the CI `style` job validate those two (added in ④a).
- `client/nullmaps.js` `map()`/`staticImage()` switch style by `theme` (`"dark"` → `style-dark.json`).

**Key technical finding (de-risked):** terrain-RGB overviews must use **`gdaladdo -r nearest`**, not
`-r average` — averaging RGB-encoded elevation produces invalid elevations at low zoom. With nearest
resampling, a GDAL-only encode (`gdal_calc.py` → Mapbox terrain-RGB) is correct and avoids rio-rgbify,
staying consistent with the existing hillshade/contour scripts.

**Design decisions (locked):**

- Encoding: **GDAL-only, Mapbox terrain-RGB, `gdaladdo -r nearest`** (no rio-rgbify).
- Gating: a **separate `style-terrain.json` variant** (opt-in); default styles stay flat/fast.
- Verification: the GDAL encode recipe is verified locally on a **tiny synthetic DEM** (no 465 MB
  download); the full VN terrain build is **box-only**, documented.

**Explicitly out of scope:** predictive traffic, live elevation queries, terrain in the default style,
terrarium encoding, hillshade-via-terrain rework, mobile/native.

## Goals / Success Criteria

1. `infra/build-terrain.sh` produces a valid Mapbox terrain-RGB `data/terrain.mbtiles` from the VN DEM,
   with nearest-resampled overviews (verified on a synthetic fixture: a known elevation round-trips
   through the encode within tolerance).
2. Martin serves it at `/tiles/terrain`; `terrain.mbtiles` is backed up + restored like the other DEM
   artifacts.
3. `style-terrain.json` adds a `raster-dem` source + `terrain` (exaggeration) + a `sky` layer, validates
   against the MapLibre spec, and is reachable through the gateway at `/style-terrain.json`.
4. `nm.map(maplibregl, el, {theme: "terrain"})` loads the terrain style; the demo offers it.
5. `make style-lint` / CI validate all three styles and the icon coverage still passes.
6. The default `style.json` / `style-dark.json` are unchanged (2D, fast).

## Design

### 1 — `infra/build-terrain.sh` (new, GDAL-only)

Mirror `build-hillshade.sh`. After downloading the DEM and `gdalbuildvrt` + `gdalwarp` to EPSG:3857 float,
encode Mapbox terrain-RGB. Mapbox decode is `height = -10000 + (R*65536 + G*256 + B) * 0.1`, so encode
`v = round((height + 10000) / 0.1)` then `R = (v >> 16) & 255`, `G = (v >> 8) & 255`, `B = v & 255`. With
`gdal_calc.py` (numpy) on the warped single-band DEM `A`:

```
gdal_calc.py -A vn_dem_3857.tif --outfile=R.tif --calc="(numpy.floor((A+10000)/0.1/65536)).astype(numpy.uint8)" --type=Byte --quiet
gdal_calc.py -A vn_dem_3857.tif --outfile=G.tif --calc="(numpy.floor((A+10000)/0.1/256)%256).astype(numpy.uint8)" --type=Byte --quiet
gdal_calc.py -A vn_dem_3857.tif --outfile=B.tif --calc="(numpy.floor((A+10000)/0.1)%256).astype(numpy.uint8)" --type=Byte --quiet
gdal_merge.py -separate -o terrain_rgb.tif -co COMPRESS=DEFLATE R.tif G.tif B.tif
gdal_translate -of MBTILES -co TILE_FORMAT=PNG terrain_rgb.tif terrain.mbtiles
gdaladdo -r nearest terrain.mbtiles 2 4 8 16 32 64
```

(Ocean/nodata: the warped DEM nodata is filled to 0 m before encoding so the sea encodes to a valid
low value rather than transparent — `gdalwarp -dstnodata 0` / `gdal_calc` treats nodata as 0.) Clean
intermediates, `ls -lh terrain.mbtiles`, and print the "restart martin + backup" hint. One-off, box-only,
`nice/ionice` like the siblings.

### 2 — Martin serving + backup/restore wiring

- `docker-compose.yml:15` martin `command`: append `"/data/terrain.mbtiles"` after
  `"/data/contours.mbtiles"`. Martin serves it at `/tiles/terrain`.
- `infra/backup.sh` ARTIFACTS: add `"data/terrain.mbtiles"` after `data/contours.mbtiles`.
- `infra/restore.sh`: add `rclone copy $F "$SRC/terrain.mbtiles" data/ 2>/dev/null || true` after contours.
- Not wired into `refresh.sh` (the DEM is a rarely-changing one-off, like hillshade/contours).

### 3 — `style-terrain.json` variant + gateway + lint

- **New `services/tiles/style/style-terrain.json`** — a copy of `style.json` with:
  - source `"terrain": {"type": "raster-dem", "url": "/tiles/terrain", "tileSize": 256, "encoding": "mapbox"}`,
  - top-level `"terrain": {"source": "terrain", "exaggeration": 1.3}`,
  - a `sky` layer `{"id": "sky", "type": "sky", "paint": {"sky-color": "#a0c4e8", "horizon-color": "#dfeaf2", "fog-color": "#ffffff", "sky-horizon-blend": 0.5, "horizon-fog-blend": 0.5}}`.
  - The `hillshade` raster layer stays (reads nicely under terrain). Everything else identical to `style.json`.
- **`infra/gateway/Caddyfile:62`**: `@style path /style.json /style-dark.json /style-terrain.json`.
- **Lint**: add `"style/style-terrain.json"` to `check-icons.mjs` `STYLES`; add a third
  `gl-style-validate … style-terrain.json` line to the `Makefile` `style-lint` target and the CI `style`
  job. (Same icon set → coverage still passes; the validator confirms the terrain/sky/source additions.)

### 4 — SDK + demo opt-in

- `client/nullmaps.js`: in `map()` and `staticImage()`, extend the theme→style mapping so
  `theme === "terrain"` → `style-terrain.json` (keep `dark`/`light`). For `map()`, also set a non-zero
  default `pitch` (e.g. 60) and `maxPitch: 85` when terrain so the 3D is visible; document that callers
  can override.
- `services/tiles/style/index.html`: add a "3D terrain" entry to the existing theme toggle (light → dark →
  terrain) using `map.setStyle("/style-terrain.json")` and a pitch.

### 5 — Docs

- `services/tiles/README.md`: a terrain section (build-terrain.sh, the nearest-overview note, the
  `/style-terrain.json` opt-in, exaggeration tuning).
- `docs/runbook-ops.md`: a one-line "build 3D terrain" entry alongside the hillshade/contour build.

### Testing / Verification

- **Encode fixture (local, no DEM download):** in the `osgeo/gdal` container, create a tiny single-band
  float GeoTIFF with known elevations (e.g. 0, 100, 1000, 8000 m), run the `gdal_calc` encode + a
  `gdal_translate` to PNG, read back R/G/B and assert `-10000 + (R*65536+G*256+B)*0.1` ≈ each elevation
  within ±1 m. This proves the recipe without the 465 MB VN download. A script
  `infra/test-terrain-encode.sh` runs it; `bash -n` + `shellcheck` cover both terrain scripts.
- **Style:** `make style-lint` validates `style-terrain.json` (gl-style-validate) + icon coverage.
- **Compose:** `docker compose -f docker-compose.yml config -q` stays valid with the extra martin arg.
- **Full VN build:** box-only, documented (cannot run in CI / not run locally).

## Risks & Mitigations

- **Overview corruption** — addressed by `gdaladdo -r nearest`; the fixture test asserts a round-trip so a
  bad encode is caught locally.
- **gdal_calc numpy expression quirks** — the fixture test exercises the exact expressions; if a `%`/shift
  form misbehaves, it fails the round-trip before any box build.
- **Terrain forced on dispatch clients** — avoided by the separate `style-terrain.json` variant; default
  styles untouched.
- **terrain.mbtiles size/bandwidth** — it is opt-in and DEM-derived (tens–low-hundreds of MB), backed up
  like hillshade; documented in box-sizing.

## Definition of Done

- `infra/build-terrain.sh` + `infra/test-terrain-encode.sh` exist, `bash -n` + `shellcheck` clean; the
  encode fixture round-trips known elevations locally.
- Martin command, backup.sh, restore.sh include `terrain.mbtiles`; `docker compose config -q` passes.
- `style-terrain.json` exists, is gateway-whitelisted, and `make style-lint` (3 styles) passes.
- SDK `theme: "terrain"` + demo load the terrain style; default styles unchanged.
- tiles README + ops runbook document the terrain build.
