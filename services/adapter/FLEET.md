# Fleet endpoints (rental use case)

GPS telemetry, geofence zones, and map-matched mileage — the geo primitives a vehicle-rental
platform (e.g. driftway) needs. All under `/v1/*` (key-gated). State lives in one writable
SQLite file (`services/adapter/app/fleet.py`, `FLEET_DB`, default `/fleet/fleet.db`, bind-mounted
from `data/fleet/` and so picked up by backups). Single-operator scale.

## Telemetry

| Endpoint | Purpose |
|---|---|
| `POST /v1/ping` | Ingest GPS. Body = one ping or `{"pings":[…]}`, each `{vehicle_id, lat, lon, ts?, speed?, heading?}` (ts = epoch s, default now). |
| `GET /v1/vehicles` | Latest position of every vehicle — the live fleet map. |
| `GET /v1/vehicles/{id}/track?from=&to=` | One vehicle's ordered ping track in a time window. |

```bash
curl -X POST "$BASE/v1/ping?key=$KEY" -H 'Content-Type: application/json' \
  -d '{"pings":[{"vehicle_id":"bike-9","lat":10.78,"lon":106.70,"speed":25}]}'
curl "$BASE/v1/vehicles?key=$KEY"
```

This is also the **seed for traffic-aware ETA** later: once pings accumulate, map-match +
aggregate per-edge speeds → Valhalla predicted-traffic.

## Geofence zones

Operator-defined polygons (allowed / restricted / pricing areas). `type` is free text; `props`
is arbitrary JSON (e.g. a price multiplier).

| Endpoint | Purpose |
|---|---|
| `POST /v1/zones` | Create a zone: `{name, type, geometry:<GeoJSON Polygon\|MultiPolygon>, props?}`. |
| `GET /v1/zones` | List zones (with geometry). |
| `DELETE /v1/zones/{id}` | Remove a zone. |
| `GET /v1/zones/check?location=lat,lng` | Which zones contain the point + `inside` (any match) — **drop-off validation, pricing, geofence-breach**. |

```bash
curl -X POST "$BASE/v1/zones?key=$KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Khu giao Q1","type":"allowed","geometry":{"type":"Polygon","coordinates":[[[106.69,10.77],[106.71,10.77],[106.71,10.79],[106.69,10.79],[106.69,10.77]]]}}'
curl "$BASE/v1/zones/check?location=10.78,106.70&key=$KEY"   # -> inside:true
```

Point-in-polygon is pure Python (ray casting, supports holes + MultiPolygon) — fine for a
handful of operator zones checked against a point.

## Map-matched mileage (distance billing)

`GET /v1/vehicles/{id}/mileage?from=&to=` — pulls the vehicle's GPS track, **snaps it to roads**
via Valhalla (`/trace_route`, `map_snap`), and returns the matched `distance` (the billable km,
not noisy raw-GPS distance) + `duration` + the route `polyline`. `?mode=` picks costing (default
motorbike). Tracks over ~1000 points are downsampled to fit Valhalla's trace limit.

```bash
curl "$BASE/v1/vehicles/bike-9/mileage?from=1717500000&to=1717503600&key=$KEY"
# -> {"status":"OK","distance":{"value":8430,"text":"8.4 km"},"polyline":{...},...}
```

## Composing for rental

- **Vehicle tracking / live map** ← `GET /v1/vehicles`.
- **Geofence-breach alert** ← on each ping (or periodically) call `/v1/zones/check` against the
  position; flag when an `allowed` zone no longer contains it.
- **Distance billing** ← `/v1/vehicles/{id}/mileage` between rental start/end timestamps.
- **Speeding detection** ← compare a ping's `speed` to `GET /v1/speed_limit?location=`.
- **Valid drop-off** ← `/v1/zones/check` at the return point.
