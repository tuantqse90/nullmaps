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
