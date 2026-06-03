# services/tiles — Map tiles + web SDK (Phase 1)

**What:** Vector tiles of Vietnam, served to a MapLibre map I embed in my apps.

**Why:** The basemap layer of NullMaps — the first end-to-end visible win, and the foundation every
other capability draws on the map for.

## Pipeline

```
Geofabrik vietnam-latest.osm.pbf  →  Planetiler  →  vietnam.pmtiles  →  Martin (HTTP)  →  MapLibre
```

- **Planetiler** builds OpenMapTiles-schema vector tiles from the VN extract in minutes — much faster
  than tilemaker at this scope.
- **PMTiles** is a single-file tile archive (cloud-native, range-request friendly).
- **Martin** (Rust) serves it over HTTP. Cheapest alternative: put the `.pmtiles` behind Caddy/CDN and
  read it directly in-browser via the `pmtiles://` protocol (see commented source in `style/index.html`).

## Run

```bash
cp .env.example .env          # set API_KEY etc. (from repo root)
make tiles                    # download VN extract + build data/vietnam.pmtiles
make demo                     # start Martin + demo, prints the URL
```

- Demo: <http://localhost:8080> — Ho Chi Minh City basemap.
- Tiles via the demo origin (no CORS): TileJSON <http://localhost:8080/tiles/vietnam>.
- Martin direct (debug): catalog <http://localhost:3000/tiles/catalog>.

## Files

- `style/style.json` — full MapLibre style (OMT layers: water, landcover/landuse, parks, buildings,
  roads with casing, labels, admin boundaries) + a **sovereignty** GeoJSON layer.
- `style/index.html` — demo page (HCMC); honors `prefers-color-scheme` and has a light/dark toggle.
- `style/style-dark.json` — dark MapLibre style, served at `/style-dark.json` (first-class endpoint).
- `Caddyfile` — serves the demo + reverse-proxies Martin under `/tiles`.
- `fonts/` — self-hosted glyph fonts (`make fonts` downloads Noto Sans); Martin serves them at
  `/tiles/font/{fontstack}/{range}`. Gitignored.

## Self-hosted glyphs

Glyphs are served by **Martin** (`--font /fonts`), not a public CDN — keeps the Privacy pillar intact.
The style's `glyphs` points at `/tiles/font/{fontstack}/{range}`; font stacks are `Noto Sans Regular`
and `Noto Sans Bold`. Run `make fonts` before `make up`/`make demo` (they depend on it).

## Sovereignty

The style **always labels Hoàng Sa (Paracel) and Trường Sa (Spratly) as "(Việt Nam)"** via a dedicated
GeoJSON layer — correct for any user-facing app (the brief's sovereignty note).

## Notes

- Style editing: load `style/style.json` into **Maputnik** to author visually.
- The demo still registers the PMTiles protocol so a `pmtiles://` source also works if you serve the
  raw `.pmtiles` file directly instead of via Martin.

## POI declutter + style lint (④a)

- The `poi-icons` layer is rank/zoom-gated: z14 shows only the most prominent POIs (`rank ≤ 3`),
  relaxing to `rank ≤ 6` at z15 and all at z16+, with `symbol-sort-key` so prominent POIs win
  collisions. Tune the `["step", ["zoom"], 3, 15, 6, 16, 99]` thresholds to taste.
- `make style-lint` validates `style.json` + `style-dark.json` against the MapLibre style spec and
  checks every `icon-image` name has a `sprites/<name>.svg` (`services/tiles/check-icons.mjs`). CI runs it.
- Verify the visual effect with `make demo`: fewer POI pins in dense HCMC at z14, important POIs retained.

## 3D terrain (④b)

- `infra/build-terrain.sh` encodes the Copernicus GLO-90 DEM as **Mapbox terrain-RGB** `data/terrain.mbtiles`
  (GDAL-only, no rio-rgbify). **Overviews MUST use `gdaladdo -r nearest`** — averaging RGB-encoded
  elevation corrupts it. `infra/test-terrain-encode.sh` round-trips the encode on a synthetic DEM (no
  465 MB download); the full VN build is box-only and off-peak.
- Martin serves it at `/tiles/terrain`; `style-terrain.json` (served at `/style-terrain.json`) adds a
  `raster-dem` source + `terrain` (exaggeration 1.3) + a root-level `sky`. The default `style.json` /
  `style-dark.json` stay flat/fast — terrain is opt-in.
- Consume it: `nm.map(maplibregl, el, { theme: "terrain" })`, or the demo's theme toggle
  (light → dark → terrain). Tune `exaggeration` in `style-terrain.json` to taste.
