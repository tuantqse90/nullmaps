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

**Terrain overlays:** hillshade + 100 m contour lines (from the Copernicus GLO-90 DEM) build via
`infra/build-hillshade.sh` / `infra/build-contour.sh` and serve through Martin alongside the basemap.

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
make adapter-test                 # Directions + Matrix + Geocode/Autocomplete (all live)
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

## AI address cleanup (Phase 5 — optional)

Off by default (no-op). Set `LLM_MODEL` + a provider key (DashScope Qwen, local Qwen, any
OpenAI-compatible endpoint) to enable. The adapter applies it only on opt-in `?normalize=1`,
fail-open. `make norm-test`; details in [`services/normalizer/README.md`](services/normalizer/README.md).

## Roadmap

| Phase | Capability | Engine | Status |
|---|---|---|---|
| 1 | Tiles + MapLibre SDK | Planetiler → PMTiles → Martin | **done** |
| 2 | Directions + Matrix (motorbike-first) | Valhalla | **done** |
| 3 | Geocoding / Reverse / Autocomplete | lightweight (pyosmium+SQLite); Photon for prod | **done** |
| 4 | Google/Goong-compat API (**required**) | FastAPI adapter | **done** (directions, matrix, geocode/reverse, autocomplete, nearby, details + isochrone/snap) |
| 5 | AI address helper (optional) | LiteLLM → Qwen | **done** (ships no-op; enable with a key) |

## Production entrypoint & deploy

Prod publishes **only the gateway** (Caddy, `:8088`) — it gates `/maps/*` + `/v1/*` on `API_KEY` and
fronts tiles/demo; the engines are internal (no published ports).

```bash
docker compose -f docker-compose.yml up -d     # PROD: gateway only (engines internal)
docker compose up -d                           # DEV: + override re-exposes engine ports for make *-test
```

Deploy to VPS: [`docs/runbook-deploy-vps.md`](docs/runbook-deploy-vps.md).
CI (compose validation + py3.12 unit tests) runs on every push.

## Client SDK & API docs

One-import JS/TS client (Directions w/ turn-by-turn steps, Matrix, Geocode, Autocomplete, TSP,
isochrone, snap, + MapLibre embed): [`client/`](client/). Interactive OpenAPI/Swagger UI at
`https://maps.nullshift.sh/docs`.

```js
import { NullMaps } from "./client/nullmaps.js";
const nm = new NullMaps({ key: "YOUR_KEY" });
const route = await nm.directions("10.7725,106.6980", "10.7951,106.7218");  // route.routes[0].legs[0].steps
nm.map(maplibregl, "map");                                                  // self-hosted basemap
```

## Stack

MapLibre GL JS · Planetiler · PMTiles · Martin · Valhalla · lightweight geocoder · FastAPI (Python 3.12)
· LiteLLM · Caddy gateway. Docker Compose is the source of truth; deployed on a **VPS via plain `docker compose`**.

## Data

Default source: **Geofabrik Vietnam extract** (`vietnam-latest.osm.pbf`). NullMaps **never pulls
full-planet OSM** without an explicit decision. Tile/graph artifacts are gitignored.

## Box sizing (single box)

| Stage | RAM | Disk | Note |
|---|---|---|---|
| Phase 1 (tiles) | 2–4 GB | ~5 GB | Planetiler build is the peak |
| + Phase 2 Valhalla | +several GB | + graph tiles | RAM-heavy graph build |
| + Phase 3 geocoder (SQLite) | ~256 MB | ~56 MB index | lightweight; Photon = heavier prod swap-in |

Re-measure and update this table as each service lands. Vietnam-only scope keeps a single box viable.

## Repo layout

```
services/{tiles,routing,geocoder,adapter}/   data/raw/   infra/   docs/
docker-compose.yml   Makefile   .env.example
```

## License & data attribution

This repository's **code is MIT licensed** (see [`LICENSE`](LICENSE)). The MIT license
covers the code in this repo only — **not** the upstream data or engines below, which carry
their own terms. Keep these attributions in any deployment:

- **Map data © OpenStreetMap contributors** — [ODbL](https://www.openstreetmap.org/copyright).
  Tiles, the Valhalla routing graph, and the lightweight geocoder derive from the Geofabrik
  Vietnam extract.
- **POIs / places / divisions from [Overture Maps](https://overturemaps.org)**
  ([CDLA-Permissive 2.0](https://cdla.dev/permissive-2-0/) + ODbL components; data from Meta,
  Microsoft, Esri, OSM).
- Engines: routing **[Valhalla](https://github.com/valhalla/valhalla)**, geocoding
  **[Photon](https://github.com/komoot/photon)**, optimization
  **[VROOM](https://github.com/VROOM-Project/vroom)**, tiles **[Martin](https://github.com/maplibre/martin)**,
  rendering **[MapLibre GL JS](https://maplibre.org)** — each under its own license.
