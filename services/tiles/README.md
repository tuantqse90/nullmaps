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

- `style/index.html` — MapLibre demo page (centered on HCMC, registers the PMTiles protocol).
- `Caddyfile` — static server config for the demo page.
- Planetiler runs via Docker from the root `Makefile` (no config file needed for the default profile).

## Notes

- MapLibre **requires `addProtocol("pmtiles", …)`** before using a `pmtiles://` source or it fails
  silently. The demo does this even when serving via Martin.
- Style editing: load the style into **Maputnik** to author visually.
- **Sovereignty:** if a consuming app is user-facing, ensure **Hoàng Sa / Trường Sa** are labelled
  correctly in the style.
