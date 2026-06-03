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
| `GET /autocomplete?q=&limit=&lat=&lon=` | typeahead; diacritic-folded; optional viewport bias |
| `GET /geocode?q=&limit=&lat=&lon=` | forward geocode; optional viewport bias |
| `GET /reverse?lat=&lon=` | nearest named feature (R*Tree → haversine) |
| `GET /healthz` | feature count |

### Verified (HCMC)

- `autocomplete=ben thanh` → **Bến Thành** (place) first (diacritic folding + exact/prefix boost)
- `geocode=nguyen hue` → **Nguyễn Huệ** (folded match)
- `reverse=10.7725,106.6980` → **Chợ Bến Thành, 7.6 m** away

## Ranking

Order = exact folded match → prefix match → **most prominent & nearest** → BM25.

- **Prominence** (`importance` column, set at import): place type (city > town > village…), a
  population boost, +20 if the feature has wikidata/wikipedia, +25 for capitals. Lifts the big-city
  result for a name shared by many places.
- **Viewport bias** (optional `lat`/`lon`): a per-km penalty (~4 pts/km) pulls results toward a point.
  So `nguyen hue` with `lat=10.776&lon=106.700` returns the **HCMC** one; without bias it returns the
  most prominent (a northern locality). Reverse + typeahead remain strong.

Still lighter than Photon (no full address interpolation / global importance model), but the common
"which Nguyễn Huệ" ambiguity is resolved when the caller passes a location.

## Rebuild

The index is a build artifact (`services/geocoder/data/geocoder.db`, gitignored). Re-run `make
geo-index` after refreshing the OSM extract. Code changes to the service only need
`docker compose build geocoder && docker compose up -d geocoder` (no reindex).

## Accuracy features (③a)

- **VN query normalization** (`app/vnorm.py`): `q1`→`quan 1`, `p3`→`phuong 3`, `tp`/`tx` expansion,
  leading house-number stripped, leading `duong`/`đ.` dropped. Applied to every search.
- **Typo tolerance**: a hand-built trigram-similarity index (`trgm` table, pg_trgm-style Jaccard) is
  queried only when the strict FTS prefix returns nothing (so common-path latency is unchanged).
  Adds roughly +50–100 MB to the index, built over distinct folded names.
- **Admin boundaries**: `boundary=administrative` at admin_level 4/6/8 (province/district/ward) are
  indexed as `kind='boundary'` centroids, so `Quận 1` / `Bình Thạnh` / `Phường Bến Nghé` are findable.
- **Reverse**: prefers street/place/boundary over a co-located POI and refuses matches beyond
  `GEOCODER_REVERSE_MAX_M` (default 5000 m) — set the env var to widen for sparse rural areas.
- **Prominence**: population is parsed to an int and log10-scaled; same-name street segments within an
  admin area are merged to one representative point.
