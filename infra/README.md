# infra — deploy + ops

NullMaps runs on a **single VPS via plain `docker compose`** (current prod: Hostinger, `/opt/nullmaps`,
native Caddy). `docker-compose.yml` at the repo root is the source of truth. **Prod deploys the base file
ALONE** — only the `gateway` (`:8088`) is published; the engines are internal. The local
`docker-compose.override.yml` re-exposes engine ports for `make *-test` and must NOT be used in prod.
(Coolify works as an alternative — see the deploy runbook.)

- **`gateway/Caddyfile`** — the single front door: gates `/maps/*` + `/v1/*` on `API_KEY`, fronts
  tiles/demo, keeps valhalla/geocoder/normalizer off the internet.
- **`lib.sh`** — shared ops helpers (log/alert/wait_healthy) sourced by the scripts below.
- **`refresh.sh`** — weekly data refresh (OSM extract → index/graph rebuild), health-gated with rollback.
- **`backup.sh` / `restore.sh` / `restore-test.sh`** — R2 artifact backup (verified, 4 weekly snapshots),
  restore, and a non-destructive restore check (`make backup-test`).
- **`monitor.sh`** — self-heal unhealthy containers + public-URL probe.
- **`systemd/` + `install-systemd.sh`**, **`crontab.snippet` + `install-cron.sh`** — the scheduler, both
  ways (pick one). See [`../docs/runbook-ops.md`](../docs/runbook-ops.md).

- **Full deploy steps:** [`../docs/runbook-deploy-vps.md`](../docs/runbook-deploy-vps.md).

## Env templates

Copy `../.env.example` → `.env`. Never commit `.env`. Relevant ops vars: `NULLMAPS_ALERT_WEBHOOK`,
`NULLMAPS_R2_DEST`, `OSM_EXTRACT_URL`, `REFRESH_TILES`.
