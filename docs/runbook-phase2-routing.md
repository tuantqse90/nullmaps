# Runbook — Phase 2: Valhalla routing + matrix

## Prereqs

- Phase 1 data present (`data/raw/vietnam-latest.osm.pbf`).
- Disk: Valhalla VN graph ≈ a few GB under `services/routing/custom_files/valhalla_tiles/`.
- RAM: build peaks ~1–1.5 GB; serving is light.

## Build & start

```bash
make graph          # hardlinks the pbf into custom_files, starts Valhalla
                    # first run auto-builds the graph (VN ~ several minutes)
docker compose logs -f valhalla    # watch build phases
```

Build phases you'll see: parse ways → build tiles (632 tiles) → enhance → reclassify ferries/links →
**shortcuts (hierarchy)** → complex turn restrictions → serving. Ready when `/status` returns 200.

## Verify

```bash
curl -s localhost:8002/status            # tileset_last_modified present
make route-test                          # >> OK: 5.4 km, 9 min
make matrix-test                         # >> matrix km: [[2.76,0.74],[3.28,1.51]]
```

Costing sanity (motorbike is differentiated):

```bash
for c in motor_scooter motorcycle auto bicycle; do
  curl -s localhost:8002/route -d '{"locations":[{"lat":10.7725,"lon":106.6980},
    {"lat":10.7951,"lon":106.7218}],"costing":"'$c'","units":"kilometers"}' \
  | python3 -c "import sys,json;s=json.load(sys.stdin)['trip']['summary'];print('$c',round(s['length'],1),'km')"
done
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `error 442 No path could be found` | A coordinate snapped to a restricted/one-way/disconnected edge (e.g. airport airside roads). Use a public arterial, or add `radius`/`search_filter` to the location. NOT a graph failure — test a central point to confirm. |
| `null` cells in the matrix | Same snapping issue, direction-specific (an arrival-only edge works as target but not source). |
| `/status` 404 / connection refused | Still building. Watch `docker compose logs -f valhalla`. |
| Rebuild from scratch | Set `force_rebuild=True` on the valhalla service (or delete `custom_files/valhalla_tiles*`) and `make graph`. |

## Disk note

The auxiliary tile sources from Phase 1 (`data/sources/`, ~2.4 GB) are only needed to rebuild PMTiles.
Safe to delete to reclaim space before Phase 3 (Photon/Elasticsearch is the hungriest service);
`make sources` re-fetches them when needed.
