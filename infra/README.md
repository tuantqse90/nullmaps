# infra — deploy config

NullMaps runs on a **single Hetzner box via Coolify**. `docker-compose.yml` at the repo root is the
source of truth for both local and prod — Coolify deploys that same compose.

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
