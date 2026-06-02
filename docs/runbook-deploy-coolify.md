# Runbook — Deploy to Hetzner via Coolify

NullMaps is Docker-Compose-first. **Prod deploys the base `docker-compose.yml` alone** — only the
**gateway** (`:8088`) is published; every engine is internal. The local `docker-compose.override.yml`
(which re-publishes engine ports for `make *-test`) is **dev-only** and must NOT be used in prod.

## Box sizing (single Hetzner box)

| Component | RAM | Disk |
|---|---|---|
| Martin + tiles | ~0.5 GB | PMTiles ~0.3 GB |
| Valhalla graph | ~1–1.5 GB build / light serve | graph ~4–5 GB |
| Geocoder (SQLite) | ~tens of MB | index ~60 MB |
| Adapter + normalizer + gateway | ~0.3 GB | small |
| **Total comfortable** | **~4–6 GB** | **~15–20 GB** |

A CPX31/CPX41-class box is comfortable. (Photon, if ever swapped in for the geocoder, needs several GB
more RAM — size up then.)

## One-time data build

Build the heavy artifacts once on the box (or build locally and copy):

```bash
make fetch        # VN OSM extract
make tiles        # PMTiles (data/vietnam.pmtiles)
make graph        # Valhalla graph (services/routing/custom_files/)
make geo-index    # geocoder SQLite index
make fonts        # self-hosted glyphs
```

These outputs are gitignored — they live on the box's volumes, not in git.

## Coolify

1. New resource → **Docker Compose**, point at this repo, compose file `docker-compose.yml`
   (do **not** add the override).
2. **Env vars:** `API_KEY` (long random), `GATEWAY_PORT` (or let Coolify's proxy front it).
   Optional Phase 5: `LLM_MODEL` + `DASHSCOPE_API_KEY` to enable the normalizer.
3. **Persistent volumes / bind mounts:** `./data`, `./services/routing/custom_files`,
   `./services/geocoder/data`, `./services/tiles/fonts`. Pre-populate from the build step above.
4. **Domain:** map `maps.nullshift.sh` → the gateway. Coolify's Traefik/Caddy terminates TLS; the
   gateway listens on `:8088`. (Flat subdomain per the CF Universal SSL convention used elsewhere.)
5. Deploy. Verify:
   ```bash
   curl https://maps.nullshift.sh/style.json                 # 200
   curl "https://maps.nullshift.sh/maps/api/directions/json?...&key=$API_KEY"   # 200
   curl "https://maps.nullshift.sh/maps/api/directions/json?..."                # 403 (no key)
   ```

## Security posture

- Only the gateway is internet-facing. Valhalla / geocoder / normalizer have **no published ports**.
- The gateway refuses to proxy `/maps/*` and `/v1/*` without the shared key (header `X-API-Key` or
  `?key=`); the adapter enforces it again. Tiles/style/demo are read-only and ungated.
- Rotate `API_KEY` by changing the env and redeploying.

## Updating map data

Re-run the relevant build target and restart the service (or wire a weekly cron that rebuilds and
restarts). Tiles/graph/index rebuild independently.
