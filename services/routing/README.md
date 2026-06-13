# services/routing — Directions + Distance Matrix (Phase 2)

> **Status: built & verified.** Valhalla serving on `:8002`, graph built from the VN extract.

**What:** Turn-by-turn routing and many-to-many distance matrices via **Valhalla**.

**Why:** Fleet / last-mile logistics is a primary NullMaps use case. **Motorbike costing is first-class**
— send `costing=motor_scooter` (or `motorcycle`).

## How it's wired

- Image: `ghcr.io/gis-ops/docker-valhalla` — auto-builds the routing graph from
  `services/routing/custom_files/vietnam-latest.osm.pbf` (hardlinked from `data/raw/`) on first start,
  then reuses it. Graph + working files live in `custom_files/` (gitignored).
- Built from the **same** VN extract as the tiles — one data source for the whole stack.
- Elevation and time-zone DBs are disabled (faster build, less disk); admins are built (border rules).

## Run

```bash
make graph         # build/start Valhalla (first run builds the graph — minutes)
make route-test    # HCMC Ben Thanh -> Landmark 81, motor_scooter
make matrix-test   # 2x2 District-1 distance matrix, motor_scooter
```

## Endpoints (Valhalla native, on :8002)

| Endpoint              | Use |
|-----------------------|-----|
| `POST /route`         | Directions. `{"locations":[...],"costing":"motor_scooter"}` |
| `POST /sources_to_targets` | Distance matrix (many-to-many) |
| `POST /locate`        | Debug: what edge a coordinate snaps to |
| `GET  /status`        | Readiness + tileset timestamp |

### Verified (HCMC, motor_scooter)

- Ben Thanh → Landmark 81: **5.4 km / 9 min**. Costing is differentiated:
  motor_scooter 5.4 km vs auto 6.3 km vs bicycle 5.4 km/19 min — scooter takes smaller roads.
- 2×2 District-1 matrix: all cells routable.

## Gotcha — coordinate snapping

Valhalla snaps each input to the nearest edge. If a coordinate lands on a **restricted or one-way
edge** you can get `error 442 "No path could be found"` or `null` matrix cells even though the network
is fine. Seen here:
- The airport airside perimeter road ("VĐ. bảo vệ sân bay") is access-restricted → no public route.
- Landmark 81's podium snap edge is arrival-only → fine as a destination, `null` as a matrix source.

**For callers:** snap to public arterials, or pass a `radius`/`search_filter` so Valhalla can pick a
routable edge. This is why the Phase-4 adapter should geocode to routable points, not raw pins.

## Multi-vehicle optimization (VRP) — VROOM

Valhalla's `/optimized_route` solves a **single**-vehicle visit order (TSP). For **many stops
across many vehicles** (delivery rounds, dispatch) NullMaps adds **VROOM** — it assigns jobs to
vehicles and orders each route, honoring capacities, time windows and skills.

- **Service:** `vroom` (`ghcr.io/vroom-project/vroom-docker`), internal-only, `mem_limit 512m`.
- **Routing backend:** the **same Valhalla** graph — VROOM pulls its cost matrix from `valhalla:8002`
  (config in `vroom-config.yml`; every profile points at the one instance, Valhalla picks the costing
  per request). So a 1-line change to `vroom-config.yml` host is all that ties them together.
- **Exposed via the adapter:** `POST /v1/optimize` (key-gated like all `/v1/*`). The adapter validates
  the problem and defaults each vehicle's `profile` to `motor_scooter` (override per vehicle, or set
  `VROOM_PROFILE`).

### Request / response (VROOM format)

Body = a [VROOM problem](https://github.com/VROOM-Project/vroom/blob/master/docs/API.md): `vehicles`
(+ `jobs` and/or `shipments`). **Coordinates are `[lon, lat]`.**

```bash
curl -X POST "$BASE/v1/optimize?key=$API_KEY" -H 'Content-Type: application/json' -d '{
  "vehicles": [
    {"id": 1, "start": [106.700, 10.776], "end": [106.700, 10.776]},
    {"id": 2, "start": [106.660, 10.762], "end": [106.660, 10.762]}
  ],
  "jobs": [
    {"id": 1, "location": [106.693, 10.769], "service": 300},
    {"id": 2, "location": [106.682, 10.800], "service": 300},
    {"id": 3, "location": [106.715, 10.730], "service": 300}
  ]
}'
```

Returns the VROOM solution: `routes` (per-vehicle ordered `steps`), `summary` (cost/duration/…),
and `unassigned`. Add `"options": {"g": true}` to the body for route geometry.

## Speed limits — `GET /v1/speed_limit`

Road speed limits along a path or at a point, via Valhalla `/trace_attributes`.

- `?path=lat,lng|lat,lng|...` (a route or GPS trace) or `?path` replaced by `?location=lat,lng`.
- Returns per road-segment: `speed_limit` (the **OSM posted limit**, km/h) and `speed`
  (Valhalla's **modeled** speed, always present). `?mode=` picks the costing (default motorbike).

> **Honest coverage note:** VN OSM `maxspeed` tagging is **sparse**, so `speed_limit` is **often
> `null`** — most roads have no posted limit in the data. `speed` (modeled from road class) is the
> always-present practical fallback. Useful for speeding checks + ETA sanity, not a legal source.

```bash
curl "$BASE/v1/speed_limit?path=10.7715,106.6960|10.7670,106.7110&key=$API_KEY"
# -> {"status":"OK","units":"km/h","segments":[{"name":"Lê Lai","speed_limit":null,"speed":57,...}]}
```

## Trip cost — `GET /v1/trip_cost`

Estimated **fuel + expressway toll** for a trip — for tourist directions or rental quotes.

```bash
curl "$BASE/v1/trip_cost?origin=10.79,106.755&destination=10.935,107.16&vehicle=car&key=$KEY"
# -> distance, duration, fuel{liters,cost}, toll{tolled_distance,cost}, total_cost, polyline
```

- `vehicle=car|motorbike|truck` sets the Valhalla costing + default fuel use + toll rate.
- **Fuel** = distance × `fuel_consumption` (L/100km) × `fuel_price` (VND/L). Both overridable;
  VN fuel price changes ~every 10 days, so pass the current `?fuel_price=`.
- **Toll** = the route's **tolled km** (Valhalla `edge.toll`) × `toll_per_km` — VN expressways
  charge by distance. Motorbikes are banned from expressways → rate 0. Overridable `?toll_per_km=`.

> **Estimate, not a quote.** Tolled distance is exact (from OSM `toll=yes` via Valhalla), but the
> per-km rate is a class average and **flat per-station BOT tolls aren't modeled** — for exact
> fares you'd add a curated toll-station table (no open VN dataset exists; verified). Defaults:
> car 8 L/100km + 2000 ₫/km, motorbike 1.8 L/100km + 0, fuel 23 500 ₫/L (`FUEL_PRICE_VND`).

## When extending

- Custom motorbike tuning (avoid highways, alley preferences) goes in the request costing options, or a
  Valhalla config override mounted into `custom_files/`.
- `make route-test` / `make matrix-test` are the smoke tests; keep them green.
