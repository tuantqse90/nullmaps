# NullMaps

**Self-hosted geospatial backend for internal NullShift Labs use** — tiles, routing, geocoding for my
own apps and fleet tooling, so I stop paying Goong / Google Maps.

> Not a public Maps platform. Single operator, single box, one shared API key. See [`CLAUDE.md`](CLAUDE.md)
> for the full framing and decision log, and [`docs/architecture.md`](docs/architecture.md) for the design.

**Pillars:** **Privacy** (fully self-hosted — location/usage data never leaves the box) and **AI**
(optional Phase 5 Vietnamese address normalizer).

## Quickstart (Phase 1 — tiles + map)

```bash
cp .env.example .env          # set API_KEY; adjust ports if needed
make demo                     # build VN PMTiles (if needed) + serve
# → http://localhost:8080     # Ho Chi Minh City basemap
```

`make help` lists all commands. Full steps: [`docs/runbook-phase1-tiles.md`](docs/runbook-phase1-tiles.md).

## Routing (Phase 2 — motorbike-first)

```bash
make graph        # build/start Valhalla (first run builds the VN graph)
make route-test   # HCMC motorbike route   |   make matrix-test  # 2x2 matrix
```

Native Valhalla on `:8002` — `POST /route`, `POST /sources_to_targets`, `costing=motor_scooter`.
Details + gotchas: [`docs/runbook-phase2-routing.md`](docs/runbook-phase2-routing.md).

## Google/Goong-compat adapter (Phase 4 — repoint your apps)

```bash
docker compose up -d adapter      # :8010
make adapter-test                 # Directions + Matrix (live), geocode 503 (pending Phase 3)
```

Drop-in Google shapes (auth via `?key=` or `X-API-Key`), motorbike-first by default:
`directions/json`, `distancematrix/json`, `geocode/json` (`address=` or `latlng=`),
`place/autocomplete/json`. See [`services/adapter/README.md`](services/adapter/README.md).

## Geocoding (Phase 3 — lightweight, internal-use)

```bash
make geo-index    # build the VN SQLite index + start the geocoder (:2322)
make geo-test     # autocomplete / geocode / reverse
```

~308k named VN features, diacritic-folded. "Good enough," not Photon — see
[`services/geocoder/README.md`](services/geocoder/README.md) for the ranking caveat.

## Roadmap

| Phase | Capability | Engine | Status |
|---|---|---|---|
| 1 | Tiles + MapLibre SDK | Planetiler → PMTiles → Martin | **done** |
| 2 | Directions + Matrix (motorbike-first) | Valhalla | **done** |
| 3 | Geocoding / Reverse / Autocomplete | lightweight (pyosmium+SQLite); Photon for prod | **done** |
| 4 | Google/Goong-compat API (**required**) | FastAPI adapter | **done** (all 4 endpoints live) |
| 5 | AI address helper (optional) | LiteLLM → Qwen | future |

## Stack

MapLibre GL JS · Planetiler · PMTiles · Martin · Valhalla · Photon · PostGIS · FastAPI (Python 3.12).
Docker Compose is the source of truth; deployed on **Hetzner via Coolify**.

## Data

Default source: **Geofabrik Vietnam extract** (`vietnam-latest.osm.pbf`). NullMaps **never pulls
full-planet OSM** without an explicit decision. Tile/graph artifacts are gitignored.

## Box sizing (single box)

| Stage | RAM | Disk | Note |
|---|---|---|---|
| Phase 1 (tiles) | 2–4 GB | ~5 GB | Planetiler build is the peak |
| + Phase 2 Valhalla | +several GB | + graph tiles | RAM-heavy graph build |
| + Phase 3 Photon/ES | +several GB | + ES index | hungriest component |

Re-measure and update this table as each service lands. Vietnam-only scope keeps a single box viable.

## Repo layout

```
services/{tiles,routing,geocoder,adapter}/   data/raw/   infra/   docs/
docker-compose.yml   Makefile   .env.example
```
