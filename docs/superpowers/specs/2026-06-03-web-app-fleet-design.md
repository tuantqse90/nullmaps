# NullMaps Web App — Fleet Features (②) Design Spec

**Date:** 2026-06-03
**Branch:** `feat/web-app-fleet`
**Status:** Approved (picked from upgrade menu) → build

## Context

Second iteration of the web app (`services/tiles/style/index.html`). The first iteration
([2026-06-03-web-app-design.md](2026-06-03-web-app-design.md)) shipped search · directions · theme ·
reverse-geocode and explicitly deferred the "fleet extras." This iteration adds exactly those, all
surfacing endpoints that already exist in the adapter — no backend changes.

Single-operator internal tool. Keyless via the `/app/*` gateway proxy (now basic-auth gated).

## Features (all wire existing endpoints)

| Feature | Endpoint | UX |
|---|---|---|
| **Multi-stop directions (TSP)** | `GET /maps/api/directions/json` with `waypoints=optimize:true\|lat,lng\|…` | "+ Thêm điểm dừng" adds via-inputs (autocomplete + remove); after A & B are set, extra map-clicks append vias; "Tối ưu thứ tự" checkbox → `optimize:true` (Valhalla `/optimized_route`). Result sums all legs, shows per-leg "Chặng N" headers + continuous step numbering. |
| **Nearby POI** | `GET /maps/api/place/nearbysearch/json?location=&type=&radius=` | "📍 Gần đây" reveals 8 category chips (restaurant/cafe/fuel/bank/pharmacy/hospital/hotel/marketplace — all verified populous in the live DB). Click → search around current map center → purple markers + a clickable result list (fly-to on click). |
| **Isochrone** | `GET /v1/isochrone?location=&contours=5,10,15&mode=` | "⏱️ Vùng tới" toggles a pick-mode; click the map → 3 concentric reachability bands (green/amber/red, fill-opacity 0.16) for the current travel mode, drawn under the route line. |
| **Share link** | (client-only) | "🔗 Chia sẻ" encodes current state into the URL (`?route=lat,lng;…&mode=&opt=1` for a route, else `?ll=lat,lng&q=` for a search) and copies it. On load, `restoreState()` parses those params and rebuilds the route (auto-runs) or drops the search marker. |
| **Mobile-responsive** | (CSS) | `@media (max-width:560px)`: panel goes full-width edge-to-edge, theme control moves to bottom-right, nav controls reflow, route step list shortens; route `fitBounds` padding adapts to viewport. |

## Design notes

- **One self-contained `index.html`** (MapLibre GL JS 4.7.1 + pmtiles from unpkg). All app JS/CSS inline.
- **Click-mode precedence:** isochrone-pick > directions-point-set (when panel open) > reverse-geocode.
- **Overlay re-add:** `paintRoute()` + `paintIso()` + `applyTerrain()` re-run on every `style.load`
  (theme switch wipes custom sources/layers). Terrain stays a `setTerrain()` modifier on the base
  style, never a baked terrain style (avoids the maplibre 4.7.1 `_checkLoaded` blank-map bug).
- **Isochrone bands** sorted largest-contour-first before `setData` so the rings read concentric;
  colored by `["interpolate", …, ["get","contour"], 5,green,10,amber,15,red]`.
- **Dynamic stop inputs** get unique ids (`stop1`, `stop2`, …) and reuse `attachAutocomplete`.
- **HTML escaping** (`esc()`) on every interpolated place name (autocomplete, nearby list, steps).

## Error handling

Fetch failure → "Lỗi kết nối" toast. Empty nearby → "Không có <loại> gần đây". Isochrone empty →
"Không tính được vùng". Directions `ZERO_RESULTS` → "Không tìm được đường". Clipboard blocked →
`prompt()` fallback with the link.

## Testing

No frontend unit harness — verification is `node --check` on the extracted inline script (syntax) plus a
headless puppeteer-core pass against the live site (authenticated): autocomplete dropdown, 2-point
directions (legs+steps+ETA), multi-stop (≥2 legs + waypoint_order), nearby (markers+list), isochrone
(features returned + band layer), share-link round-trip (encode → fresh-load restore auto-routes), theme
cycle (zero pageerrors), and mobile viewport (panel spans width). A screenshot is captured for visual
confirmation.

## Deploy

`index.html` is served by the `demo` Caddy container via the existing volume mount — deploy is a file
overwrite on the box (`git archive HEAD <file> | ssh tar -xf -`), no container rebuild.

## Out of scope

Traffic-aware ETA (separate sub-project ③), saved places, turn-by-turn voice, offline, drag-to-reorder
stops, fleet GPS ingestion.
