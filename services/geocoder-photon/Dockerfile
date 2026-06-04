# Photon (komoot) — prominence-ranked, typeahead-first geocoder backed by an embedded
# OpenSearch index. We serve the prebuilt Vietnam dump (no Nominatim import needed).
#
# Photon >= 1.0.0 uses OpenSearch (the old Elasticsearch backend was removed); the
# graphhopper by-country-code dumps require Photon >= 1.0.1 — we pin 1.1.0.
FROM eclipse-temurin:21-jre-jammy

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates bzip2 \
 && rm -rf /var/lib/apt/lists/*

ARG PHOTON_VERSION=1.1.0
RUN curl -fSL -o /photon.jar \
    "https://github.com/komoot/photon/releases/download/${PHOTON_VERSION}/photon-${PHOTON_VERSION}.jar"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PHOTON_DATA=/photon
EXPOSE 2322
ENTRYPOINT ["/entrypoint.sh"]
