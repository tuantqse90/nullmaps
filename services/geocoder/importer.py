"""Build a lightweight geocoder index from an OSM extract.

Reads a .osm.pbf, extracts named features (places, POIs, streets) and writes a
single SQLite file with:
  - features      : id, name, folded, kind, lat, lon, extra
  - features_fts  : FTS5 over `folded` (diacritic-folded) for typeahead
  - features_rtree: R*Tree over lat/lon for reverse geocoding

"Good enough for internal use," not Photon/Nominatim. Diacritic folding lets
'nguyen' match 'Nguyễn' and 'q1' / 'quan 1' behave for VN input.

Usage (inside the build container):
    python importer.py /data/raw/vietnam-latest.osm.pbf /data/geocoder.db
"""
from __future__ import annotations

import sqlite3
import sys
import unicodedata

import osmium

# Tag sets that make a feature worth indexing (must also have a name).
PLACE = {"city", "town", "village", "suburb", "neighbourhood", "hamlet",
         "quarter", "borough", "municipality", "locality"}
POI_KEYS = ("amenity", "shop", "tourism", "office", "leisure", "healthcare",
            "aeroway", "public_transport", "railway")
STREET_HW = {"motorway", "trunk", "primary", "secondary", "tertiary",
             "residential", "living_street", "unclassified", "road"}


def fold(s: str) -> str:
    """Lowercase + strip diacritics (NFKD, drop combining marks). VN đ -> d."""
    s = s.replace("Đ", "D").replace("đ", "d")
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()


def classify(tags) -> str | None:
    if "place" in tags and tags["place"] in PLACE:
        return "place"
    if "highway" in tags and tags["highway"] in STREET_HW:
        return "street"
    for k in POI_KEYS:
        if k in tags:
            return "poi"
    if "addr:housenumber" in tags:
        return "address"
    return None


class Handler(osmium.SimpleHandler):
    def __init__(self, db: sqlite3.Connection):
        super().__init__()
        self.db = db
        self.batch: list[tuple] = []
        self.count = 0

    def _add(self, osm_id: str, tags, lat: float, lon: float):
        name = tags.get("name")
        if not name:
            return
        kind = classify(tags)
        if kind is None:
            return
        extra = tags.get("addr:city") or tags.get("addr:district") or ""
        self.batch.append((osm_id, name, fold(name), kind, lat, lon, extra))
        self.count += 1
        if len(self.batch) >= 5000:
            self._flush()

    def _flush(self):
        self.db.executemany(
            "INSERT INTO features(osm_id,name,folded,kind,lat,lon,extra) "
            "VALUES (?,?,?,?,?,?,?)", self.batch)
        self.batch.clear()

    def node(self, n):
        if n.location.valid() and len(n.tags) > 0:
            self._add(f"n{n.id}", n.tags, n.location.lat, n.location.lon)

    def way(self, w):
        if len(w.tags) == 0 or "name" not in w.tags:
            return
        # centroid of available node locations (apply_file locations=True)
        xs = ys = k = 0.0
        for node in w.nodes:
            if node.location.valid():
                xs += node.location.lon
                ys += node.location.lat
                k += 1
        if k:
            self._add(f"w{w.id}", w.tags, ys / k, xs / k)


def build(pbf: str, out: str) -> int:
    db = sqlite3.connect(out)
    db.executescript("""
        DROP TABLE IF EXISTS features;
        CREATE TABLE features(
          id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,
          kind TEXT, lat REAL, lon REAL, extra TEXT);
    """)
    h = Handler(db)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    h._flush()
    db.commit()

    # Rank: places first, then streets, then POIs/addresses (for result ordering)
    db.executescript("""
        CREATE INDEX idx_folded ON features(folded);
        CREATE VIRTUAL TABLE features_fts USING fts5(
          folded, content='features', content_rowid='id', tokenize='unicode61');
        INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features;
        CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
        INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features;
    """)
    db.commit()
    db.execute("PRAGMA optimize")
    db.close()
    return h.count


if __name__ == "__main__":
    pbf, out = sys.argv[1], sys.argv[2]
    print(f">> indexing {pbf} -> {out}")
    total = build(pbf, out)
    print(f">> done: {total} named features indexed")
