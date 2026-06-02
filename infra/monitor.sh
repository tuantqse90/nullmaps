#!/usr/bin/env bash
# Self-heal + uptime probe for NullMaps. Runs every few minutes (systemd timer / cron).
#  - restarts any NullMaps container marked unhealthy (Docker healthchecks only flag)
#  - alerts if the public URL is not 200
# Optional alerting: set NULLMAPS_ALERT_WEBHOOK to POST failures to Slack/Discord.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck disable=SC2034
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
