#!/usr/bin/env python3
"""Build the Overture Maps VN business-POI index the adapter merges into Photon.

What it produces
----------------
`overture_vn.db` — a SQLite file (≈475 MB) with:
  * `places(name, lon, lat, category, cats, context, conf, phone, website, social,
            brand, folded, ward, province)` — ≈1.25M VN POIs
  * `places_fts`   — FTS5 prefix index over the diacritic-folded name (text search)
  * `places_rtree` — R*Tree over lon/lat (nearby-by-category radius search)

These are the cafés / shops / offices / clinics that OSM (and therefore Photon)
mostly lack. `ward`/`province` are the authoritative 2025 admin names, point-in-polygon
tagged from Overture Divisions. `phone`/`website`/`brand` power Place Details. The
adapter mounts this read-only and merges prefix hits into its text-search results,
serves nearby-by-category, and resolves `ov:<rowid>` place_ids (see
services/adapter/app/main.py `_overture_query` / `_overture_nearby` / `_overture_detail`).

Why these filters
-----------------
  * `addresses[1].country = 'VN'`  — the bbox alone leaks Thai/Khmer border POIs.
  * `confidence >= 0.5`            — drops the long tail of low-trust junk.
  * `length(name) <= 80`           — skips description-as-name garbage.

Refresh cadence: Overture ships monthly. Bump RELEASE, re-run, re-ship (see README).

Usage
-----
    python3 -m pip install --user --break-system-packages duckdb   # PEP668 box
    python3 build_overture_db.py [RELEASE] [OUT]
        RELEASE  default 2026-05-20.0
        OUT      default ./overture_vn.db

Runs in ~18 min: ~12 min streaming VN POIs from S3 (public, no creds; conf>=0.3 reads
more than 0.5), ~10 s folding + FTS + R*Tree, ~3-7 min the ward/province spatial join
(set VN_ADMIN_DUCKDB to a cached polygon DB to skip the S3 division read). Needs ~2 GB
free disk and the DuckDB spatial extension (auto-installed).
"""
import os
import sys
import time
import sqlite3
import unicodedata

RELEASE = sys.argv[1] if len(sys.argv) > 1 else "2026-05-20.0"
OUT = sys.argv[2] if len(sys.argv) > 2 else "overture_vn.db"
# Vietnam bounding box (lon 102-110, lat 8-24) — country filter trims the overhang.
BBOX = (102, 110, 8, 24)
# Minimum Overture confidence. 0.3 keeps the long tail (rural coverage) while the
# adapter's ranking demotes low-confidence hits below prominent ones, so they only
# surface when their name is actually typed. Raise toward 0.5 if junk leaks in.
MIN_CONF = float(os.environ.get("OVERTURE_MIN_CONF", "0.3"))
# Optional cached polygon DB (tables wards, regions) to skip the S3 division read.
ADMIN_CACHE = os.environ.get("VN_ADMIN_DUCKDB", "")


def fold(s: str) -> str:
    """Diacritic-fold + lowercase (Đ/đ -> d) — must match the adapter's `_fold`."""
    s = (s or "").replace("Đ", "D").replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower().strip()


def extract(out: str) -> None:
    import duckdb
    if os.path.exists(out):
        os.remove(out)
    con = duckdb.connect()
    con.execute("INSTALL httpfs;LOAD httpfs;INSTALL spatial;LOAD spatial;INSTALL sqlite;LOAD sqlite;")
    con.execute("SET s3_region='us-west-2';SET http_timeout=300000;SET http_retries=10;SET http_keep_alive=true;")
    con.execute(f"ATTACH '{out}' AS o (TYPE sqlite);")
    src = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme=places/type=place/*"
    x0, x1, y0, y1 = BBOX
    t = time.time()
    print(f"streaming VN places from {RELEASE} (this is the slow part)...", flush=True)
    con.execute(f"""CREATE TABLE o.places AS
      SELECT names.primary AS name, ST_X(geometry) AS lon, ST_Y(geometry) AS lat,
             categories.primary AS category,
             array_to_string(categories.alternate, ',') AS cats,
             COALESCE(addresses[1].freeform, addresses[1].locality) AS context,
             CAST(round(confidence*100) AS INTEGER) AS conf,
             phones[1] AS phone, websites[1] AS website,
             socials[1] AS social, brand.names.primary AS brand
      FROM read_parquet('{src}')
      WHERE bbox.xmin BETWEEN {x0} AND {x1} AND bbox.ymin BETWEEN {y0} AND {y1}
        AND addresses[1].country='VN' AND confidence>={MIN_CONF}
        AND names.primary IS NOT NULL AND length(names.primary)<=80""")
    n = con.execute("SELECT count(*) FROM o.places").fetchone()[0]
    meta = con.execute("SELECT count(phone), count(website), count(brand) FROM o.places").fetchone()
    con.close()
    print(f"  extracted {n:,} VN places (conf>={MIN_CONF}) in {time.time()-t:.0f}s "
          f"| phone {meta[0]:,} web {meta[1]:,} brand {meta[2]:,}", flush=True)


def enrich_admin(out: str) -> None:
    """Point-in-polygon tag every POI with its 2025 ward + province, from Overture
    Divisions (the `division_area` polygons: subtype 'locality' = phường/xã/đặc khu,
    subtype 'region' = the 34 reformed provinces). A build-time bbox-prefiltered spatial
    join; the adapter then shows '<street>, <ward>, <province>' as the secondary line.

    Overture's freeform address tail often holds stale pre-2025 names, so we tag
    authoritatively here rather than trusting it."""
    import duckdb
    da = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme=divisions/type=division_area/*"
    con = duckdb.connect()
    con.execute("INSTALL httpfs;LOAD httpfs;INSTALL spatial;LOAD spatial;INSTALL sqlite;LOAD sqlite;")
    con.execute("SET s3_region='us-west-2';SET http_timeout=300000;SET http_retries=10;SET http_keep_alive=true;")
    t = time.time()
    if ADMIN_CACHE and os.path.exists(ADMIN_CACHE):
        print(f"loading ward/province polygons from cache {ADMIN_CACHE}...", flush=True)
        con.execute(f"ATTACH '{ADMIN_CACHE}' AS cache (READ_ONLY);")
        con.execute("CREATE TEMP TABLE wards AS SELECT * FROM cache.wards;")
        con.execute("CREATE TEMP TABLE regions AS SELECT * FROM cache.regions;")
        con.execute("DETACH cache;")
    else:
        print("loading VN ward/province polygons from S3...", flush=True)
        for tbl, sub in (("wards", "locality"), ("regions", "region")):
            con.execute(f"""CREATE TEMP TABLE {tbl} AS
              SELECT names.primary AS name, geometry AS geom,
                     ST_XMin(geometry) xmin, ST_XMax(geometry) xmax,
                     ST_YMin(geometry) ymin, ST_YMax(geometry) ymax
              FROM read_parquet('{da}') WHERE country='VN' AND subtype='{sub}'""")
    con.execute(f"ATTACH '{out}' AS o (TYPE sqlite);")
    con.execute("CREATE TEMP TABLE pts AS SELECT rowid AS rid, lon, lat FROM o.places;")
    print(f"  polygons loaded [{time.time()-t:.0f}s]; spatial join...", flush=True)
    # bbox BETWEEN prefilter makes ST_Contains test only the 1-3 candidate polygons/point
    con.execute("""CREATE TEMP TABLE wmap AS SELECT p.rid, ANY_VALUE(w.name) AS ward
      FROM pts p JOIN wards w ON p.lon BETWEEN w.xmin AND w.xmax AND p.lat BETWEEN w.ymin AND w.ymax
        AND ST_Contains(w.geom, ST_Point(p.lon, p.lat)) GROUP BY p.rid;""")
    con.execute("""CREATE TEMP TABLE rmap AS SELECT p.rid, ANY_VALUE(r.name) AS province
      FROM pts p JOIN regions r ON p.lon BETWEEN r.xmin AND r.xmax AND p.lat BETWEEN r.ymin AND r.ymax
        AND ST_Contains(r.geom, ST_Point(p.lon, p.lat)) GROUP BY p.rid;""")
    rows = con.execute("""SELECT pts.rid, wmap.ward, rmap.province FROM pts
      LEFT JOIN wmap ON wmap.rid=pts.rid LEFT JOIN rmap ON rmap.rid=pts.rid;""").fetchall()
    con.close()
    s = sqlite3.connect(out)
    cols = [r[1] for r in s.execute("PRAGMA table_info(places)")]
    if "ward" not in cols:
        s.execute("ALTER TABLE places ADD COLUMN ward TEXT")
    if "province" not in cols:
        s.execute("ALTER TABLE places ADD COLUMN province TEXT")
    s.executemany("UPDATE places SET ward=?, province=? WHERE rowid=?", [(r[1], r[2], r[0]) for r in rows])
    s.commit()
    s.close()
    tagged = sum(1 for r in rows if r[1])
    print(f"  tagged {tagged:,}/{len(rows):,} wards ({100*tagged//max(1,len(rows))}%) in {time.time()-t:.0f}s", flush=True)


def build_index(out: str) -> None:
    con = sqlite3.connect(out)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(places)")]
    if "folded" not in cols:
        con.execute("ALTER TABLE places ADD COLUMN folded TEXT")
    t = time.time()
    rows = con.execute("SELECT rowid, name FROM places").fetchall()
    con.executemany("UPDATE places SET folded=? WHERE rowid=?", [(fold(r["name"]), r["rowid"]) for r in rows])
    con.executescript("""
      DROP TABLE IF EXISTS places_fts;
      CREATE VIRTUAL TABLE places_fts USING fts5(folded, content='places', content_rowid='rowid', tokenize='unicode61');
      INSERT INTO places_fts(rowid, folded) SELECT rowid, folded FROM places;
      CREATE INDEX IF NOT EXISTS idx_places_conf ON places(conf);
      -- R*Tree for nearby-by-category (radius box prefilter, see adapter _overture_nearby)
      DROP TABLE IF EXISTS places_rtree;
      CREATE VIRTUAL TABLE places_rtree USING rtree(id, minLon, maxLon, minLat, maxLat);
      INSERT INTO places_rtree(id, minLon, maxLon, minLat, maxLat)
        SELECT rowid, lon, lon, lat, lat FROM places WHERE lon IS NOT NULL AND lat IS NOT NULL;
    """)
    con.commit()
    con.close()
    size = os.path.getsize(out) / 1048576
    print(f"  folded {len(rows):,} + FTS + R*Tree in {time.time()-t:.0f}s | {out} = {size:.0f} MB", flush=True)


if __name__ == "__main__":
    extract(OUT)
    build_index(OUT)
    enrich_admin(OUT)   # PIP-tag 2025 ward + province
    print("done.", flush=True)
