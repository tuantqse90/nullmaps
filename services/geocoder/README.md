# services/geocoder — Geocoding, Reverse, Autocomplete (Phase 3)

> **Status: built & verified (lightweight).** Serves on `:2322` from a SQLite index.

**What:** Address ↔ coordinate lookup and typeahead for VN, diacritic-folded.

**Why:** Address typeahead for app forms + geocoding/reverse for routing inputs.

## Design — lightweight, not Photon

A full Photon/Nominatim stack needs a heavy import (PostgreSQL, hours, tens of GB RAM/disk). For
**internal "good enough"** use, this service instead builds a compact index directly from the OSM
extract:

```
vietnam-latest.osm.pbf --(importer.py, pyosmium)--> geocoder.db (SQLite)
  features        named places / streets / POIs / addresses (id, name, folded, kind, lat, lon)
  features_fts    FTS5 over diacritic-folded name (typeahead, unicode61)
  features_rtree  R*Tree over lat/lon (reverse geocoding)
```

~308k named VN features, ~56 MB DB, builds in a few minutes, ~tens of MB RAM to serve. **Photon stays
the production option** for the Hetzner box when address-level recall/ranking matters.

## Build & run

```bash
make geo-index     # build the SQLite index from data/raw + start the service
make geo-test      # autocomplete / geocode / reverse smoke test
```

## Endpoints (`:2322`)

| Endpoint | Use |
|---|---|
| `GET /autocomplete?q=&limit=` | typeahead; diacritic-folded prefix match |
| `GET /geocode?q=&limit=` | forward geocode (best matches) |
| `GET /reverse?lat=&lon=` | nearest named feature (R*Tree → haversine) |
| `GET /healthz` | feature count |

### Verified (HCMC)

- `autocomplete=ben thanh` → **Bến Thành** (place) first (diacritic folding + exact/prefix boost)
- `geocode=nguyen hue` → **Nguyễn Huệ** (folded match)
- `reverse=10.7725,106.6980` → **Chợ Bến Thành, 7.6 m** away

## Ranking (known limitation)

Order = exact folded match → prefix match → place > street > POI → BM25. There is **no
importance/population signal**, so forward-geocoding a name shared by many places (e.g. "Nguyễn Huệ"
exists in many cities) returns *a* correct match, not necessarily the most prominent one. Reverse
geocoding and typeahead are strong; prominence-ranked forward geocoding is where Photon would win.

## Rebuild

The index is a build artifact (`services/geocoder/data/geocoder.db`, gitignored). Re-run `make
geo-index` after refreshing the OSM extract. Code changes to the service only need
`docker compose build geocoder && docker compose up -d geocoder` (no reindex).
