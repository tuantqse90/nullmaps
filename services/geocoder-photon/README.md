# geocoder-photon — Photon (prominence-ranked typeahead)

Photon (komoot) serving the prebuilt **graphhopper Vietnam dump** — a proper
search-as-you-type geocoder with prominence ranking and rich result context
(street / ward / district / city on every hit). It replaces the lightweight SQLite
geocoder for **text search** (autocomplete / geocode / reverse); the SQLite engine
stays for nearby-by-category + place-details and as a fallback.

## Why

The SQLite substitute was "good enough" for typeahead but visibly weak: it returned
the same name 3–5× with no distinguishing context ("Highlands ×5"), and ranked by raw
importance. Photon dedupes by relevance, ranks by prominence + proximity, and — the big
one — returns **district/city/street** for each result, so branches are distinguishable
("Highlands Coffee · Lê Lợi, Bến Thành" vs "· Nguyễn Du, Bến Thành").

## How it runs

- `Dockerfile`: `eclipse-temurin:17-jre` + **Photon 0.7.4** (the ES-backed JAR
  `photon-0.7.4.jar` — the graphhopper `by-country-code` dumps are Elasticsearch-format,
  `photon_data/elasticsearch/`; Photon ≥ 1.0 moved to OpenSearch and **cannot** read them).
- `entrypoint.sh`: on first run, discovers the newest `photon-db-vn-<date>.tar.bz2` from
  the graphhopper listing (the `-latest` alias 404s on their server) and stream-extracts
  it into the data volume; thereafter just serves. Override with `PHOTON_EXTRACT_URL`.
- Listens on `:2322` (internal). API: `GET /api?q=&limit=&lat=&lon=` and
  `GET /reverse?lat=&lon=&limit=`. Returns GeoJSON.
- Resources: ~0.5–1 GB index on disk, **~300 MB RAM** in practice (`-Xmx1g`, `mem_limit 2g`).

## Wiring

`docker-compose.yml` runs it as `photon`; the adapter has `PHOTON_URL` +
`SEARCH_ENGINE=photon`. The adapter's `geocoder()` routes `/geocode`, `/autocomplete`,
`/reverse` to Photon (mapping its GeoJSON onto the internal result dict via
`_photon_feature`) and **falls back to the SQLite geocoder on error/empty**.
`SEARCH_ENGINE=sqlite` reverts entirely. nearby/details always use SQLite.

## Refresh

Bump the index by clearing the volume and restarting (it re-downloads the newest dump):

```bash
docker compose stop photon && rm -rf services/geocoder-photon/data/* \
  && docker compose up -d photon          # first boot re-downloads + indexes (~3-5 min)
```

## Known trade-offs vs the SQLite engine

- **Genuine misspellings** (a dropped/added letter, e.g. `nguyn hue`) are handled a bit
  worse than the SQLite trigram fallback. No-diacritics input (`nguyen hue`, `hoan kiem`)
  works fine — Photon folds tone marks.
- Ranking is **viewport-bias sensitive**: a cross-region query (a Hà Nội place searched
  while the map is on HCMC) is pulled toward the viewport. In normal use the map is
  centred on the region being searched, so this is rarely hit.
- `place_id` differs between engines (Photon `osm_type+osm_id` vs SQLite `osm_id`), so a
  Photon autocomplete `place_id` won't resolve via the SQLite `place/details` endpoint.
  The web app geocodes by text on pick, so it isn't affected.
