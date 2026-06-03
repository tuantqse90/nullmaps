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
import json
import os
import sqlite3
import tempfile

from indexops import build_trigrams, insert_legacy_districts, merge_streets
from app.main import fold


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


def test_merge_streets_collapses_close_keeps_distant_separate():
    # Merge is by name + ~0.1° (~11 km) grid: nearby same-name segments collapse, but a
    # same-named street in another province stays SEPARATE (the Nguyễn-Duy-Trinh bug).
    con = _seed([
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.00, "lon": 106.00, "importance": 10},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.04, "lon": 106.03, "importance": 12},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 11.50, "lon": 107.20, "importance": 8},  # far away
        {"name": "Chợ Bến Thành", "folded": "cho ben thanh", "kind": "poi", "lat": 10.0, "lon": 106.0},
    ])
    merge_streets(con)
    streets = con.execute("SELECT lat, lon, importance FROM features WHERE kind='street' ORDER BY lat").fetchall()
    assert len(streets) == 2                              # close pair merged; distant one kept
    near = streets[0]
    assert abs(near["lat"] - 10.02) < 1e-9                # averaged 10.00 & 10.04
    assert near["importance"] == 12                       # max within the cluster
    assert any(abs(s["lat"] - 11.50) < 1e-9 for s in streets)  # distant same-name street survives
    assert con.execute("SELECT count(*) FROM features WHERE kind='poi'").fetchone()[0] == 1  # poi untouched


def test_merge_streets_safe_before_virtual_tables():
    """Regression: merge_streets must be called BEFORE features_fts / features_rtree exist.

    This test mirrors the importer.build() calling order: merge -> create virtual
    tables -> build_trigrams. Verifies that FTS queries succeed and the R*Tree
    contains no phantom entries (rows deleted by merge must never appear in the index).
    """
    con = _seed([
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.00, "lon": 106.00, "district": "Quan 1", "importance": 10},
        {"name": "Lê Lợi", "folded": "le loi", "kind": "street", "lat": 10.04, "lon": 106.03, "district": "Quan 1", "importance": 12},
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


def test_importance_population_is_log_scaled():
    import pytest
    pytest.importorskip("osmium")                 # importer imports osmium at module top
    from importer import importance
    # Use populations all below the cap boundary (100K → log10*8=40 hits cap).
    # 50000 → ~38, 5000 → ~30, 500 → ~22 — all distinct and below cap=40.
    big = importance({"place": "city", "population": "50000"}, "place")
    mid = importance({"place": "city", "population": "5000"}, "place")
    small = importance({"place": "city", "population": "500"}, "place")
    assert big > mid > small                       # smooth, not digit-count ties


def test_insert_legacy_districts_idempotent():
    con = _seed([])
    data = [{"name": "Quận 1", "lat": 10.776, "lon": 106.701, "city": "TP HCM"},
            {"name": "Bình Thạnh", "lat": 10.81, "lon": 106.709, "city": "TP HCM"}]
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        n1 = insert_legacy_districts(con, path, fold)
        n2 = insert_legacy_districts(con, path, fold)   # run twice -> idempotent
    finally:
        os.unlink(path)
    assert n1 == 2 and n2 == 2
    rows = con.execute(
        "SELECT name, folded, kind, importance FROM features WHERE category='legacy_district'"
    ).fetchall()
    assert len(rows) == 2                               # not duplicated
    q1 = [r for r in rows if r["folded"] == "quan 1"][0]
    assert q1["name"] == "Quận 1" and q1["kind"] == "boundary" and q1["importance"] == 60
