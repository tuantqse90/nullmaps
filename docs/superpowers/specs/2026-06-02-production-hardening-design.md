# Production Hardening — Design Spec

**Date:** 2026-06-02
**Sub-project:** ② of the NullMaps upgrade program (① Quick wins ✅ → **② Hardening** → ③ Accuracy → ④ Visual)
**Branch:** `feat/production-hardening`
**Status:** Approved design → ready for implementation plan

## Context

Goal: make the **unattended single-box deploy stay correct on its own**. Confirmed deploy target (operator):
**plain `docker compose` on a shared Hostinger VPS at `/opt/nullmaps`, fronted by native Caddy**
(`maps.nullshift.sh → localhost:8088`). NOT Coolify — the CLAUDE.md / runbook references to Coolify are
stale and get truthed-up here. Compose project name = the directory name `nullmaps`, so containers are
`nullmaps-<svc>-1`; we still switch to project-agnostic `docker compose` subcommands for robustness.

**Confirmed from code (read, not assumed):**

- `infra/refresh.sh:31-33` deletes `valhalla_tiles.tar` + the unpacked dir then force-recreates Valhalla
  with **no rollback and no health gate** — a failed graph build leaves routing dead with no fallback.
- `infra/refresh.sh:27,39` use hardcoded `docker restart nullmaps-geocoder-1` / `nullmaps-martin-1`
  (fragile: breaks if the dir is renamed or `COMPOSE_PROJECT_NAME` is set).
- `infra/refresh.sh:8` is `set -uo pipefail` (no `-e`): a mid-script failure is silent — the `&&` chains
  gate individual steps but nothing alerts on partial failure.
- `infra/backup.sh` does `rclone copy` to a single flat `artifacts/` path with **no verification, no
  retention/versioning** (a corrupt artifact overwrites the good one), **no restore-test**, and **no
  failure alert**.
- `infra/monitor.sh` is solid: self-heals unhealthy containers via `docker ps --filter name=nullmaps`,
  probes the public URL, optional `NULLMAPS_ALERT_WEBHOOK`. Reused as the alert pattern.
- **Nothing in the repo schedules** refresh / monitor / backup (no committed cron/timer).
- All services define healthchecks in `docker-compose.yml` (valhalla→`/status`, martin→`/tiles/health`,
  geocoder→`/healthz`, adapter/normalizer/gateway too) → health-gating can poll
  `docker inspect -f '{{.State.Health.Status}}'` without any published engine port.
- `services/adapter/app/main.py`: `_rl` and `_by_key` are unbounded `defaultdict`s keyed by API key
  (`main.py:117-119`); `require_key` raises `HTTPException(403, detail=...)` and `parse_latlng` raises
  `HTTPException(400, ...)` → FastAPI's default `{"detail": ...}` body, not a Google shape.

**Design decisions (locked with operator):**

- Deploy target: **plain VPS** (`/opt/nullmaps`).
- Scheduler: **ship both** systemd timers and a cron snippet, document both, operator picks at install.
- B2 error bodies: **keep the original HTTP status code** (403/400/…) **and** add a Google-shaped body
  (`status` + `error_message`). Google clients branch on `status`; HTTP-aware clients still see the code.
- Scope: all three groups (Ops resilience, Adapter robustness, Docs truth-up).

**Explicitly out of scope** (deferred): live-traffic / predictive ETAs (track ③), Photon swap, Redis,
multi-key auth, full disaster-recovery automation, `/metrics` key-gating (already shipped in ①).

## Goals / Success Criteria

1. A failed Valhalla graph rebuild **rolls back** to the previous graph and the box keeps routing.
2. `refresh.sh` restarts services by compose service name (project-agnostic) and **health-gates** each
   restart, **alerting** on any failure instead of failing silently.
3. `backup.sh` **verifies** every uploaded artifact, keeps **4 weekly snapshots**, and **alerts** on
   failure; a `restore-test` proves the latest backup is openable.
4. refresh / monitor / backup are **scheduled** by committed, installable timers (systemd) and a cron
   snippet.
5. The adapter's `_rl` / `_by_key` memory is **bounded** under a flood of distinct keys.
6. Adapter error responses on `/maps` and `/v1` are **Google-shaped** (`status` + `error_message`).
7. The fragile/untested adapter paths have tests; the suite is **green where `osmium` is absent**.
8. Docs reflect the **plain-VPS** reality and ops procedures.

## Design

### Group A — Ops resilience

#### A0. `infra/lib.sh` (new) — shared helpers
A tiny sourced library so refresh + monitor share one implementation:
- `ts()` → UTC timestamp.
- `log(msg)` → timestamped line to stdout + the caller's `$LOG`.
- `alert(msg)` → log + POST to `NULLMAPS_ALERT_WEBHOOK` if set (the existing monitor.sh pattern).
- `wait_healthy(svc, timeout_s)` → resolve the container via `docker compose -f docker-compose.yml ps -q
  <svc>`, poll `docker inspect -f '{{.State.Health.Status}}'` every 5s until `healthy`; return non-zero on
  timeout. Treats a service with no healthcheck as healthy once `running`.

`monitor.sh` is refactored to source `lib.sh` (behavior unchanged).

#### A1. `infra/refresh.sh` hardening
- Source `infra/lib.sh`.
- **Project-agnostic restarts:** replace `docker restart nullmaps-geocoder-1` / `nullmaps-martin-1` with
  `docker compose -f docker-compose.yml restart geocoder` / `martin`.
- **Geocoder swap with rollback:** before overwriting `geocoder.db`, keep `geocoder.db.bak`. After
  `restart geocoder`, `wait_healthy geocoder 60`. On timeout → restore `.bak`, restart again, `alert`.
- **Valhalla rebuild with rollback:** `mv valhalla_tiles.tar valhalla_tiles.tar.bak` (do **not** `rm`),
  `rm -rf valhalla_tiles`, force-recreate, `wait_healthy valhalla 600`. On success → keep the `.bak` as the
  single rollback copy. On timeout → restore `valhalla_tiles.tar.bak`, force-recreate again,
  `wait_healthy valhalla 600`, `alert "valhalla rebuild failed — rolled back to previous graph"`.
- **No silent failures:** each major step (pbf download, geocoder, valhalla, tiles, backup) is guarded;
  failure calls `alert` and is recorded, but an independent artifact's failure does not abort the others.
  A final `log "refresh DONE (errors: N)"` summarizes.
- Tiles refresh (`REFRESH_TILES=1`) gets the same `compose restart martin` + `wait_healthy martin`.

#### A2. `infra/backup.sh` robustness + `infra/restore-test.sh` (new)
- Source `infra/lib.sh`.
- Keep the flat `"$DEST/"` "latest" copies (restore.sh depends on them) for a fast box rebuild.
- **Verify:** after each `rclone copy`, run `rclone check --one-way <local> <remote>`; on mismatch,
  `alert "backup verify failed: <file>"` and exit non-zero.
- **Retention:** also copy the artifact set to `"$DEST/weekly/$(date +%G-W%V)/"`; then list `weekly/`
  subdirs and `rclone purge` all but the most recent 4.
- **Failure alert:** wrap the run so any non-zero step routes to `alert` (the refresh caller already pipes
  output to the log).
- `infra/restore-test.sh`: `rclone copy` `geocoder.db` and `vietnam.pmtiles` to a tmp dir, run
  `sqlite3 <db> 'PRAGMA integrity_check;'` (expect `ok`) and verify the PMTiles header magic
  (`PMTiles` / version byte), print PASS/FAIL, clean up tmp. Wire `make backup-test`.

#### A3. Scheduler — `infra/systemd/` + cron (both shipped)
- `infra/systemd/nullmaps-monitor.service` + `.timer` (`OnCalendar=*:0/5`, i.e. every 5 min).
- `infra/systemd/nullmaps-refresh.service` + `.timer` (`OnCalendar=Sun 03:30`, weekly off-peak).
- `infra/systemd/nullmaps-backup.service` + `.timer` (`OnCalendar=Wed 03:30`, a mid-week backup between
  refreshes; refresh already backs up at the end).
- Each `.service` is `Type=oneshot`, `WorkingDirectory=/opt/nullmaps`, runs the matching script as the
  deploy user, `EnvironmentFile=-/opt/nullmaps/.env`.
- `infra/install-systemd.sh` copies/links the units into `/etc/systemd/system/`, `daemon-reload`,
  `enable --now` the timers (idempotent).
- `infra/crontab.snippet` with the three equivalent cron lines; `infra/install-cron.sh` appends them to
  the deploy user's crontab idempotently (guarded by a `# nullmaps` marker block).
- Both documented in `infra/README.md`; operator picks one.

### Group B — Adapter robustness (`services/adapter/app/main.py`, TDD)

#### B1. Bound rate-limit / metrics memory
Replace the unbounded `_rl` and `_by_key` `defaultdict`s with `cachetools` (already a dependency):
- `_by_key` → `LRUCache(maxsize=1024)` with manual get-or-0 increment.
- `_rl` → `TTLCache(maxsize=1024, ttl=120)` (minute windows already expire; TTL bounds stragglers).
- `_counts` keyed by `(endpoint, status)` is finite (endpoints × status codes) → left as-is.

The middleware logic is adjusted to `cache.get(key, default)` + reassignment (cachetools caches don't
support `defaultdict` semantics).

#### B2. Google-shaped error bodies
Add an exception handler:

```python
_GOOGLE_STATUS = {400: "INVALID_REQUEST", 403: "REQUEST_DENIED", 404: "NOT_FOUND",
                  429: "OVER_QUERY_LIMIT", 502: "UNKNOWN_ERROR"}

@app.exception_handler(StarletteHTTPException)
async def google_error(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    if path.startswith("/maps") or path.startswith("/v1"):
        status = _GOOGLE_STATUS.get(exc.status_code, "UNKNOWN_ERROR")
        return JSONResponse({"status": status, "error_message": exc.detail},
                            status_code=exc.status_code)  # keep HTTP code, add Google body
    raise exc  # /healthz, /metrics, etc. keep FastAPI default
```

This makes `require_key` (403→REQUEST_DENIED) and `parse_latlng` (400→INVALID_REQUEST) failures
Google-shaped on the customer surface while preserving HTTP semantics.

#### B3. Expand tests (`services/adapter/tests/`, `services/geocoder/tests/`)
Adapter (engines mocked, following the existing `monkeypatch` + `TestClient` pattern):
- reverse geocode, autocomplete, nearbysearch, place details, isochrone, snap — happy path + shape.
- 429 rate-limit path (drive `RATE_LIMIT_PER_MIN` low, exceed it).
- error-body shape: missing key → `{"status":"REQUEST_DENIED"}`; bad `latlng` → `{"status":"INVALID_REQUEST"}`.
- cardinality cap: flood `_by_key` / `_rl` with >maxsize distinct keys → `len()` stays bounded.

Geocoder:
- Add `pytest.importorskip("osmium")` to `test_importer_and_service_fold_agree` so the suite is green
  where pyosmium isn't installed (it only runs in the geocoder image / CI).
- (Optional, light) a fixture-SQLite test asserting `fts_match` ranking order on a handful of seeded rows.

### Group C — Docs truth-up
- Rename `docs/runbook-deploy-coolify.md` → `docs/runbook-deploy-vps.md`; rewrite for plain
  `docker compose` on the Hostinger VPS (`/opt/nullmaps`), native Caddy TLS, and scheduler install
  (systemd + cron). Keep a one-line "Coolify also works as an alternative" note.
- `infra/README.md`: plain-VPS deploy, scheduler install (both mechanisms), backup/restore + restore-test,
  refresh cadence and rollback behavior.
- `CLAUDE.md`: change the "Deployed on Hetzner via Coolify" line to the real plain-VPS deploy
  (Coolify-compatible noted).
- `docs/runbook-ops.md` (new): day-2 cheat-sheet — refresh cadence, artifact locations, manual
  rollback/restore, key rotation, where logs/alerts go.

## Testing Strategy

- Adapter B1/B2/B3 are **TDD** (failing test → implement → pass), run via `python3 -m pytest`.
- Shell scripts (A0/A1/A2/A3) can't be unit-tested cleanly; verify with `bash -n` syntax checks +
  `shellcheck`, added to `.github/workflows/ci.yml` for `infra/*.sh`. `restore-test.sh` is the real
  functional check of the backup pipeline.
- Health-gate / rollback logic is validated by reading + `shellcheck`; a manual VPS dry-run is documented
  in the ops runbook (no automated way to fault-inject a graph build in CI).

## Risks & Mitigations

- **Valhalla rebuild rollback keeps only one `.bak`** — acceptable: the remote weekly backups are the
  deeper history; the `.bak` is just the immediate previous graph.
- **systemd vs cron drift** — shipping both risks double-scheduling if an operator installs both; the
  install scripts print a warning if the other mechanism's marker is already present.
- **`rclone purge` on weekly retention** — guarded to only operate under `"$DEST/weekly/"` and only on
  entries beyond the newest 4, to avoid deleting the flat "latest" set.
- **Error-body handler scope** — strictly limited to `/maps` and `/v1` prefixes so health/metrics/observability
  tooling is unaffected.

## Definition of Done

- All success criteria met; `python3 -m pytest` green in `services/adapter` and `services/geocoder`
  (osmium test skipped where absent).
- `shellcheck infra/*.sh` clean; `bash -n` passes; CI runs both.
- `infra/restore-test.sh` reports PASS against the live R2 backup (run manually on the box or documented).
- systemd units + cron snippet committed with idempotent installers.
- No doc references the geocoder as Photon-running or the deploy as Coolify-only; ops runbook exists.
