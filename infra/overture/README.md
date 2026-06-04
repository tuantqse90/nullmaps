# Overture business-POI index (`overture_vn.db`)

Adds the ≈978k Vietnamese **business POIs** — cafés, shops, offices, clinics — that
OpenStreetMap (and therefore Photon) mostly lacks. Sourced from
[Overture Maps](https://overturemaps.org) Places (Meta + Microsoft data, monthly).

The adapter mounts this read-only and **merges prefix hits into Photon text-search**
(`/geocode`, `/autocomplete`): Photon keeps its street/address quality on top, exact
business-name matches surface from Overture. See `services/adapter/app/main.py`
(`_overture_query`, the merge in `geocoder()`).

## What's in it

`overture_vn.db` (SQLite, ≈220 MB):

| table        | purpose                                                                      |
|--------------|------------------------------------------------------------------------------|
| `places`     | `name, lon, lat, category, context, conf, folded, ward, province` (≈978k rows) |
| `places_fts` | FTS5 prefix index over `folded` (diacritic-folded name)                      |

`context` is Overture's freeform address; the adapter shows only its first segment
(the street/house number, e.g. `76A Đường Lê Lai`) — the tail often carries **stale
pre-2025 admin names**. `ward` + `province` are the **authoritative 2025 names**,
point-in-polygon tagged from Overture Divisions (see below). `conf` is confidence ×100.

The autocomplete secondary line is then `<street>, <ward>, <province>` — e.g.
`76A Đường Lê Lai, Phường Bến Thành, Thành phố Hồ Chí Minh`.

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

# build (RELEASE defaults to 2026-05-20.0). ~10 min, ~1 GB scratch disk.
python3 infra/overture/build_overture_db.py 2026-05-20.0 /tmp/overture_vn.db

# ship to the box + restart the adapter (picks up the new file via the ./data mount)
scp /tmp/overture_vn.db <box>:/opt/nullmaps/data/overture_vn.db
ssh <box> 'cd /opt/nullmaps && docker compose restart adapter'
```

Find the latest release id at <https://docs.overturemaps.org/release/latest/> (or
list `s3://overturemaps-us-west-2/release/`).

## Filters (why ≈978k, not 2M)

The build keeps only rows with:

- `addresses[1].country = 'VN'` — the bbox alone (lon 102–110) leaks Thai/Khmer
  border POIs (e.g. "Gulf of Thailand"); the country tag trims them.
- `confidence >= 0.5` — drops the low-trust long tail.
- `length(name) <= 80` — skips description-as-name junk.

## Notes

- **Read-only at runtime.** The adapter never writes it. Absent file = the merge is
  silently skipped (Photon-only), so a box without the file still works.
- **Not committed.** It's a regenerable data artifact (`*.db` is gitignored). Only the
  build script lives in git.
- Mounted via `./data:/data:ro` in `docker-compose.yml`; path set by `OVERTURE_DB`.
