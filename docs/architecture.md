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
                  │ Valhalla (P2) │  │ Photon │  Martin (P1)
                  │ route/matrix  │  │ (P3)   │  ┌────────────┐
                  │ motorbike     │  │ geocode│  │ vietnam.   │
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
| Geocoding | Photon                          | Typeahead + diacritic folding |
| Adapter   | FastAPI (Python 3.12)           | Repoint apps without client rewrites |
| Store     | PostgreSQL + PostGIS            | Spatial store |
| Deploy    | Docker Compose on Hetzner/Coolify | One box, compose = source of truth |

## Pillars

- **Privacy** — fully self-hosted; location/usage data never leaves the box.
- **AI** — optional Phase 5 LiteLLM→Qwen address normalizer.
