# CLAUDE.md — NullMaps

Context and working agreement for Claude Code (and humans) on this repo.

## What this is

**NullMaps** is a **self-hosted geospatial backend for internal NullShift Labs use**. It powers my own
apps and logistics/fleet tooling so I stop paying Goong or Google Maps.

**This is NOT a public Maps-Platform product.** No third-party customers, no billing, no developer
portal, no SLA, no multi-tenancy. Build for **a single operator (me)** with a small number of my own
apps as clients. When a design choice trades "platform generality" against "simple for one operator,"
always choose simple.

Accuracy bar: **"good enough for my use cases," not "beat Goong."** Ride OpenStreetMap and improve
opportunistically. Do **not** turn this into a data-collection company.

## NullShift pillars in play

- **Privacy** — fully self-hosted. My location and usage data never leave my box. This is the whole
  point: no queries to Google/Goong, no telemetry out.
- **AI** — the *optional* Phase 5 address helper (LiteLLM → local Qwen) to normalize messy Vietnamese
  address input before it hits the geocoder. Polish, not a moat.

## Capabilities

| Capability | Engine | Phase |
|---|---|---|
| Map tiles + web SDK | Planetiler → PMTiles, served by Martin; MapLibre GL JS | 1 |
| Directions (motorbike-first) | Valhalla (`motor_scooter`/`motorcycle`) | 2 |
| Distance Matrix (many-to-many) | Valhalla `/matrix` | 2 |
| Geocoding / Reverse | lightweight pyosmium+SQLite (Photon for prod) | 3 |
| Autocomplete / Places | same geocoder, diacritic-folded typeahead | 3 |
| Google/Goong-compat API | FastAPI adapter shim | 4 |
| AI address normalization | LiteLLM → Qwen (optional, no-op until configured) | 5 |

## Decisions already made (scaffold around these — do not relitigate)

- **Self-hosted, Docker-first.** Prod runs **plain `docker compose` on a VPS** (currently Hostinger,
  `/opt/nullmaps`, behind native Caddy); Coolify works as an alternative. `docker-compose.yml` is the
  source of truth for local + prod.
- **Single beefy box** to start, not a cluster. Photon's Elasticsearch + Valhalla's tile graph +
  PostGIS are RAM-hungry. Vietnam-only keeps it manageable. **RAM/disk sizing is documented in the
  README** — keep it current as services land.
- **Auth = one shared API key** in env (`API_KEY`). My apps send it; the adapter/services check it.
  No Kong/Tyk, no key management, no quotas.
- **Single front door:** prod publishes only the **gateway** (Caddy, `:8088`) — it gates `/maps/*` and
  `/v1/*` on the key and fronts tiles/demo; the engines (valhalla, geocoder, normalizer) have **no
  published ports**. `docker-compose.yml` is prod-safe; `docker-compose.override.yml` re-exposes engine
  ports for local `make *-test` only. Deploy prod with the base file ALONE.
- **Compat adapter is REQUIRED, not optional** — my existing apps already speak Google Maps / Goong
  shapes. The adapter lets me repoint them without rewriting client code. Goong's REST shapes mirror
  Google's, so a Google-compatible adapter covers most of Goong too.
- **Runtime:** Python 3.12 for glue services (adapter, helpers).
- **Data default:** the **Geofabrik Vietnam extract** (`vietnam-latest.osm.pbf`). **Never pull
  full-planet OSM without asking.**

## Architecture / chosen stack

- **Rendering / SDK:** MapLibre GL JS (web), `react-map-gl` for React. Styles authored in **Maputnik**.
  MapLibre Native for mobile only if/when needed.
  - MapLibre **requires the PMTiles protocol registered** (`pmtiles` JS `addProtocol`) or it fails
    silently on init. The demo does this.
- **Tiles:** Geofabrik VN extract → **Planetiler** → **PMTiles** (+ MBTiles option). Served via
  **Martin** (Rust), or static PMTiles behind Caddy/CDN for the cheapest path. `go-pmtiles` for
  regional extracts.
- **Routing + Matrix:** **Valhalla**, motorbike costing required (`motor_scooter`/`motorcycle`).
  Graph built from the same VN extract. Endpoints `/route`, `/matrix`.
- **Geocoding + Autocomplete:** **Photon** is the chosen production engine. On the dev box (disk/RAM
  too small for a Nominatim import) Phase 3 ships a **lightweight substitute**: `pyosmium` extracts
  named features from the VN extract into a SQLite **FTS5 + R*Tree** index (diacritic-folded),
  served by a small FastAPI service. "Good enough" for internal typeahead/reverse; swap in Photon on
  the Hetzner box when prominence-ranked forward geocoding matters. Pelias only if both fall short.
- **Data store:** PostgreSQL + PostGIS (plus whatever Valhalla/Photon manage internally).
- **Adapter (REQUIRED):** thin **FastAPI** shim mapping Google Maps request/response shapes onto the
  native engines. Implement **only the endpoints my apps actually use — ask which ones at Phase 4.**
- **Observability:** Grafana + Loki, lightweight, optional for v1.

## Repo layout

```
nullmaps/
  CLAUDE.md  README.md  docker-compose.yml  Makefile  .env.example  .gitignore
  services/
    tiles/      # Planetiler config, MapLibre style + demo, Martin/PMTiles serving
    routing/    # Valhalla config + motorbike profile + matrix   (Phase 2)
    geocoder/   # Photon setup + VN import                       (Phase 3)
    adapter/    # FastAPI Google/Goong-compat shim               (Phase 4)
  data/raw/     # OSM extracts (gitignored)
  infra/        # VPS deploy + ops scripts, scheduler units, env templates
  docs/         # architecture + runbooks
```

## Roadmap (build in this order)

1. **Tiles + render (done first):** VN PMTiles → Martin → MapLibre demo centered on **Ho Chi Minh
   City**. End-to-end visible win.
2. **Routing + Matrix:** Valhalla motorbike profile; `/route` + `/matrix`. *(Bump to Phase 1 priority
   if I say fleet/dispatch is the immediate need.)*
3. **Geocoding + Autocomplete:** Photon on VN data; `/geocode`, `/reverse`, `/autocomplete`.
4. **Compat adapter (REQUIRED):** Google/Goong-shaped endpoints for the APIs my apps use. Grow
   incrementally — as each native engine comes online, expose its Google-shaped endpoint so I can
   repoint an app immediately. **Ask which Google/Goong endpoints my apps call before implementing.**
5. **(Optional) AI address helper** — only if I ask.

## Conventions

- **Documentation-first.** Each `services/*` has its own README: what it is, why, how to run.
- **Composable services**, wired by the root compose. Maps stacks are assembled, not monolithic.
- Docs and code comments in **English**.
- Don't commit data: `*.osm.pbf`, `*.pmtiles`, `*.mbtiles`, `data/raw/`, `valhalla_tiles/` are
  gitignored.

## Working agreement for Claude

- **Do not scaffold beyond the current phase** without being asked.
- **Ask before pulling full-planet OSM data** — default to the Vietnam extract.
- At Phase 4, **ask which Google/Goong endpoints my apps actually call** before writing adapter routes.
- **Sovereignty:** internal use is not a legal blocker, but if any app is user-facing, label
  **Hoàng Sa / Trường Sa** correctly in the MapLibre style anyway.
