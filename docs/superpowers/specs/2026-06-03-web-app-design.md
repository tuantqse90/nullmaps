# NullMaps Web App — Design Spec

**Date:** 2026-06-03
**Branch:** `feat/web-app`
**Status:** Approved → build

## Context

Turn the bare basemap demo (`services/tiles/style/index.html`) into a Google-Maps-like web app served at
`maps.nullshift.sh`, surfacing the backend that the upgrade program built: geocode/autocomplete (③a typo +
legacy districts), directions with Vietnamese turn-by-turn (① + follow-up), theme variants (④a/④b), and
reverse-geocode. Single-operator internal tool.

## Decisions (locked)

- **Key handling: gateway proxy** — a Caddy route `/app/*` strips the prefix and injects the shared
  `X-API-Key` server-side, so the in-browser app calls the API **keyless** (the key never reaches the
  browser). The existing `/maps/*` gate is untouched (the operator's other apps still send the key).
- **Scope:** search (autocomplete) · directions (route + VN steps + ETA + mode xe máy/ô tô/đi bộ) · theme
  toggle light/dark/terrain · click-to-reverse-geocode. No fleet extras (nearby/isochrone/snap) for now.

## Design

### Gateway (`infra/gateway/Caddyfile`)

Add **before** the catch-all demo handler:

```
# Keyless in-browser app proxy: inject the API key server-side so the page never sees it.
@app path /app/*
handle @app {
    uri strip_prefix /app
    reverse_proxy adapter:8000 {
        header_up X-API-Key {$API_KEY}
    }
}
```

The app calls `/app/maps/api/...` (no key); Caddy rewrites to `/maps/api/...` + adds the key header; the
adapter accepts it (its `require_key` reads `X-API-Key`). `@api_noauth` (path `/maps/*`) does not match
`/app/*`, so this is a separate, keyless surface. (Open to anyone hitting the page — acceptable for an
internal demo; basic-auth on `/app/*` can be added later.)

### Frontend (`services/tiles/style/index.html`, rewritten)

One self-contained page (MapLibre GL JS + pmtiles from unpkg, as today; all app JS/CSS inline — no
external SDK file dependency). Served by the `demo` Caddy container via the existing volume mount.

- **Map:** full-screen, HCMC center, NavigationControl + GeolocateControl, PMTiles protocol registered.
- **Search** (floating card, top-left): debounced input → `/app/maps/api/place/autocomplete/json?input=`
  → suggestion dropdown (main + secondary text); pick → `/app/maps/api/geocode/json?address=<main_text>`
  → fly to + marker + popup.
- **Directions** (toggle inside the card): From/To fields (each with the same autocomplete) or "đặt trên
  bản đồ" (click A then B); mode chips 🛵 `two_wheeler` (default) / 🚗 `driving` / 🚶 `walking`;
  "Tìm đường" → `/app/maps/api/directions/json?origin=&destination=&mode=` → decode `overview_polyline`,
  draw a casing+line layer, `fitBounds`, and a results card with `legs[0].distance.text` +
  `duration.text` + a scrollable list of `steps[].html_instructions` (Vietnamese).
- **Theme** (top-right segmented control): ☀️ `/style.json` · 🌙 `/style-dark.json` · 🏔️
  `/style-terrain.json` (+ `easeTo` pitch 60). On `style.load`, re-add the route layer (setStyle wipes
  custom layers).
- **Click-to-reverse:** map click (when not setting directions points) →
  `/app/maps/api/geocode/json?latlng=` → popup `formatted_address`.
- **Helpers (inline):** `api(path, params)` (fetch the keyless proxy), `decodePolyline` (precision-5),
  `drawRoute`, `clearRoute`, small debounce, a tiny toast for errors.
- **Style:** clean cards (rounded, soft shadow), system-ui, NullShift brand green (#00B260 / #163300),
  legible on both light and dark basemaps; not a generic gradient look.

### Error handling

Fetch failures → a small toast ("Lỗi kết nối"); empty geocode/autocomplete → "Không tìm thấy"; directions
`ZERO_RESULTS` → "Không tìm được đường".

## Testing

No frontend unit harness. Verify by: `curl -s maps.nullshift.sh/app/maps/api/geocode/json?address=q1`
(keyless → 200 "Quận 1"); load the page and exercise search, directions (VN steps), theme cycle, and
map-click reverse. The committed page is validated for well-formed HTML and that `/maps/*` still requires a
key (regression).

## Deploy

Add the gateway route + reload gateway; the new `index.html` is served live via the demo volume.

## Out of scope

Fleet extras (nearby/isochrone/snap), basic-auth on `/app`, saved places, multi-stop UI, mobile-native.
