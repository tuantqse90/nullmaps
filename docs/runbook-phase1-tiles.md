# Runbook — Phase 1: build & serve Vietnam tiles

## Prereqs

- Docker + Docker Compose, `make`, `curl`.
- ~Disk: VN extract ~0.5–1 GB, PMTiles output ~1–2 GB. Build RAM: a few GB.

## Steps

```bash
cp .env.example .env          # set API_KEY (and ports if 3000/8080 are taken)
make fetch                    # download data/raw/vietnam-latest.osm.pbf  (VN only — never planet)
make tiles                    # Planetiler -> data/vietnam.pmtiles  (minutes)
make demo                     # start martin + demo
open http://localhost:8080    # HCMC basemap
```

`make demo` builds tiles automatically if `data/vietnam.pmtiles` is missing.

## Verify

- `curl http://localhost:3000/tiles/health` → ok.
- `curl http://localhost:3000/tiles/catalog` → lists the `vietnam` source.
- `curl http://localhost:8080/tiles/vietnam` → TileJSON (same-origin path the demo uses).
- Demo page renders roads/water/labels around Ho Chi Minh City and pans/zooms smoothly.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Blank map, no errors | PMTiles protocol not registered, or wrong `source-layer`. The demo registers it; check the layer names match the OpenMapTiles schema. |
| 404 on tiles | Martin source name ≠ `vietnam`. It's derived from the filename — keep `PMTILES_FILE=vietnam.pmtiles`. |
| CORS errors | Shouldn't happen — Caddy reverse-proxies Martin under `/tiles` on the demo origin. If you point MapLibre at `:3000` directly you'll hit CORS; use the `/tiles/...` path instead. |
| Planetiler OOM | Give Docker more RAM, or build on the Hetzner box. |
| Build hangs on `water-polygons-split-3857.zip` | Planetiler's built-in downloader doesn't retry, and the `osmdata.openstreetmap.de` mirror is frequently slow/stalls. Run `make sources` first — it curls the three auxiliary sources (lake centerlines, natural-earth, water polygons) with retry + resume before the build. `make tiles` depends on it. |
| `... .shp.zip does not exist. Run with --download` | Auxiliary sources missing. These are small global basemap files (NOT planet OSM). `make sources` fetches them; the build keeps OSM local via `--osm-path`. |

## Box sizing (keep current)

- **Phase 1 (tiles only):** modest — 2–4 GB RAM, ~5 GB disk.
- **Phase 2 (+Valhalla):** graph build is RAM-heavy; budget extra RAM + disk for `valhalla_tiles`.
- **Phase 3 (+Photon/Elasticsearch):** the hungriest — Elasticsearch wants several GB RAM on its own.
  Re-measure and update the root README before sizing the prod box.
