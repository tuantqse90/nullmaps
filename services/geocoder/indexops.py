"""Pure SQLite index operations for the geocoder, separated from importer.py so
they can be unit-tested without osmium. Operate on an open sqlite3.Connection."""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from app.vnorm import trigrams


def insert_legacy_districts(con: sqlite3.Connection, path: str,
                            fold: Callable[[str], str]) -> int:
    """Index pre-2025 urban districts (Quận/Huyện, abolished in VN's 2025 admin reform)
    as searchable boundary points, so colloquial 'Quận 1' / 'Bình Thạnh' still resolve
    even though they are no longer OSM admin units. Idempotent. Returns the row count."""
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    con.execute("DELETE FROM features WHERE category = 'legacy_district'")
    con.executemany(
        "INSERT INTO features(osm_id, name, folded, kind, lat, lon, extra, importance, "
        "category, housenumber, street, city, district, region) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"legacy:{d['name']}", d["name"], fold(d["name"]), "boundary",
          d["lat"], d["lon"], d.get("city", ""), 60, "legacy_district",
          "", "", d.get("city", ""), "", "") for d in rows])
    return len(rows)


def build_trigrams(con: sqlite3.Connection) -> None:
    """(Re)build the `trgm` similarity index over DISTINCT folded strings."""
    con.execute("DROP TABLE IF EXISTS trgm")
    con.execute("CREATE TABLE trgm(g TEXT, folded TEXT)")
    rows = con.execute("SELECT DISTINCT folded FROM features WHERE folded <> ''").fetchall()
    batch: list[tuple[str, str]] = []
    for r in rows:
        folded = r[0]
        for g in trigrams(folded):
            batch.append((g, folded))
        if len(batch) >= 10000:
            con.executemany("INSERT INTO trgm(g, folded) VALUES (?, ?)", batch)
            batch.clear()
    if batch:
        con.executemany("INSERT INTO trgm(g, folded) VALUES (?, ?)", batch)
    con.execute("CREATE INDEX idx_trgm_g ON trgm(g)")


def merge_streets(con: sqlite3.Connection) -> None:
    """Collapse same-name street segments that are geographically close into one
    representative row (averaged location, max importance). A long street split across
    many OSM ways stops producing near-duplicate hits — but same-named streets in
    DIFFERENT places stay SEPARATE.

    The ~0.1° (~11 km) grid key is essential: district/city are unpopulated, so without
    it every same-name street nationwide (e.g. a "Nguyễn Duy Trinh" in HCMC and another
    in Bình Phước) would merge into a single row at their meaningless average centroid."""
    con.executescript("""
        DROP TABLE IF EXISTS _street_rep;
        CREATE TEMP TABLE _street_rep AS
          SELECT MIN(id) AS keep_id, folded,
                 COALESCE(NULLIF(district, ''), city, '') AS area,
                 AVG(lat) AS alat, AVG(lon) AS alon, MAX(importance) AS imp
          FROM features WHERE kind='street'
          GROUP BY folded, COALESCE(NULLIF(district, ''), city, ''),
                   CAST(lat / 0.1 AS INT), CAST(lon / 0.1 AS INT);
        UPDATE features SET
          lat = (SELECT alat FROM _street_rep WHERE keep_id = features.id),
          lon = (SELECT alon FROM _street_rep WHERE keep_id = features.id),
          importance = (SELECT imp FROM _street_rep WHERE keep_id = features.id)
          WHERE id IN (SELECT keep_id FROM _street_rep);
        DELETE FROM features
          WHERE kind='street' AND id NOT IN (SELECT keep_id FROM _street_rep);
        DROP TABLE _street_rep;
    """)
