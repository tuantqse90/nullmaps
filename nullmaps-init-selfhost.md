# NullMaps — Project Init Prompt (Self-Hosted / Internal Use)

> Paste this as your **first message in Claude Code**, at the root of an empty repo.
> Working name `NullMaps` — rename freely.
> This replaces the earlier public-platform version — use **this** one.

---

You are helping me bootstrap **NullMaps**, a NullShift Labs project. Before writing any implementation code, read this brief, then (1) produce `CLAUDE.md`, (2) propose the repo file tree for my approval, and (3) scaffold **Phase 1 only**.

## What we're building

NullMaps is a **self-hosted geospatial backend for my own use** — it powers my own apps and logistics/fleet tooling so I stop paying Goong or Google Maps. **It is NOT a public Maps-Platform product:** no third-party customers, no billing, no developer portal, no SLA, no multi-tenancy. Build for a single operator (me) with a small number of my own apps as clients.

Capabilities I want:

- **Map Tiles + Web SDK** — vector tiles of Vietnam + a MapLibre map I embed in my apps
- **Directions** — routing, **motorbike-first** (fleet / last-mile is a primary use case)
- **Distance Matrix** — many-to-many for route / dispatch logic
- **Geocoding / Reverse Geocoding**
- **Autocomplete / Places** — address typeahead for my app forms

Accuracy bar is **"good enough for my use cases," not "beat Goong."** Ride OpenStreetMap and improve opportunistically. Do NOT turn this into a data-collection company.

## Decisions already made (scaffold around these)

- **Self-hosted, Docker-first**, deployed on **Hetzner via Coolify**. Docker Compose is the source of truth for local + prod.
- **Single beefy box** to start (not a cluster). Be mindful: Photon's Elasticsearch + Valhalla's tile graph + PostGIS are RAM-hungry — Vietnam-only keeps this manageable, but size the box and document the RAM/disk needs in the README.
- **Auth = one shared API key** in env, so my apps authenticate. No Kong/Tyk, no key management, no quotas.
- **My existing apps already call Google Maps / Goong APIs** — so a compat adapter (below) is a required part of the build, not optional.
- **Language/runtime:** Python 3.12 for any glue services.

## Architecture / chosen stack

- **Rendering / SDK:** MapLibre GL JS (web), `react-map-gl` for React. Styles authored in **Maputnik**. MapLibre Native for mobile only if/when I need it.
- **Tiles:** Generate from the **Geofabrik Vietnam OSM extract** with **Planetiler** -> **PMTiles** (+ MBTiles option). Serve via **Martin** (Rust tile server), or static PMTiles behind Caddy/CDN for the cheapest path. `go-pmtiles` for regional extracts.
- **Routing + Matrix:** **Valhalla** — motorbike costing (`motor_scooter` / `motorcycle`) is required, not optional. Expose `/route` and `/matrix`.
- **Geocoding + Autocomplete:** **Photon** (OSM-based, Elasticsearch, typeahead-friendly, diacritic folding for VN). Pelias noted as the heavier alternative only if Photon proves insufficient.
- **Data store:** PostgreSQL + PostGIS (plus whatever Valhalla/Photon manage internally).
- **Google/Goong-compat adapter (REQUIRED):** a thin **FastAPI** shim mapping **Google Maps API** request/response shapes onto the native engines. This is how my existing apps consume NullMaps without rewriting client code. Goong's API shapes are near-identical to Google's, so a Google-compatible adapter covers most of Goong too. **Implement ONLY the specific endpoints my apps actually use — ask me which ones when you reach the adapter phase.**
- **(Optional, later) AI address helper:** **LiteLLM** -> local **Qwen** to normalize messy Vietnamese address input (abbreviations, missing diacritics, typos) before it hits Photon. Polish, not a moat.
- **Observability:** Grafana + Loki (lightweight; optional for v1).

## Repo structure (monorepo, lean)

```
nullmaps/
  CLAUDE.md
  README.md
  docker-compose.yml          # the whole stack on one box
  Makefile                    # make tiles | up | demo | route-test
  .env.example                # API_KEY, data paths, ports
  .gitignore                  # *.osm.pbf, *.pmtiles, *.mbtiles, data/raw, valhalla_tiles
  services/
    tiles/                    # Planetiler config, MapLibre style, Martin/PMTiles serving
    routing/                  # Valhalla config + motorbike profile + matrix
    geocoder/                 # Photon setup + VN data import
    adapter/                  # FastAPI Google/Goong-compat shim (REQUIRED - my apps speak Google/Goong shapes)
  data/
    raw/                      # OSM extracts (gitignored)
  infra/                      # Coolify config, env templates
  docs/                       # architecture + runbooks
```

## Roadmap (build in this order)

- **Phase 1 — Tiles + render (do now):** Planetiler -> Vietnam PMTiles -> Martin serving -> a MapLibre demo page centered on **Ho Chi Minh City** showing the VN basemap. End-to-end visible win.
- **Phase 2 — Routing + Matrix:** Valhalla with motorbike profile; `/route` + `/matrix`. *(Bump this to Phase 1 priority if I say fleet/dispatch is the immediate need.)*
- **Phase 3 — Geocoding + Autocomplete:** Photon on VN data; `/geocode`, `/reverse`, `/autocomplete`.
- **Phase 4 — Compat adapter (REQUIRED):** Google/Goong-compatible endpoints for the APIs my apps use. Grow it incrementally — as each native engine (Phases 1-3) comes online, expose its Google-shaped endpoint so I can repoint an app immediately. When you start this phase, ask me which Google/Goong endpoints my apps actually call so you implement only those.
- **Phase 5 (optional) — AI address helper**, only if I ask.

## Conventions

- **Documentation-first.** Each `services/*` has its own README: what it is, why, how to run.
- **Composable services**, wired by the root compose. Maps stacks are assembled, not monolithic.
- **NullShift pillars in play:** **Privacy** (fully self-hosted — my location & usage data never leave my box) and **AI** (the optional address helper). State this frame in `CLAUDE.md`.
- Docs and code comments in English.

## What to produce in this session

1. `CLAUDE.md` — context, the **"internal-use, not a platform"** framing, stack decisions, conventions, pillars.
2. The lean file tree above, with a stub README in each `services/*`.
3. **Phase 1, working:** `docker-compose.yml` + `Makefile` so I can run `make tiles` (extract/build VN PMTiles) and `make demo` (serve via Martin + open a MapLibre HCMC demo).
4. `.env.example` + `.gitignore`.

**Start by showing me the proposed `CLAUDE.md` and file tree for approval. Do NOT scaffold beyond Phase 1. Ask me before pulling full-planet OSM data — default to the Vietnam extract. Scaffold `adapter/` as a real FastAPI service in the tree, but build its endpoints in Phase 4 (after the native engines); when you reach it, ask me which Google/Goong endpoints my apps call so you implement only those.**

### Technical hints (use these to avoid dead ends)

- Vietnam OSM extract: Geofabrik `vietnam-latest.osm.pbf`.
- Regional PMTiles extract: `pmtiles extract <source> vietnam.pmtiles --bbox=102.1,8.2,109.5,23.4` (rough mainland VN bbox = `minLon,minLat,maxLon,maxLat`).
- Planetiler builds VN tiles in minutes from the Geofabrik extract — much faster than tilemaker for this scope.
- MapLibre **requires the PMTiles protocol registered** (`pmtiles` JS lib `addProtocol`) or it fails silently on init.
- Valhalla motorbike: `costing=motor_scooter` (or `motorcycle`) in the route request; build the routing graph from the same VN extract.
- Photon is partial-token friendly out of the box; enable diacritic folding for the VN analyzer.
- For the adapter: Goong's REST endpoints (Place/Autocomplete, Geocode, Direction, DistanceMatrix) mirror Google's closely — map to Google shapes first, then add any Goong-specific field differences.
- Sovereignty note: for internal use this is **not a legal blocker**, but if any of my apps are user-facing, label Hoàng Sa / Trường Sa correctly in the MapLibre style anyway.
