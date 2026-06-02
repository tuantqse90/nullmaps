# Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the unattended single-box NullMaps deploy stay correct on its own — resilient data refresh with rollback, verified scheduled backups, a committed scheduler, a bounded/hardened adapter, and docs that match the real plain-VPS deploy.

**Architecture:** Group A is shell ops (`infra/*.sh` + `infra/systemd/`), sharing one `infra/lib.sh` (log/alert/wait_healthy); health-gating polls `docker inspect` health so it needs no published engine port. Group B hardens the FastAPI adapter (`services/adapter/app/main.py`) with bounded `cachetools` rate-limit/metrics dicts and a Google-shaped error handler, via TDD. Group C truths-up docs to plain `docker compose` on the Hostinger VPS.

**Tech Stack:** Bash, Docker Compose, rclone (R2), systemd timers / cron, Python 3.12, FastAPI, cachetools, pytest, shellcheck.

**Spec:** `docs/superpowers/specs/2026-06-02-production-hardening-design.md`

**Branch:** `feat/production-hardening` (already created)

**Deploy target (confirmed):** plain `docker compose` on Hostinger VPS at `/opt/nullmaps`, native Caddy. Compose project = `nullmaps`. **B2 keeps the original HTTP status code** and adds a Google body.

---

## File Structure

- **Create** `infra/lib.sh` — shared `ts`/`log`/`alert`/`wait_healthy`; one source of truth for ops helpers.
- **Modify** `infra/monitor.sh` — source `lib.sh` (behavior identical).
- **Modify** `infra/refresh.sh` — project-agnostic restarts, geocoder + Valhalla rollback with health-gate, no silent failures.
- **Modify** `infra/backup.sh` — verify each upload, 4 weekly snapshots, alert on failure.
- **Create** `infra/restore-test.sh` — non-destructive “is the latest backup restorable?” check.
- **Create** `infra/systemd/nullmaps-{monitor,refresh,backup}.{service,timer}` — scheduler units.
- **Create** `infra/install-systemd.sh`, `infra/crontab.snippet`, `infra/install-cron.sh` — both install paths.
- **Modify** `Makefile` — add `backup-test` target.
- **Modify** `.github/workflows/ci.yml` — add a `shell` job (`bash -n` + `shellcheck`).
- **Modify** `services/adapter/app/main.py` — bounded `_rl`/`_by_key`, Google error handler.
- **Create** `services/adapter/tests/test_places_fleet.py` — places/fleet/error-shape/cardinality tests.
- **Modify** `services/geocoder/tests/test_fold.py` — `pytest.importorskip("osmium")`.
- **Rename+rewrite** `docs/runbook-deploy-coolify.md` → `docs/runbook-deploy-vps.md`; **modify** `infra/README.md`, `CLAUDE.md`, `README.md` links; **create** `docs/runbook-ops.md`.

---

## Task 1: Shared ops library `infra/lib.sh`

**Files:**
- Create: `infra/lib.sh`

- [ ] **Step 1: Create the library**

```bash
#!/usr/bin/env bash
# Shared helpers for NullMaps ops scripts (refresh, monitor, backup, restore-test).
# Source after cd-ing to the repo root:  . "$(dirname "$0")/lib.sh"
# Provides: ts, log, alert, wait_healthy. Honors $LOG and $NULLMAPS_ALERT_WEBHOOK.

WEBHOOK="${NULLMAPS_ALERT_WEBHOOK:-}"
COMPOSE="docker compose -f docker-compose.yml"

ts() { date -u +%FT%TZ; }

# log <msg> — timestamped line to stdout and, if $LOG is set, the logfile.
log() {
  local line; line="$(ts) $1"
  if [ -n "${LOG:-}" ]; then echo "$line" | tee -a "$LOG"; else echo "$line"; fi
}

# alert <msg> — log it and POST to the webhook if configured. Never fails the caller.
alert() {
  log "ALERT: $1"
  if [ -n "$WEBHOOK" ]; then
    curl -s -m 10 -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"NullMaps: $1\"}" "$WEBHOOK" >/dev/null 2>&1 || true
  fi
  return 0
}

# wait_healthy <service> <timeout_s> — poll the container's docker health until "healthy"
# (or just "running" for a service with no healthcheck). Returns 0 healthy, 1 on timeout.
wait_healthy() {
  local svc="$1" timeout="${2:-120}" waited=0 cid status running
  while [ "$waited" -lt "$timeout" ]; do
    cid="$($COMPOSE ps -q "$svc" 2>/dev/null)"
    if [ -n "$cid" ]; then
      status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)"
      running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)"
      case "$status" in
        healthy) return 0 ;;
        none) [ "$running" = "true" ] && return 0 ;;
      esac
    fi
    sleep 5; waited=$((waited + 5))
  done
  return 1
}
```

- [ ] **Step 2: Verify syntax + lint**

Run: `cd /Users/nullshift-labs/dev/nullmap && bash -n infra/lib.sh && shellcheck -e SC1091,SC2086 infra/lib.sh && echo OK`
Expected: `OK` (no shellcheck findings)

- [ ] **Step 3: Commit**

```bash
git add infra/lib.sh
git commit -m "feat(infra): shared ops lib (log/alert/wait_healthy)"
```

---

## Task 2: Refactor `infra/monitor.sh` onto `lib.sh`

**Files:**
- Modify: `infra/monitor.sh`

- [ ] **Step 1: Replace the file**

```bash
#!/usr/bin/env bash
# Self-heal + uptime probe for NullMaps. Runs every few minutes (systemd timer / cron).
#  - restarts any NullMaps container marked unhealthy (Docker healthchecks only flag)
#  - alerts if the public URL is not 200
# Optional alerting: set NULLMAPS_ALERT_WEBHOOK to POST failures to Slack/Discord.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=/var/log/nullmaps-monitor.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"

# 1) self-heal unhealthy containers
for c in $(docker ps --filter name=nullmaps --filter health=unhealthy -q); do
  name=$(docker inspect -f '{{.Name}}' "$c" | sed 's#^/##')
  docker restart "$c" >/dev/null 2>&1 && alert "restarted unhealthy $name"
done

# 2) public reachability
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://maps.nullshift.sh/style.json)
[ "$code" != "200" ] && alert "PUBLIC DOWN style.json http=$code"
exit 0
```

- [ ] **Step 2: Verify**

Run: `cd /Users/nullshift-labs/dev/nullmap && bash -n infra/monitor.sh && shellcheck -e SC1091,SC2086 infra/monitor.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add infra/monitor.sh
git commit -m "refactor(infra): monitor.sh uses shared lib"
```

---

## Task 3: Harden `infra/refresh.sh` (rollback + health-gate + alerts)

**Files:**
- Modify: `infra/refresh.sh`

- [ ] **Step 1: Replace the file**

```bash
#!/usr/bin/env bash
# Refresh NullMaps map data from a fresh OSM extract, then back up + restart.
# Resource-aware: single-instance lock, nice/ionice, runs off-peak (systemd timer / cron).
# Rebuilds geocoder index + Valhalla graph by default; REFRESH_TILES=1 also rebuilds PMTiles.
# Health-gated with rollback: a failed index/graph rebuild restores the previous artifact.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/refresh.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG=/var/log/nullmaps-refresh.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"
exec 9>/tmp/nullmaps-refresh.lock; flock -n 9 || { echo "refresh already running"; exit 0; }
N="nice -n 15 ionice -c3"
URL="${OSM_EXTRACT_URL:-https://download.geofabrik.de/asia/vietnam-latest.osm.pbf}"
ERRORS=0

log "refresh START"
mkdir -p data/raw

# --- fresh OSM extract (atomic swap) ---
if $N curl -fSL -o data/raw/vietnam-latest.osm.pbf.new "$URL"; then
  mv data/raw/vietnam-latest.osm.pbf.new data/raw/vietnam-latest.osm.pbf
  log "pbf updated"
else
  alert "pbf download failed — keeping previous extract, aborting refresh"
  exit 1
fi

# --- geocoder index (rebuild on container fs, swap the file, health-gate + rollback) ---
GEO_DB="services/geocoder/data/geocoder.db"
[ -f "$GEO_DB" ] && cp -f "$GEO_DB" "$GEO_DB.bak"
if $N docker run --rm -v "$ROOT/data/raw:/raw:ro" -v "$ROOT/services/geocoder/data:/data" \
     nullmaps-geocoder sh -c "mkdir -p /build && python importer.py /raw/vietnam-latest.osm.pbf /build/geocoder.db && mv /build/geocoder.db /data/geocoder.db"; then
  $COMPOSE restart geocoder >/dev/null
  if wait_healthy geocoder 90; then
    log "geocoder index rebuilt"
  else
    alert "geocoder unhealthy after rebuild — rolling back"
    [ -f "$GEO_DB.bak" ] && mv -f "$GEO_DB.bak" "$GEO_DB" && $COMPOSE restart geocoder >/dev/null
    ERRORS=$((ERRORS + 1))
  fi
else
  alert "geocoder index build failed — keeping previous index"
  ERRORS=$((ERRORS + 1))
fi

# --- Valhalla graph (force rebuild from the new pbf, health-gate + rollback) ---
ln -f data/raw/vietnam-latest.osm.pbf services/routing/custom_files/vietnam-latest.osm.pbf 2>/dev/null || true
TAR="services/routing/custom_files/valhalla_tiles.tar"
[ -f "$TAR" ] && mv -f "$TAR" "$TAR.bak"
rm -rf services/routing/custom_files/valhalla_tiles
if $N $COMPOSE up -d --force-recreate valhalla >/dev/null && wait_healthy valhalla 600; then
  log "valhalla graph rebuilt"
else
  alert "valhalla rebuild failed — rolling back to previous graph"
  if [ -f "$TAR.bak" ]; then
    mv -f "$TAR.bak" "$TAR"
    rm -rf services/routing/custom_files/valhalla_tiles
    $N $COMPOSE up -d --force-recreate valhalla >/dev/null
    wait_healthy valhalla 600 || alert "valhalla still unhealthy after rollback — manual attention needed"
  fi
  ERRORS=$((ERRORS + 1))
fi

# --- tiles (optional, heavy) ---
if [ "${REFRESH_TILES:-0}" = "1" ]; then
  if $N docker run --rm -v "$ROOT/data:/data" ghcr.io/onthegomap/planetiler:latest \
       --osm-path=/data/raw/vietnam-latest.osm.pbf --download --output=/data/vietnam.pmtiles --force; then
    $COMPOSE restart martin >/dev/null && wait_healthy martin 60 && log "tiles rebuilt"
  else
    alert "tiles rebuild failed — keeping previous PMTiles"
    ERRORS=$((ERRORS + 1))
  fi
fi

# --- back up the fresh artifacts ---
if bash infra/backup.sh >>"$LOG" 2>&1; then
  log "backed up to R2"
else
  alert "backup failed after refresh"
  ERRORS=$((ERRORS + 1))
fi

log "refresh DONE (errors: $ERRORS)"
[ "$ERRORS" -eq 0 ] || exit 1
```

- [ ] **Step 2: Verify**

Run: `cd /Users/nullshift-labs/dev/nullmap && bash -n infra/refresh.sh && shellcheck -e SC1091,SC2086 infra/refresh.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add infra/refresh.sh
git commit -m "feat(infra): refresh.sh rollback + health-gate + project-agnostic restarts"
```

---

## Task 4: Robust `infra/backup.sh` (verify + retention + alert)

**Files:**
- Modify: `infra/backup.sh`

- [ ] **Step 1: Replace the file**

```bash
#!/usr/bin/env bash
# Back up NullMaps built artifacts (tiles + geocoder index + Valhalla graph) to R2.
# Verifies each upload, keeps a flat "latest" set + 4 weekly snapshots, alerts on failure.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/backup.sh
# R2 remote `r2:` configured in rclone; bucket-scoped token => --s3-no-check-bucket.
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=/var/log/nullmaps-backup.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"

DEST="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
WEEKLY="$DEST/weekly/$(date -u +%G-W%V)"
F="--s3-no-check-bucket"

ARTIFACTS=(
  "data/vietnam.pmtiles"
  "data/hillshade.mbtiles"
  "data/contours.mbtiles"
  "services/geocoder/data/geocoder.db"
  "services/routing/custom_files/valhalla_tiles.tar"
  "services/routing/custom_files/valhalla.json"
)

# put <localfile> <remote_dir> — copy then checksum-verify; alert+fail on mismatch.
put() {
  local src="$1" dstdir="$2" base
  base="$(basename "$src")"
  [ -s "$src" ] || { log "skip (missing/empty): $src"; return 0; }
  rclone copy $F "$src" "$dstdir/" || { alert "backup copy failed: $src"; return 1; }
  rclone check $F --one-way --include "$base" "$(dirname "$src")" "$dstdir" >/dev/null 2>&1 \
    || { alert "backup verify failed: $src"; return 1; }
  return 0
}

log "backup START -> $DEST"
rc=0
for a in "${ARTIFACTS[@]}"; do
  put "$a" "$DEST"   || rc=1   # flat "latest" (restore.sh reads this)
  put "$a" "$WEEKLY" || rc=1   # retained weekly snapshot
done
rclone sync $F services/routing/custom_files/admin_data "$DEST/admin_data/" \
  || { alert "backup admin_data sync failed"; rc=1; }

# retention: keep only the 4 most recent weekly snapshots
mapfile -t weeks < <(rclone lsf $F "$DEST/weekly/" 2>/dev/null | sed 's#/##' | sort)
if [ "${#weeks[@]}" -gt 4 ]; then
  for old in "${weeks[@]:0:${#weeks[@]}-4}"; do
    rclone purge $F "$DEST/weekly/$old" >/dev/null 2>&1 && log "pruned old weekly: $old"
  done
fi

if [ "$rc" -eq 0 ]; then log "backup DONE"; else alert "backup completed with errors"; fi
exit "$rc"
```

- [ ] **Step 2: Verify**

Run: `cd /Users/nullshift-labs/dev/nullmap && bash -n infra/backup.sh && shellcheck -e SC1091,SC2086 infra/backup.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add infra/backup.sh
git commit -m "feat(infra): backup verify + weekly retention + failure alerts"
```

---

## Task 5: `infra/restore-test.sh` + `make backup-test`

**Files:**
- Create: `infra/restore-test.sh`
- Modify: `Makefile`

- [ ] **Step 1: Create restore-test.sh**

```bash
#!/usr/bin/env bash
# Verify the latest R2 backup is restorable: pull geocoder.db + vietnam.pmtiles to a temp
# dir, run sqlite integrity_check and a PMTiles magic-byte check. Non-destructive.
#
# Usage (on the VPS):  bash /opt/nullmaps/infra/restore-test.sh
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=/var/log/nullmaps-backup.log
set -a; [ -f .env ] && . ./.env; set +a
# shellcheck source=infra/lib.sh
. "$(dirname "$0")/lib.sh"

SRC="${NULLMAPS_R2_DEST:-r2:tasco-drive-pgbackrest/nullmaps/artifacts}"
F="--s3-no-check-bucket"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rc=0

log "restore-test START <- $SRC"
rclone copy $F "$SRC/geocoder.db"     "$TMP/" || { alert "restore-test: cannot pull geocoder.db"; exit 1; }
rclone copy $F "$SRC/vietnam.pmtiles" "$TMP/" || { alert "restore-test: cannot pull vietnam.pmtiles"; exit 1; }

# 1) sqlite integrity (use the host sqlite3 if present, else the geocoder image)
if command -v sqlite3 >/dev/null 2>&1; then
  res="$(sqlite3 "$TMP/geocoder.db" 'PRAGMA integrity_check;' 2>&1 | head -1)"
else
  res="$(docker run --rm -v "$TMP:/t" nullmaps-geocoder \
    python -c "import sqlite3; print(sqlite3.connect('/t/geocoder.db').execute('PRAGMA integrity_check').fetchone()[0])" 2>&1 | tail -1)"
fi
if [ "$res" = "ok" ]; then log "geocoder.db integrity: ok"; else alert "restore-test: geocoder.db integrity=$res"; rc=1; fi

# 2) PMTiles magic bytes (file starts with the 7-byte ASCII "PMTiles")
magic="$(head -c 7 "$TMP/vietnam.pmtiles" 2>/dev/null)"
if [ "$magic" = "PMTiles" ]; then log "vietnam.pmtiles magic: ok"; else alert "restore-test: pmtiles magic='$magic'"; rc=1; fi

if [ "$rc" -eq 0 ]; then log "restore-test PASS"; else log "restore-test FAIL"; fi
exit "$rc"
```

- [ ] **Step 2: Add the Makefile target**

Append to `Makefile` (after the `matrix-test` target near the end):

```makefile
.PHONY: backup-test
backup-test: ## (ops) Verify the latest R2 backup is restorable (sqlite + pmtiles checks)
	bash infra/restore-test.sh
```

- [ ] **Step 3: Verify**

Run: `cd /Users/nullshift-labs/dev/nullmap && bash -n infra/restore-test.sh && shellcheck -e SC1091,SC2086 infra/restore-test.sh && grep -q backup-test Makefile && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add infra/restore-test.sh Makefile
git commit -m "feat(infra): restore-test + make backup-test"
```

---

## Task 6: Scheduler — systemd units + cron (both)

**Files:**
- Create: `infra/systemd/nullmaps-monitor.service`, `infra/systemd/nullmaps-monitor.timer`
- Create: `infra/systemd/nullmaps-refresh.service`, `infra/systemd/nullmaps-refresh.timer`
- Create: `infra/systemd/nullmaps-backup.service`, `infra/systemd/nullmaps-backup.timer`
- Create: `infra/install-systemd.sh`, `infra/crontab.snippet`, `infra/install-cron.sh`

- [ ] **Step 1: Create the three `.service` units**

`infra/systemd/nullmaps-monitor.service`:
```ini
[Unit]
Description=NullMaps self-heal + uptime probe
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/nullmaps
EnvironmentFile=-/opt/nullmaps/.env
ExecStart=/usr/bin/env bash /opt/nullmaps/infra/monitor.sh
```

`infra/systemd/nullmaps-refresh.service`:
```ini
[Unit]
Description=NullMaps weekly data refresh (OSM extract -> index/graph rebuild + backup)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/nullmaps
EnvironmentFile=-/opt/nullmaps/.env
ExecStart=/usr/bin/env bash /opt/nullmaps/infra/refresh.sh
```

`infra/systemd/nullmaps-backup.service`:
```ini
[Unit]
Description=NullMaps artifact backup to R2
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/nullmaps
EnvironmentFile=-/opt/nullmaps/.env
ExecStart=/usr/bin/env bash /opt/nullmaps/infra/backup.sh
```

- [ ] **Step 2: Create the three `.timer` units**

`infra/systemd/nullmaps-monitor.timer`:
```ini
[Unit]
Description=Run NullMaps monitor every 5 minutes

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
```

`infra/systemd/nullmaps-refresh.timer`:
```ini
[Unit]
Description=Run NullMaps data refresh weekly (Sun 03:30 UTC)

[Timer]
OnCalendar=Sun *-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

`infra/systemd/nullmaps-backup.timer`:
```ini
[Unit]
Description=Run NullMaps backup mid-week (Wed 03:30 UTC)

[Timer]
OnCalendar=Wed *-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create `infra/install-systemd.sh`**

```bash
#!/usr/bin/env bash
# Install + enable the NullMaps systemd timers (run as root on the VPS).
set -euo pipefail
SRC="$(cd "$(dirname "$0")/systemd" && pwd)"
DST=/etc/systemd/system
if crontab -l 2>/dev/null | grep -q '# nullmaps'; then
  echo "WARNING: a nullmaps cron block exists (infra/install-cron.sh) — you may be double-scheduling. Remove it first." >&2
fi
for u in nullmaps-monitor nullmaps-refresh nullmaps-backup; do
  install -m 0644 "$SRC/$u.service" "$DST/$u.service"
  install -m 0644 "$SRC/$u.timer"   "$DST/$u.timer"
done
systemctl daemon-reload
for u in nullmaps-monitor nullmaps-refresh nullmaps-backup; do
  systemctl enable --now "$u.timer"
done
systemctl list-timers 'nullmaps-*' --no-pager || true
echo "Installed. Adjust OnCalendar in $DST/nullmaps-*.timer then: systemctl daemon-reload"
```

- [ ] **Step 4: Create `infra/crontab.snippet`**

```cron
# nullmaps — NullMaps ops schedule (managed by infra/install-cron.sh; do not edit between markers)
*/5 * * * * cd /opt/nullmaps && bash infra/monitor.sh >> /var/log/nullmaps-monitor.log 2>&1
30 3 * * 0 cd /opt/nullmaps && bash infra/refresh.sh >> /var/log/nullmaps-refresh.log 2>&1
30 3 * * 3 cd /opt/nullmaps && bash infra/backup.sh  >> /var/log/nullmaps-backup.log 2>&1
# end nullmaps
```

- [ ] **Step 5: Create `infra/install-cron.sh`**

```bash
#!/usr/bin/env bash
# Install the NullMaps cron schedule into the current user's crontab (idempotent).
set -euo pipefail
SNIP="$(cd "$(dirname "$0")" && pwd)/crontab.snippet"
if systemctl list-timers 'nullmaps-*' 2>/dev/null | grep -q nullmaps; then
  echo "WARNING: nullmaps systemd timers are active — you may be double-scheduling. Remove them first." >&2
fi
tmp="$(mktemp)"
crontab -l 2>/dev/null | sed '/# nullmaps/,/# end nullmaps/d' > "$tmp"   # drop any prior block
cat "$SNIP" >> "$tmp"
crontab "$tmp"
rm -f "$tmp"
echo "Installed nullmaps cron block:"; crontab -l | sed -n '/# nullmaps/,/# end nullmaps/p'
```

- [ ] **Step 6: Verify**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
bash -n infra/install-systemd.sh infra/install-cron.sh
shellcheck -e SC1091,SC2086 infra/install-systemd.sh infra/install-cron.sh
# systemd unit files must contain the right sections
grep -q 'OnCalendar=' infra/systemd/nullmaps-monitor.timer && grep -q 'Type=oneshot' infra/systemd/nullmaps-refresh.service && echo OK
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add infra/systemd infra/install-systemd.sh infra/crontab.snippet infra/install-cron.sh
git commit -m "feat(infra): scheduler — systemd timers + cron snippet (both installable)"
```

---

## Task 7: CI — shellcheck + bash -n for infra scripts

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a `shell` job**

In `.github/workflows/ci.yml`, after the `tests:` job block (end of file), add:

```yaml
  shell:
    name: shellcheck + bash -n (infra)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Syntax check
        run: for f in infra/*.sh; do bash -n "$f"; done
      - name: ShellCheck
        run: shellcheck -e SC1091,SC2086 infra/*.sh
```

- [ ] **Step 2: Verify the workflow parses + lint passes locally**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
for f in infra/*.sh; do bash -n "$f"; done && shellcheck -e SC1091,SC2086 infra/*.sh && echo "lint ok"
```
Expected: `yaml ok` then `lint ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: shellcheck + bash -n for infra scripts"
```

---

## Task 8: Bound rate-limit / metrics memory (adapter)

**Files:**
- Modify: `services/adapter/app/main.py` (imports + `_by_key`/`_rl` definitions ~lines 27, 133-135; middleware ~lines 138-158)
- Test: `services/adapter/tests/test_places_fleet.py` (new)

- [ ] **Step 1: Write the failing test**

Create `services/adapter/tests/test_places_fleet.py`:

```python
"""Adapter hardening: bounded rate-limit/metrics dicts, Google-shaped errors,
and the places/fleet endpoints (engines mocked)."""
import importlib
import os

from fastapi.testclient import TestClient


def load(api_key="secret", rate_per_min=None):
    os.environ["API_KEY"] = api_key
    if rate_per_min is not None:
        os.environ["RATE_LIMIT_PER_MIN"] = str(rate_per_min)
    else:
        os.environ.pop("RATE_LIMIT_PER_MIN", None)
    import app.main as m
    importlib.reload(m)
    return m


def test_rate_limit_dicts_are_bounded():
    m = load()
    c = TestClient(m.app)
    for i in range(1100):  # > maxsize (1024) distinct keys
        c.get("/maps/api/geocode/json", params={"address": "x", "key": f"k{i}"})
    assert len(m._by_key) <= 1024
    assert len(m._rl) <= 1024
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_places_fleet.py::test_rate_limit_dicts_are_bounded -v`
Expected: FAIL — `len(m._by_key)` is 1100 (unbounded defaultdict)

- [ ] **Step 3: Bound the dicts**

In `services/adapter/app/main.py`, change the cachetools import line:

```python
from cachetools import TTLCache
```

to:

```python
from cachetools import TTLCache, LRUCache
```

Replace the metrics/rate-limit dict definitions:

```python
_counts: dict = defaultdict(int)          # (endpoint, status) -> total
_by_key: dict = defaultdict(int)          # key -> total requests
_rl: dict = defaultdict(lambda: [0, 0])   # key -> [minute_window, count]
```

with:

```python
_counts: dict = defaultdict(int)              # (endpoint, status) -> total (finite key space)
_by_key: LRUCache = LRUCache(maxsize=1024)    # key -> total requests (bounded)
_rl: TTLCache = TTLCache(maxsize=1024, ttl=120)  # key -> [minute_window, count] (bounded)
```

Then update the middleware body. Replace:

```python
        key = request.query_params.get("key") or request.headers.get("x-api-key") or "anon"
        minute = int(time.time() // 60)
        st = _rl[key]
        if st[0] != minute:
            st[0], st[1] = minute, 0
        st[1] += 1
        _by_key[key] += 1
```

with:

```python
        key = request.query_params.get("key") or request.headers.get("x-api-key") or "anon"
        minute = int(time.time() // 60)
        st = _rl.get(key)
        if st is None or st[0] != minute:
            st = [minute, 0]
        st[1] += 1
        _rl[key] = st
        _by_key[key] = _by_key.get(key, 0) + 1
```

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass (including the new bounded-dicts test)

- [ ] **Step 5: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_places_fleet.py
git commit -m "fix(adapter): bound rate-limit/metrics dicts (cachetools LRU/TTL)"
```

---

## Task 9: Google-shaped error bodies (adapter)

**Files:**
- Modify: `services/adapter/app/main.py` (import + handler near app creation ~line 63)
- Test: `services/adapter/tests/test_places_fleet.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/adapter/tests/test_places_fleet.py`:

```python
def test_missing_key_returns_google_request_denied():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "x"})  # no key
    assert r.status_code == 403
    b = r.json()
    assert b["status"] == "REQUEST_DENIED"
    assert "error_message" in b


def test_bad_latlng_returns_google_invalid_request():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "not-a-coord", "key": "secret"})
    assert r.status_code == 400
    assert r.json()["status"] == "INVALID_REQUEST"


def test_metrics_keeps_default_error_shape():
    m = load()
    c = TestClient(m.app)
    r = c.get("/metrics")  # not under /maps or /v1 -> default {detail}
    assert r.status_code == 403
    assert "detail" in r.json()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_places_fleet.py -k "google or invalid or default_error" -v`
Expected: FAIL — the 403/400 bodies are `{"detail": ...}`, not `{"status": ...}`

- [ ] **Step 3: Add the exception handler**

In `services/adapter/app/main.py`, add to the imports (near `from fastapi import ...`):

```python
from starlette.exceptions import HTTPException as StarletteHTTPException
```

Immediately after the `app = FastAPI(...)` block ends (after the closing `)` ~line 63), add:

```python
# Google clients branch on a `status` field. Render HTTPException on the customer
# surface (/maps, /v1) as a Google-shaped body while preserving the HTTP status code.
_GOOGLE_STATUS = {400: "INVALID_REQUEST", 403: "REQUEST_DENIED", 404: "NOT_FOUND",
                  429: "OVER_QUERY_LIMIT", 502: "UNKNOWN_ERROR"}


@app.exception_handler(StarletteHTTPException)
async def google_shaped_error(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    if path.startswith("/maps") or path.startswith("/v1"):
        status = _GOOGLE_STATUS.get(exc.status_code, "UNKNOWN_ERROR")
        return JSONResponse({"status": status, "error_message": exc.detail},
                            status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
```

- [ ] **Step 4: Run to verify they pass + full suite (no regressions)**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_places_fleet.py
git commit -m "feat(adapter): Google-shaped error bodies on /maps + /v1 (keep HTTP code)"
```

---

## Task 10: Cover the fragile adapter paths (places + fleet + 429)

**Files:**
- Test: `services/adapter/tests/test_places_fleet.py`

- [ ] **Step 1: Write the tests (engines mocked)**

Append to `services/adapter/tests/test_places_fleet.py`:

```python
# --- canned engine responses ---------------------------------------------------
async def fake_reverse(path, params):
    return {"result": {"osm_id": "n9", "name": "Chợ Bến Thành", "kind": "poi",
                       "lat": 10.7725, "lon": 106.6980, "district": "Quận 1", "city": "HCMC"}}


async def fake_results(path, params):
    return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                        "lat": 10.7704, "lon": 106.6951, "extra": "HCMC",
                        "category": "marketplace", "distance_m": 120}]}


async def fake_detail(path, params):
    return {"result": {"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                       "lat": 10.7704, "lon": 106.6951}}


async def fake_isochrone(path, payload):
    return {"type": "FeatureCollection", "features": [{"type": "Feature"}]}


async def fake_trace(path, payload):
    from app.polyline import encode
    shape6 = encode([(10.77, 106.69), (10.79, 106.72)], precision=6)
    return {"trip": {"status": 0, "summary": {"length": 3.1, "time": 240},
                    "legs": [{"shape": shape6}]}}


def test_reverse_geocode_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_reverse)
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "10.7725,106.6980", "key": "secret"})
    assert r.status_code == 200
    g = r.json()["results"][0]
    assert g["geometry"]["location"] == {"lat": 10.7725, "lng": 106.6980}


def test_autocomplete_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/autocomplete/json", params={"input": "ben thanh", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["predictions"][0]["structured_formatting"]["main_text"] == "Bến Thành"


def test_nearbysearch_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/nearbysearch/json",
              params={"location": "10.77,106.69", "radius": "500", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["results"][0]["distance_m"] == 120


def test_place_details_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_detail)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/details/json", params={"place_id": "n1", "key": "secret"})
    assert r.json()["result"]["name"] == "Bến Thành"


def test_isochrone_passthrough(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_isochrone)
    c = TestClient(m.app)
    r = c.get("/v1/isochrone", params={"location": "10.77,106.69", "contours": "10", "key": "secret"})
    assert r.json()["type"] == "FeatureCollection"


def test_snap_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_trace)
    c = TestClient(m.app)
    r = c.get("/v1/snap", params={"path": "10.77,106.69|10.79,106.72", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["distance"]["value"] == 3100
    assert b["snapped_polyline"]["points"]


def test_rate_limit_429(monkeypatch):
    m = load(rate_per_min=1)
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    first = c.get("/maps/api/geocode/json", params={"address": "a", "key": "secret"})
    second = c.get("/maps/api/geocode/json", params={"address": "b", "key": "secret"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["status"] == "OVER_QUERY_LIMIT"
```

- [ ] **Step 2: Run the new tests**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_places_fleet.py -q`
Expected: all pass

- [ ] **Step 3: Run the full adapter suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add services/adapter/tests/test_places_fleet.py
git commit -m "test(adapter): cover reverse/autocomplete/nearby/details/isochrone/snap/429"
```

---

## Task 11: Make the geocoder suite green without osmium

**Files:**
- Modify: `services/geocoder/tests/test_fold.py`

- [ ] **Step 1: Guard the osmium-dependent test**

In `services/geocoder/tests/test_fold.py`, find the test that imports `importer` (it begins `def test_importer_and_service_fold_agree():`). Make its first line skip when pyosmium is absent. Change:

```python
def test_importer_and_service_fold_agree():
    from importer import fold as ifold
```

to:

```python
def test_importer_and_service_fold_agree():
    pytest.importorskip("osmium")  # importer.py imports osmium; only present in the geocoder image / CI
    from importer import fold as ifold
```

(`pytest` is already imported at the top of `test_fold.py`.)

- [ ] **Step 2: Verify the suite is green locally**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/geocoder && python3 -m pytest -q`
Expected: passes with 1 skipped (the osmium test) where pyosmium isn't installed; all pass where it is.

- [ ] **Step 3: Commit**

```bash
git add services/geocoder/tests/test_fold.py
git commit -m "test(geocoder): skip osmium-dependent test when pyosmium is absent"
```

---

## Task 12: Docs truth-up (Coolify → plain VPS) + ops runbook

**Files:**
- Rename: `docs/runbook-deploy-coolify.md` → `docs/runbook-deploy-vps.md` (and rewrite)
- Modify: `infra/README.md`, `CLAUDE.md`, `README.md`
- Create: `docs/runbook-ops.md`

- [ ] **Step 1: Rename + rewrite the deploy runbook**

```bash
cd /Users/nullshift-labs/dev/nullmap
git mv docs/runbook-deploy-coolify.md docs/runbook-deploy-vps.md
```

Replace the contents of `docs/runbook-deploy-vps.md` with:

```markdown
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
```

- [ ] **Step 2: Create `docs/runbook-ops.md`**

```markdown
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
```

- [ ] **Step 3: Fix `infra/README.md`**

Replace the contents of `infra/README.md` with:

```markdown
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
```

- [ ] **Step 4: Fix the `CLAUDE.md` deploy line**

In `CLAUDE.md`, replace:

```
- **Self-hosted, Docker-first.** Deployed on **Hetzner via Coolify**. `docker-compose.yml` is the
  source of truth for local + prod.
```

with:

```
- **Self-hosted, Docker-first.** Prod runs **plain `docker compose` on a VPS** (currently Hostinger,
  `/opt/nullmaps`, behind native Caddy); Coolify works as an alternative. `docker-compose.yml` is the
  source of truth for local + prod.
```

- [ ] **Step 5: Fix dangling links to the renamed runbook**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
grep -rln "runbook-deploy-coolify" README.md docs infra CLAUDE.md
```
For each hit, replace `runbook-deploy-coolify.md` with `runbook-deploy-vps.md` (and any "Coolify"-specific link text with "VPS"). The known hits are `README.md` (the deploy section link) and any remaining doc cross-links.

- [ ] **Step 6: Verify no stale references remain**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap
grep -rn -i "runbook-deploy-coolify\|Hetzner via Coolify" README.md docs infra CLAUDE.md || echo "OK: no stale refs"
test -f docs/runbook-deploy-vps.md && test -f docs/runbook-ops.md && echo "OK: runbooks present"
```
Expected: `OK: no stale refs` then `OK: runbooks present`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs: truth-up deploy to plain VPS + add day-2 ops runbook"
```

---

## Task 13: Final verification

- [ ] **Step 1: Adapter + geocoder + normalizer tests**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q
cd /Users/nullshift-labs/dev/nullmap/services/geocoder && python3 -m pytest -q
cd /Users/nullshift-labs/dev/nullmap/services/normalizer && python3 -m pytest -q
```
Expected: adapter all pass; geocoder passes (osmium test skipped locally); normalizer all pass.

- [ ] **Step 2: All infra scripts lint clean**

Run: `cd /Users/nullshift-labs/dev/nullmap && for f in infra/*.sh; do bash -n "$f"; done && shellcheck -e SC1091,SC2086 infra/*.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Compose still validates (CI parity)**

Run: `cd /Users/nullshift-labs/dev/nullmap && docker compose -f docker-compose.yml config -q && echo OK`
Expected: `OK`

- [ ] **Step 4: Branch log**

Run: `git log --oneline main..HEAD`
Expected: the spec commit + Tasks 1-12 commits.

---

## Self-Review (completed by plan author)

- **Spec coverage:** A0 lib.sh (T1) ✓ · A1 refresh rollback/health-gate/project-agnostic (T3) ✓ · A2 backup verify+retention+alert (T4) ✓ · restore-test (T5) ✓ · A3 scheduler both systemd+cron (T6) ✓ · B1 bounded dicts (T8) ✓ · B2 Google errors keep-HTTP-code (T9) ✓ · B3 expand tests (T10) + osmium skip (T11) ✓ · CI shellcheck/bash -n (T7) ✓ · C docs truth-up + ops runbook (T12) ✓. monitor refactor (T2) supports A0. All spec success criteria mapped.
- **Placeholder scan:** none — every file is given in full or via exact find/replace anchors; the one search-driven step (T12 Step 5 link fixes) names the known hits and the exact substitution.
- **Type/name consistency:** `lib.sh` exports `ts`/`log`/`alert`/`wait_healthy`/`$COMPOSE`, used identically in monitor/refresh/backup/restore-test. `_by_key` (LRUCache) and `_rl` (TTLCache) defined in T8 are the same names asserted bounded in T8's test and used by the T9 handler context. `google_shaped_error` + `_GOOGLE_STATUS` defined once (T9). The new test loader `load(api_key, rate_per_min)` is defined once in T8 and reused by T9/T10. systemd unit names `nullmaps-{monitor,refresh,backup}` are consistent across units, installer, and the ops runbook.
