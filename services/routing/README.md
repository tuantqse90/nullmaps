# services/routing — Directions + Distance Matrix (Phase 2)

> **Status: scaffolded, not built.** Lands in Phase 2. Bump to Phase 1 priority if fleet/dispatch is
> the immediate need.

**What:** Turn-by-turn routing and many-to-many distance matrices via **Valhalla**.

**Why:** Fleet / last-mile logistics is a primary NullMaps use case. **Motorbike costing is required,
not optional** (`costing=motor_scooter`, or `motorcycle`).

## Plan

- Build the Valhalla routing graph from the **same** `data/raw/vietnam-latest.osm.pbf` used for tiles.
- Expose:
  - `/route`  — directions (motorbike-first).
  - `/matrix` — many-to-many for dispatch logic.
- Persist `valhalla_tiles/` (gitignored) so the graph survives restarts.

## When implementing

- Uncomment the `valhalla` service in `docker-compose.yml`.
- Default costing in NullMaps requests should be `motor_scooter`.
- Wire `make route-test` to a real HCMC motorbike smoke test.
