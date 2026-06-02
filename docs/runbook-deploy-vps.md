# Runbook — Deploy to a plain VPS (docker compose)

NullMaps is Docker-Compose-first. **Prod deploys the base `docker-compose.yml` alone** — only the
**gateway** (`:8088`) is published; every engine is internal. The local `docker-compose.override.yml`
(which re-publishes engine ports for `make *-test`) is **dev-only** and must NOT be used in prod.

Current prod: a shared **Hostinger VPS**, repo at **`/opt/nullmaps`**, native **Caddy** terminating TLS
and reverse-proxying `maps.nullshift.sh → localhost:8088`. (Coolify also works as an alternative — point a
"Docker Compose" resource at this repo with the base compose file; the steps below map 1:1.)

## Box sizing (single VPS)

| Component | RAM | Disk |
|---|---|---|
| Martin + tiles | ~0.5 GB | PMTiles ~0.3 GB |
| Valhalla graph | ~1–1.5 GB build / light serve | graph ~4–5 GB |
| Geocoder (SQLite) | ~tens of MB | index ~60 MB |
| Adapter + normalizer + gateway | ~0.3 GB | small |
| **Total comfortable** | **~4–6 GB** | **~15–20 GB** |

## One-time data build

Build the heavy artifacts once on the box (or build locally and copy), or restore from R2:

```bash
make fetch && make tiles && make graph && make geo-index && make fonts   # build
# or, on a fresh box with an existing backup:
bash infra/restore.sh                                                     # pull artifacts from R2
```

These outputs are gitignored — they live on the box's volumes, not in git.

## Deploy

1. `git clone` the repo to `/opt/nullmaps` (or `git pull` to update).
2. `cp .env.example .env`; set `API_KEY` (long random), optional `LLM_MODEL` + `DASHSCOPE_API_KEY`
   (normalizer), `NULLMAPS_ALERT_WEBHOOK` (ops alerts), `NULLMAPS_R2_DEST` (backup target).
3. `docker compose -f docker-compose.yml up -d` (base file only — engines internal, gateway on :8088).
4. Front it with native Caddy: `maps.nullshift.sh → localhost:8088` (Caddy terminates TLS).
5. Install the ops scheduler — see [`runbook-ops.md`](runbook-ops.md).
6. Verify:
   ```bash
   curl https://maps.nullshift.sh/style.json                                    # 200
   curl "https://maps.nullshift.sh/maps/api/directions/json?...&key=$API_KEY"   # 200
   curl "https://maps.nullshift.sh/maps/api/directions/json?..."                # 403 (no key)
   ```

## Security posture

- Only the gateway is internet-facing. Valhalla / geocoder / normalizer have **no published ports**.
- The gateway refuses `/maps/*` and `/v1/*` without the shared key (`X-API-Key` or `?key=`); the adapter
  enforces it again. Tiles/style/demo are read-only and ungated.
- Rotate `API_KEY` by changing `.env`, `docker compose up -d` to recreate, and updating every client.
