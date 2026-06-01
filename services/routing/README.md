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

## When extending

- Custom motorbike tuning (avoid highways, alley preferences) goes in the request costing options, or a
  Valhalla config override mounted into `custom_files/`.
- `make route-test` / `make matrix-test` are the smoke tests; keep them green.
