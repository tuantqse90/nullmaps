# services/adapter — Google/Goong-compat shim (Phase 4, REQUIRED)

> **Status: all four endpoints live.** Directions + Matrix → Valhalla; Geocoding + Autocomplete →
> the Phase-3 geocoder. Runs on `:8010` (8000 collides locally).

**What:** A thin **FastAPI** shim that maps **Google Maps API** request/response shapes onto the native
NullMaps engines (Martin, Valhalla, Photon).

**Why:** My existing apps already speak Google Maps / Goong shapes. This adapter lets me repoint them at
NullMaps **without rewriting client code**. Goong's REST endpoints mirror Google's closely, so a
Google-compatible adapter covers most of Goong too — add Goong-specific field diffs as needed.

## Build approach (incremental)

As each native engine comes online (Phases 1–3), expose its Google-shaped endpoint here so I can
repoint one app immediately. Likely mappings:

| Google/Goong endpoint        | Native engine            |
|------------------------------|--------------------------|
| Directions API               | Valhalla `/route`        |
| Distance Matrix API          | Valhalla `/matrix`       |
| Geocoding / Reverse API      | Photon `/geocode`,`/reverse` |
| Places Autocomplete API      | Photon `/autocomplete`   |
| Static/JS Maps (tiles)       | Martin / MapLibre        |

## ⚠️ Before writing routes

**Ask the operator which Google/Goong endpoints the apps actually call**, and implement **only those**.
Do not build the full Google surface speculatively.

## Auth

Single shared `API_KEY` from env — check it on every request. No key management, no quotas.

## Endpoints

| Endpoint | Maps to | Status |
|---|---|---|
| `GET /maps/api/directions/json` | Valhalla `/route` | **live** |
| `GET /maps/api/distancematrix/json` | Valhalla `/sources_to_targets` | **live** |
| `GET /maps/api/geocode/json` | geocoder `/geocode` (`address=`) or `/reverse` (`latlng=`) | **live** |
| `GET /maps/api/place/autocomplete/json` | geocoder `/autocomplete` (`input=`) | **live** |
| `GET /healthz` | — | open (no key) |

**Travel mode → costing** (motorbike-first): unspecified / `two_wheeler` / `bike` / `scooter` →
`motor_scooter`; `motorcycle` → `motorcycle`; `driving`/`car` → `auto`; `walking` → `pedestrian`;
`bicycling` → `bicycle`. Accepts Google's `mode=` or Goong's `vehicle=`.

Valhalla returns polyline precision-6 shapes; the adapter re-encodes to precision-5 for Google's
`overview_polyline` (`app/polyline.py`).

## Run & verify

```bash
docker compose up -d adapter
make adapter-test     # directions + matrix + geocode
```

Verified (HCMC, against the live stack):
- Directions default = motorbike **5.4 km / 9 min**; `mode=driving` = **6.3 km** (mode mapping works)
- 2×2 distance matrix all routable
- geocode `address=nguyen hue` → Nguyễn Huệ; `latlng=10.7725,106.6980` → Chợ Bến Thành
- autocomplete `input=ben thanh` → Bến Thành; missing key → 403

## Tests

`tests/` has unit tests (Valhalla mocked) for shape mapping, mode→costing, and polyline roundtrip.
Pure-logic + integration (`make adapter-test`) are the authoritative checks. Note: Starlette's
`TestClient` is flaky under Python 3.14 locally — run unit tests in CI (3.12) or rely on
`make adapter-test` against the running container.
