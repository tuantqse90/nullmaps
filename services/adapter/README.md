# services/adapter — Google/Goong-compat shim (Phase 4, REQUIRED)

> **Status: scaffolded as a real FastAPI service; endpoints built in Phase 4.**
> Only the health check is live today.

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

## Tests

```bash
cd services/adapter
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q          # health shape + shared-key auth (accept/reject) — 5 tests
```

## Run (current: health only)

```bash
docker compose up -d adapter        # after uncommenting the adapter service
curl localhost:8000/healthz
```
