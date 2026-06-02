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
