#!/usr/bin/env python3
"""Build the Overture Maps VN business-POI index the adapter merges into Photon.

What it produces
----------------
`overture_vn.db` — a SQLite file with:
  * `places(name, lon, lat, category, context, conf, folded)`  — ≈978k VN POIs
  * `places_fts`  — an FTS5 prefix index over the diacritic-folded name

These are the cafés / shops / offices / clinics that OSM (and therefore Photon)
mostly lack. The adapter mounts this read-only and merges prefix hits into its
text-search results (see services/adapter/app/main.py `_overture_query`).

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

Runs in ~10 min: ~9 min streaming the VN row-groups from S3 (public, no creds),
~1 min folding + building the FTS index. Needs ~1 GB free disk.
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
             COALESCE(addresses[1].freeform, addresses[1].locality) AS context,
             CAST(round(confidence*100) AS INTEGER) AS conf
      FROM read_parquet('{src}')
      WHERE bbox.xmin BETWEEN {x0} AND {x1} AND bbox.ymin BETWEEN {y0} AND {y1}
        AND addresses[1].country='VN' AND confidence>=0.5
        AND names.primary IS NOT NULL AND length(names.primary)<=80""")
    n = con.execute("SELECT count(*) FROM o.places").fetchone()[0]
    con.close()
    print(f"  extracted {n:,} VN places in {time.time()-t:.0f}s", flush=True)


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
    """)
    con.commit()
    con.close()
    size = os.path.getsize(out) / 1048576
    print(f"  folded {len(rows):,} + built FTS in {time.time()-t:.0f}s | {out} = {size:.0f} MB", flush=True)


if __name__ == "__main__":
    extract(OUT)
    build_index(OUT)
    print("done.", flush=True)
