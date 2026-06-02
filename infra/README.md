# infra — deploy config

NullMaps runs on a **single Hetzner box via Coolify**. `docker-compose.yml` at the repo root is the
source of truth. **Prod deploys the base file ALONE** — only the `gateway` (`:8088`) is published; the
engines are internal. The local `docker-compose.override.yml` re-exposes engine ports for `make *-test`
and must NOT be used in prod.

- **`gateway/Caddyfile`** — the single front door: gates `/maps/*` + `/v1/*` on `API_KEY`, fronts
  tiles/demo, keeps valhalla/geocoder/normalizer off the internet.
- **Full deploy steps:** [`../docs/runbook-deploy-coolify.md`](../docs/runbook-deploy-coolify.md).

## Deploy (Coolify)

1. Point a Coolify "Docker Compose" resource at this repo.
2. Set env from `.env.example` (at minimum `API_KEY`, ports). Coolify injects them as the compose env.
3. Persist volumes: `./data` (tiles), and later `valhalla_tiles` / `photon_data` / `pgdata`.
4. Build tiles once on the box (`make tiles`) or upload a prebuilt `data/vietnam.pmtiles`.

## Box sizing

See the root `README.md` "Box sizing" section — keep RAM/disk numbers current as Valhalla and Photon
(both RAM-hungry) come online.

## Env templates

Copy `../.env.example` → `.env`. Never commit `.env`.
