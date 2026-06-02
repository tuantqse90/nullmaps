# Runbook — Day-2 ops

How the unattended box stays correct, and what to do when it doesn't.

## Scheduler (pick ONE)

- **systemd (recommended):** `sudo bash infra/install-systemd.sh` — installs + enables timers
  `nullmaps-monitor` (every 5 min), `nullmaps-refresh` (Sun 03:30 UTC), `nullmaps-backup` (Wed 03:30 UTC).
  Check: `systemctl list-timers 'nullmaps-*'`. Logs: `journalctl -u nullmaps-refresh`.
- **cron:** `bash infra/install-cron.sh` — installs the same schedule into the user crontab.
  The installers warn if the other mechanism is already active (avoid double-scheduling).

## What runs

- `infra/monitor.sh` — restarts any unhealthy container; alerts if `https://maps.nullshift.sh/style.json`
  ≠ 200. Alerts go to `NULLMAPS_ALERT_WEBHOOK` (Slack/Discord) if set.
- `infra/refresh.sh` — downloads a fresh VN OSM extract, rebuilds the geocoder index and Valhalla graph,
  optionally tiles (`REFRESH_TILES=1`), then backs up. **Health-gated with rollback:** a failed rebuild
  restores the previous `geocoder.db.bak` / `valhalla_tiles.tar.bak` and alerts.
- `infra/backup.sh` — uploads artifacts to R2 (`NULLMAPS_R2_DEST`), verifies each with `rclone check`,
  keeps a flat "latest" set + the 4 most recent weekly snapshots, alerts on any failure.
- `infra/restore-test.sh` (`make backup-test`) — pulls the latest `geocoder.db` + `vietnam.pmtiles` and
  runs `PRAGMA integrity_check` + a PMTiles magic-byte check. Run it periodically to trust the backups.

## Artifact locations

- Tiles: `data/vietnam.pmtiles` (+ `hillshade.mbtiles`, `contours.mbtiles`)
- Geocoder index: `services/geocoder/data/geocoder.db`
- Valhalla graph: `services/routing/custom_files/valhalla_tiles.tar` (+ unpacked `valhalla_tiles/`)
- Logs: `/var/log/nullmaps-{monitor,refresh,backup}.log`
- Remote backups: `NULLMAPS_R2_DEST` (default `r2:tasco-drive-pgbackrest/nullmaps/artifacts`, weeklies under `weekly/`)

## Manual recovery

- **Restore a fresh box:** `bash infra/restore.sh` then `docker compose -f docker-compose.yml up -d`.
- **Roll back a bad graph manually:** `mv services/routing/custom_files/valhalla_tiles.tar.bak
  services/routing/custom_files/valhalla_tiles.tar && docker compose restart valhalla`.
- **Rotate the API key:** edit `.env`, `docker compose -f docker-compose.yml up -d`, update all clients.
