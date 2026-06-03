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

import math
import os
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


# Higher = more prominent. Drives forward-geocode ranking so a name shared by
# many places resolves to the most important one (e.g. the big-city street).
PLACE_WEIGHT = {"city": 100, "municipality": 95, "town": 70, "borough": 60,
                "suburb": 50, "quarter": 40, "village": 40, "neighbourhood": 30,
                "hamlet": 20, "locality": 15}

# Admin boundary prominence by OSM admin_level (VN: 4=province/city, 6=district, 8=ward).
ADMIN_WEIGHT = {"4": 80, "6": 50, "8": 30}


def importance(tags, kind: str) -> int:
    score = {"place": 30, "street": 10, "poi": 5, "address": 3}.get(kind, 0)
    if kind == "place":
        score = max(score, PLACE_WEIGHT.get(tags.get("place", ""), 20))
    pop_raw = tags.get("population", "").replace(".", "").replace(",", "")
    if pop_raw.isdigit():
        score += min(40, round(math.log10(max(int(pop_raw), 1)) * 8))
    if "wikidata" in tags or "wikipedia" in tags:
        score += 20          # documented => notable
    if tags.get("capital") in ("yes", "2", "3", "4"):
        score += 25
    return score


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
        # category = the meaningful OSM class (restaurant/fuel/...) for nearby search
        cat = ""
        for k in POI_KEYS:
            if k in tags:
                cat = tags[k]
                break
        if not cat:
            cat = tags.get("place") or tags.get("highway") or ""
        housenumber = tags.get("addr:housenumber", "")
        street = tags.get("addr:street", "")
        city = tags.get("addr:city", "")
        district = tags.get("addr:district") or tags.get("addr:subdistrict", "")
        region = tags.get("addr:province") or tags.get("addr:state", "")
        extra = city or district or ""
        self.batch.append((osm_id, name, fold(name), kind, lat, lon, extra,
                           importance(tags, kind), cat, housenumber, street, city, district, region))
        self.count += 1
        if len(self.batch) >= 5000:
            self._flush()

    def _flush(self):
        self.db.executemany(
            "INSERT INTO features(osm_id,name,folded,kind,lat,lon,extra,importance,"
            "category,housenumber,street,city,district,region) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.batch)
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

    def area(self, a):
        if a.tags.get("boundary") != "administrative":
            return
        lvl = a.tags.get("admin_level")
        name = a.tags.get("name")
        if lvl not in ADMIN_WEIGHT or not name:
            return
        # representative point = center of the outer-ring bounding box
        minlat = minlon = 1e9
        maxlat = maxlon = -1e9
        for outer in a.outer_rings():
            for node in outer:
                lat, lon = node.location.lat, node.location.lon  # match the proven way() handler
                minlat, maxlat = min(minlat, lat), max(maxlat, lat)
                minlon, maxlon = min(minlon, lon), max(maxlon, lon)
        if minlat > maxlat:
            return
        oid = f"{'w' if a.from_way() else 'r'}{a.orig_id()}"
        self.batch.append((oid, name, fold(name), "boundary",
                           (minlat + maxlat) / 2, (minlon + maxlon) / 2, name,
                           ADMIN_WEIGHT[lvl], "admin_level_" + lvl, "", "", "", "", ""))
        self.count += 1
        if len(self.batch) >= 5000:
            self._flush()


def build(pbf: str, out: str) -> int:
    db = sqlite3.connect(out)
    db.executescript("""
        DROP TABLE IF EXISTS features_fts;
        DROP TABLE IF EXISTS features_rtree;
        DROP TABLE IF EXISTS features;
        CREATE TABLE features(
          id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,
          kind TEXT, lat REAL, lon REAL, extra TEXT, importance INTEGER DEFAULT 0,
          category TEXT, housenumber TEXT, street TEXT, city TEXT, district TEXT, region TEXT);
    """)
    h = Handler(db)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    h._flush()
    db.commit()

    from indexops import merge_streets, build_trigrams, insert_legacy_districts
    # merge_streets must run BEFORE the CREATE VIRTUAL TABLE statements below.
    # FTS5 content tables and R*Tree virtual tables reference features rows by id;
    # deleting rows after they are indexed causes "fts5: missing row N" errors and
    # leaves phantom entries in features_rtree that inflate reverse-geocode results.
    merge_streets(db)
    db.commit()

    # Legacy pre-2025 districts (Quận/Huyện) so colloquial "Quận 1" still resolves.
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy_districts.json")
    if os.path.exists(legacy):
        print(f">> inserted {insert_legacy_districts(db, legacy, fold)} legacy districts")
        db.commit()

    # Rank: places first, then streets, then POIs/addresses (for result ordering)
    db.executescript("""
        CREATE INDEX idx_folded ON features(folded);
        CREATE INDEX idx_category ON features(category);
        CREATE INDEX idx_osmid ON features(osm_id);
        CREATE VIRTUAL TABLE features_fts USING fts5(
          folded, content='features', content_rowid='id', tokenize='unicode61');
        INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features;
        CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
        INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features;
    """)
    build_trigrams(db)             # hand-built trigram-similarity index
    db.commit()
    db.execute("PRAGMA optimize")
    db.close()
    return h.count


if __name__ == "__main__":
    pbf, out = sys.argv[1], sys.argv[2]
    print(f">> indexing {pbf} -> {out}")
    total = build(pbf, out)
    print(f">> done: {total} named features indexed")
