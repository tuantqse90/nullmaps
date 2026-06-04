#!/usr/bin/env bash
# Bring up Photon: on first run, download + unpack the prebuilt Vietnam search index
# into the data volume; thereafter just serve it. The dump untars to $DATA/photon_data.
set -euo pipefail

DATA="${PHOTON_DATA:-/photon}"
EXTRACT_URL="${PHOTON_EXTRACT_URL:-https://download1.graphhopper.com/public/extracts/by-country-code/vn/photon-db-vn-latest.tar.bz2}"

mkdir -p "$DATA"
if [ ! -d "$DATA/photon_data" ]; then
  echo ">> Photon index missing — downloading VN dump ($EXTRACT_URL)"
  # stream-extract so we never store the full .tar.bz2 (saves disk)
  curl -fSL "$EXTRACT_URL" | tar -xj -C "$DATA"
  echo ">> Photon VN index ready ($(du -sh "$DATA/photon_data" | cut -f1))"
fi

# -Xmx via JAVA_OPTS (default 1g — VN-only index is small); bind all interfaces so the
# adapter can reach it over the compose network.
exec java ${JAVA_OPTS:--Xmx1g} -jar /photon.jar \
  -data-dir "$DATA" -listen-ip 0.0.0.0 -listen-port 2322 "$@"
