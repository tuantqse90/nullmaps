"""Pure SQL index-op tests (no osmium): trigram build + street merge.

Calling order invariant (mirrors importer.build()):
  1. merge_streets(con)   — must run BEFORE virtual tables exist
  2. CREATE VIRTUAL TABLE features_fts / features_rtree
  3. build_trigrams(con)  — runs after rows are stable

The _seed() fixture intentionally does NOT create features_fts / features_rtree
so that test_merge_streets_collapses_same_name_within_area validates the
correct pre-index state. test_merge_streets_safe_before_virtual_tables explicitly
verifies that merge_streets works with only the base features table and that a
subsequent FTS build over the merged rows succeeds (no phantom-row corruption).
"""
import sqlite3

from indexops import build_trigrams, merge_streets


def _seed(rows):
    """Seed a bare features table (no virtual tables) — the state importer.build()
    has immediately after h._flush()/db.commit() and before the CREATE VIRTUAL TABLE
    block. merge_streets must be called in this window."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE features(id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,"
        " kind TEXT, lat REAL, lon REAL, extra TEXT, importance INTEGER DEFAULT 0,"
        " category TEXT, housenumber TEXT, street TEXT, city TEXT, district TEXT, region TEXT)")
    for i, r in enumerate(rows, 1):
        con.execute(
            "INSERT INTO features(id,osm_id,name,folded,kind,lat,lon,importance,district,city)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, f"n{i}", r["name"], r["folded"], r["kind"], r["lat"], r["lon"],
             r.get("importance", 0), r.get("district", ""), r.get("city", "")))
    con.commit()
    return con


def test_build_trigrams_populates_distinct_folded():
    con = _seed([{"name": "Nguyễn Huệ", "folded": "nguyen hue", "kind": "street", "lat": 10.0, "lon": 106.0},
                 {"name": "Nguyễn Huệ", "folded": "nguyen hue", "kind": "street", "lat": 10.1, "lon": 106.1}])
    build_trigrams(con)
    foldeds = {r[0] for r in con.execute("SELECT DISTINCT folded FROM trgm").fetchall()}
    assert foldeds == {"nguyen hue"}                      # deduped
    # the trigram 'ngu' maps to the folded string
    assert con.execute("SELECT count(*) FROM trgm WHERE g='ngu'").fetchone()[0] == 1


def test_merge_streets_collapses_same_name_within_area():
    con = _seed([
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.0, "lon": 106.0, "district": "Quan 1", "importance": 10},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.2, "lon": 106.2, "district": "Quan 1", "importance": 12},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 11.0, "lon": 107.0, "district": "Quan 3", "importance": 8},
        {"name": "Chợ Bến Thành", "folded": "cho ben thanh", "kind": "poi", "lat": 10.0, "lon": 106.0},
    ])
    merge_streets(con)
    streets = con.execute("SELECT folded, district, lat, lon, importance FROM features WHERE kind='street' ORDER BY district").fetchall()
    assert len(streets) == 2                              # one per (folded, district)
    q1 = [s for s in streets if s["district"] == "Quan 1"][0]
    assert abs(q1["lat"] - 10.1) < 1e-9                   # averaged 10.0 & 10.2
    assert q1["importance"] == 12                         # max
    assert con.execute("SELECT count(*) FROM features WHERE kind='poi'").fetchone()[0] == 1  # poi untouched


def test_merge_streets_safe_before_virtual_tables():
    """Regression: merge_streets must be called BEFORE features_fts / features_rtree exist.

    This test mirrors the importer.build() calling order: merge -> create virtual
    tables -> build_trigrams. Verifies that FTS queries succeed and the R*Tree
    contains no phantom entries (rows deleted by merge must never appear in the index).
    """
    con = _seed([
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.0, "lon": 106.0, "district": "Quan 1", "importance": 10},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.2, "lon": 106.2, "district": "Quan 1", "importance": 12},
    ])
    # Step 1: merge BEFORE creating virtual tables (the correct order)
    merge_streets(con)
    con.commit()

    # Step 2: create virtual tables over the already-merged features table
    con.executescript("""
        CREATE VIRTUAL TABLE features_fts USING fts5(
          folded, content='features', content_rowid='id', tokenize='unicode61');
        INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features;
        CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
        INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features;
    """)
    build_trigrams(con)
    con.commit()

    # FTS query must not raise "fts5: missing row N from content table"
    fts_rows = con.execute("SELECT rowid FROM features_fts WHERE folded MATCH 'le loi'").fetchall()
    assert len(fts_rows) == 1, "FTS should return exactly one merged street row"

    # R*Tree must contain no phantom entries (ids that no longer exist in features)
    feature_ids = {r[0] for r in con.execute("SELECT id FROM features").fetchall()}
    rtree_ids = {r[0] for r in con.execute("SELECT id FROM features_rtree").fetchall()}
    assert rtree_ids == feature_ids, f"Phantom R*Tree entries detected: {rtree_ids - feature_ids}"
