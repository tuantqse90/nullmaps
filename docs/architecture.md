# NullMaps — Architecture

Self-hosted geospatial backend for internal NullShift Labs use. Single operator, single box, one shared
API key. See `CLAUDE.md` for the full framing and decision log.

## Component diagram

```
                       ┌─────────────────────────────┐
   my apps  ──Google/  │  adapter (FastAPI, Phase 4)  │
            Goong JSON │  one shared API_KEY          │
                       └───────┬─────────┬───────┬────┘
                               │         │       │
                  ┌────────────▼──┐  ┌───▼────┐  ▼ tiles
                  │ Valhalla (P2) │  │geocoder│  Martin (P1)
                  │ route/matrix  │  │ (P3)   │  ┌────────────┐
                  │ motorbike     │  │ SQLite │  │ vietnam.   │
                  └───────┬───────┘  └───┬────┘  │ pmtiles    │
                          │              │       └─────▲──────┘
                          └──── built from ────────────┘
                               vietnam-latest.osm.pbf
                                                       MapLibre GL ◄── my apps' map
```

All three native engines derive from the **same Geofabrik Vietnam extract**.

## Data flow (Phase 1)

1. `make tiles`: download `vietnam-latest.osm.pbf` → Planetiler → `data/vietnam.pmtiles`.
2. Martin serves the PMTiles over HTTP (range requests).
3. MapLibre (demo or my app) loads Martin's TileJSON and renders the VN basemap.

## Stack

| Concern   | Choice                          | Why |
|-----------|---------------------------------|-----|
| Render/SDK| MapLibre GL JS, `react-map-gl`  | Open, no vendor SDK |
| Tiles     | Planetiler → PMTiles → Martin   | Fast VN build, single-file archive |
| Routing   | Valhalla                        | First-class motorbike costing |
| Geocoding | lightweight SQLite (FTS5/R*Tree) | Typeahead + diacritic folding; Photon = prod swap-in |
| Adapter   | FastAPI (Python 3.12)           | Repoint apps without client rewrites |
| Gateway   | Caddy (`:8088`, key-gated front door) | Single entrypoint; engines have no public ports |
| AI helper | LiteLLM → Qwen (optional, Phase 5) | Vietnamese address normalization, opt-in `?normalize=1` |
| Terrain   | Copernicus GLO-90 DEM → GDAL/tippecanoe | Hillshade + contour overlays via Martin |
| Store     | PostgreSQL + PostGIS            | Spatial store |
| Deploy    | Docker Compose on Hetzner/Coolify | One box, compose = source of truth |

## Pillars

- **Privacy** — fully self-hosted; location/usage data never leaves the box.
- **AI** — optional Phase 5 LiteLLM→Qwen address normalizer.
