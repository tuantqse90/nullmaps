#!/usr/bin/env bash
# Bring up Photon: on first run, download + unpack the prebuilt Vietnam search index
# into the data volume; thereafter just serve it. The dump untars to $DATA/photon_data.
set -euo pipefail

DATA="${PHOTON_DATA:-/photon}"
BASE="https://download1.graphhopper.com/public/extracts/by-country-code/vn/"

mkdir -p "$DATA"
if [ ! -d "$DATA/photon_data" ]; then
  URL="${PHOTON_EXTRACT_URL:-}"
  if [ -z "$URL" ]; then
    # the "-latest" alias 404s on their server — discover the newest dated dump instead
    FILE=$(curl -fsSL "$BASE" | grep -oE 'photon-db-vn-[0-9]{6}\.tar\.bz2' | sort -u | tail -1)
    [ -n "$FILE" ] || { echo "!! could not find a VN dump under $BASE"; exit 1; }
    URL="${BASE}${FILE}"
  fi
  echo ">> Photon index missing — downloading VN dump: $URL"
  # stream-extract so we never store the full .tar.bz2 (saves disk)
  curl -fSL "$URL" | tar -xj -C "$DATA"
  echo ">> Photon VN index ready ($(du -sh "$DATA/photon_data" | cut -f1))"
fi

# -Xmx via JAVA_OPTS (default 1g — VN-only index is small); bind all interfaces so the
# adapter can reach it over the compose network.
exec java ${JAVA_OPTS:--Xmx1g} -jar /photon.jar \
  -data-dir "$DATA" -listen-ip 0.0.0.0 -listen-port 2322 "$@"
