# Photon (komoot) — prominence-ranked, typeahead-first geocoder. We serve the prebuilt
# graphhopper Vietnam dump (no Nominatim import needed).
#
# The by-country-code dumps are **Elasticsearch-format** (photon_data/elasticsearch/),
# so we pin Photon 0.7.x — the last line that still reads the ES backend (1.0.0+ moved
# to OpenSearch and cannot load these dumps). Old embedded ES wants Java 17, not 21.
FROM eclipse-temurin:17-jre-jammy

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates bzip2 \
 && rm -rf /var/lib/apt/lists/*

ARG PHOTON_VERSION=0.7.4
RUN curl -fSL -o /photon.jar \
    "https://github.com/komoot/photon/releases/download/${PHOTON_VERSION}/photon-${PHOTON_VERSION}.jar"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PHOTON_DATA=/photon
EXPOSE 2322
ENTRYPOINT ["/entrypoint.sh"]
