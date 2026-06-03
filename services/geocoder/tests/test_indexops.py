"""Pure SQL index-op tests (no osmium): trigram build + street merge."""
import sqlite3

from indexops import build_trigrams, merge_streets


def _seed(rows):
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
