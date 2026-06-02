#!/usr/bin/env bash
# Self-heal + uptime probe for NullMaps. Runs from cron every few minutes.
#  - restarts any NullMaps container marked unhealthy (Docker healthchecks only
#    flag, they don't auto-restart)
#  - logs a line if the public URL is not 200
# Optional alerting: set NULLMAPS_ALERT_WEBHOOK to POST failures to Slack/Discord.
set -uo pipefail
LOG=/var/log/nullmaps-monitor.log
WEBHOOK="${NULLMAPS_ALERT_WEBHOOK:-}"
ts() { date -u +%FT%TZ; }
alert() { echo "$(ts) $1" >> "$LOG"; [ -n "$WEBHOOK" ] && curl -s -m 10 -X POST -H 'Content-Type: application/json' -d "{\"text\":\"NullMaps: $1\"}" "$WEBHOOK" >/dev/null 2>&1 || true; }

# 1) self-heal unhealthy containers
for c in $(docker ps --filter name=nullmaps --filter health=unhealthy -q); do
  name=$(docker inspect -f '{{.Name}}' "$c" | sed 's#^/##')
  docker restart "$c" >/dev/null 2>&1 && alert "restarted unhealthy $name"
done

# 2) public reachability
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://maps.nullshift.sh/style.json)
[ "$code" != "200" ] && alert "PUBLIC DOWN style.json http=$code"
exit 0
