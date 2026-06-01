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

## Roadmap

| Phase | Capability | Engine | Status |
|---|---|---|---|
| 1 | Tiles + MapLibre SDK | Planetiler → PMTiles → Martin | **active** |
| 2 | Directions + Matrix (motorbike-first) | Valhalla | scaffolded |
| 3 | Geocoding / Reverse / Autocomplete | Photon | scaffolded |
| 4 | Google/Goong-compat API (**required**) | FastAPI adapter | scaffolded |
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
