#!/usr/bin/env bash
# NullMaps bug-hunting harness — run the backend fuzzer, backend probe, and the
# frontend headless smoke against a target, aggregate, and fail if any found a bug.
#
#   API_KEY=... NM_BASIC=nullshift:PASS NM_BASE=https://maps.nullshift.sh harness/run.sh
#   harness/run.sh be          # backend only (fuzz + probe)
#   harness/run.sh fe          # frontend only
#
# Config (env):
#   NM_BASE   target base URL          (default http://localhost:8088)
#   API_KEY   shared key for /maps,/v1 (required; falls back to .env)
#   NM_BASIC  user:pass for /app + page (frontend only)
#   CHROME    Chrome executable for the FE smoke
set -uo pipefail
cd "$(dirname "$0")/.."

# pull API_KEY from .env if not in the environment
if [ -z "${API_KEY:-}" ] && [ -f .env ]; then
  API_KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2-)
  export API_KEY
fi
export NM_BASE="${NM_BASE:-http://localhost:8088}"
WHAT="${1:-all}"
rc=0

echo "==================== NullMaps bug harness ===================="
echo "target: $NM_BASE"
echo

if [ "$WHAT" = "all" ] || [ "$WHAT" = "be" ]; then
  echo "---- backend fuzz (robustness: no 5xx / contract) ----"
  python3 harness/be_fuzz.py  || rc=1
  echo
  echo "---- backend probe (correctness: routes + geocode accuracy) ----"
  python3 harness/be_probe.py || rc=1
  echo
fi

if [ "$WHAT" = "all" ]; then
  # BE just spent the per-key rate-limit budget; the FE's /app calls share that one
  # injected key, so give the minute window time to reset before the headless run.
  echo "(pausing 65s so the rate-limit window resets before the FE run...)"
  sleep 65
fi

if [ "$WHAT" = "all" ] || [ "$WHAT" = "fe" ]; then
  echo "---- frontend smoke (headless: features + 0 pageerrors) ----"
  if ! node -e "require.resolve('puppeteer-core')" >/dev/null 2>&1; then
    # install into harness/ (node_modules is gitignored) so the smoke can import it
    echo "   (installing puppeteer-core into harness/ ...)"
    ( cd harness && [ -f package.json ] || echo '{"name":"nm-harness","private":true}' > package.json
      npm i --silent puppeteer-core@23 >/dev/null 2>&1 )
  fi
  ( cd harness && NODE_PATH="$(pwd)/node_modules" node fe_smoke.mjs ) || rc=1
  echo
  echo "---- theme switcher visual regression (Playwright) ----"
  if ! ( cd harness && node -e "require.resolve('playwright-core')" ) >/dev/null 2>&1; then
    echo "   (installing playwright-core into harness/ ...)"
    ( cd harness && [ -f package.json ] || echo '{"name":"nm-harness","private":true}' > package.json
      PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i --silent playwright-core@1.48 >/dev/null 2>&1 )
  fi
  ( cd harness && node fe_theme.mjs ) || rc=1
  echo
fi

echo "==================== done (exit $rc) ===================="
exit $rc
