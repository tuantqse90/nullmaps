# Overture business-POI index (`overture_vn.db`)

Adds the ≈978k Vietnamese **business POIs** — cafés, shops, offices, clinics — that
OpenStreetMap (and therefore Photon) mostly lacks. Sourced from
[Overture Maps](https://overturemaps.org) Places (Meta + Microsoft data, monthly).

The adapter mounts this read-only and **merges prefix hits into Photon text-search**
(`/geocode`, `/autocomplete`): Photon keeps its street/address quality on top, exact
business-name matches surface from Overture. See `services/adapter/app/main.py`
(`_overture_query`, the merge in `geocoder()`).

## What's in it

`overture_vn.db` (SQLite, ≈475 MB):

| table          | purpose                                                                                                  |
|----------------|----------------------------------------------------------------------------------------------------------|
| `places`       | `name, lon, lat, category, cats, context, conf, phone, website, social, brand, folded, ward, province` (≈1.25M rows) |
| `places_fts`   | FTS5 prefix index over `folded` (diacritic-folded name) — text autocomplete                              |
| `places_rtree` | R*Tree over `lon/lat` — nearby-by-category radius search                                                  |

`context` is Overture's freeform address; the adapter shows only its first segment
(the street/house number, e.g. `76A Đường Lê Lai`) — the tail often carries **stale
pre-2025 admin names**. `ward` + `province` are the **authoritative 2025 names**,
point-in-polygon tagged from Overture Divisions (see below). `conf` is confidence ×100.
`cats` is Overture's alternate categories (comma-joined) used to widen nearby recall.
`phone`/`website`/`social`/`brand` power Google-shaped **Place Details** (≈87 % have a
phone, ≈40 % a website).

The autocomplete secondary line is `<street>, <ward>, <province>` — e.g.
`76A Đường Lê Lai, Phường Bến Thành, Thành phố Hồ Chí Minh`. Place-details on an
`ov:<rowid>` place_id returns the phone/website/brand.

## 2025 admin tagging

Vietnam's July-2025 reform abolished districts and went two-tier:
**province (34) → ward/commune (≈3,300)**. The build tags every POI by point-in-polygon
against Overture Divisions `division_area`:

- `subtype='region'`  → the 34 reformed provinces (`Tỉnh …` / `Thành phố …`)
- `subtype='locality'` → ≈3,387 wards (`Phường …` / `Xã …` / `Đặc khu …`)

≈99 % of POIs get a ward; the rest are offshore/border points with no covering polygon.

## Build / refresh

Overture ships a new release ~monthly. To refresh:

```bash
# one-time: DuckDB (PEP668 boxes need the flag)
python3 -m pip install --user --break-system-packages duckdb

# build (RELEASE defaults to 2026-05-20.0). ~18 min, ~2 GB scratch disk.
# OVERTURE_MIN_CONF (default 0.3) sets the confidence floor; VN_ADMIN_DUCKDB points at a
# cached ward/region polygon DB to skip the slow S3 division read on a re-run.
python3 infra/overture/build_overture_db.py 2026-05-20.0 /tmp/overture_vn.db

# ship to the box (as .new, then atomic mv so the live adapter never reads a half-written
# file) + restart the adapter (reopens the file via the ./data mount)
scp /tmp/overture_vn.db <box>:/opt/nullmaps/data/overture_vn.db.new
ssh <box> 'cd /opt/nullmaps && mv -f data/overture_vn.db.new data/overture_vn.db && docker compose restart adapter'
```

Find the latest release id at <https://docs.overturemaps.org/release/latest/> (or
list `s3://overturemaps-us-west-2/release/`).

## Filters (why ≈1.25M, not 2M)

The build keeps only rows with:

- `addresses[1].country = 'VN'` — the bbox alone (lon 102–110) leaks Thai/Khmer
  border POIs (e.g. "Gulf of Thailand"); the country tag trims them.
- `confidence >= OVERTURE_MIN_CONF` (default **0.3**) — keeps the long tail for rural
  coverage; the adapter's ranking demotes low-confidence hits so they only surface when
  their name is actually typed. Raise toward 0.5 if junk leaks in.
- `length(name) <= 80` — skips description-as-name junk.

## Notes

- **Read-only at runtime.** The adapter never writes it. Absent file = the merge is
  silently skipped (Photon-only), so a box without the file still works.
- **Not committed.** It's a regenerable data artifact (`*.db` is gitignored). Only the
  build script lives in git.
- Mounted via `./data:/data:ro` in `docker-compose.yml`; path set by `OVERTURE_DB`.
