"""Search/reverse tests against a seeded in-memory SQLite (no osmium)."""
import sqlite3

import pytest

import app.main as m
from app.vnorm import trigrams

SCHEMA = """
CREATE TABLE features(
  id INTEGER PRIMARY KEY, osm_id TEXT, name TEXT, folded TEXT,
  kind TEXT, lat REAL, lon REAL, extra TEXT, importance INTEGER DEFAULT 0,
  category TEXT, housenumber TEXT, street TEXT, city TEXT, district TEXT, region TEXT);
CREATE VIRTUAL TABLE features_fts USING fts5(folded, content='features', content_rowid='id', tokenize='unicode61');
CREATE VIRTUAL TABLE features_rtree USING rtree(id, minlat, maxlat, minlon, maxlon);
CREATE TABLE trgm(g TEXT, folded TEXT);
"""


def make_db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for i, r in enumerate(rows, 1):
        con.execute(
            "INSERT INTO features(id,osm_id,name,folded,kind,lat,lon,extra,importance,"
            "category,housenumber,street,city,district,region)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, r.get("osm_id", f"n{i}"), r["name"], m.fold(r["name"]), r["kind"], r["lat"], r["lon"],
             r.get("extra", ""), r.get("importance", 0), r.get("category", ""), "", "",
             r.get("city", ""), r.get("district", ""), ""))
    con.execute("INSERT INTO features_fts(rowid, folded) SELECT id, folded FROM features")
    con.execute("INSERT INTO features_rtree SELECT id, lat, lat, lon, lon FROM features")
    for row in con.execute("SELECT DISTINCT folded FROM features").fetchall():
        for g in trigrams(row["folded"]):
            con.execute("INSERT INTO trgm(g, folded) VALUES (?, ?)", (g, row["folded"]))
    con.commit()
    return con


@pytest.fixture
def seeded(monkeypatch):
    con = make_db([
        {"name": "Nguyễn Huệ", "kind": "street", "lat": 10.7740, "lon": 106.7040, "importance": 12},
        {"name": "Lê Lợi", "kind": "street", "lat": 10.7720, "lon": 106.6990, "importance": 11},
        {"name": "Quận 1", "kind": "boundary", "lat": 10.7700, "lon": 106.7000, "importance": 50},
        {"name": "Chợ Bến Thành", "kind": "poi", "lat": 10.7725, "lon": 106.6980, "importance": 5},
    ])
    monkeypatch.setattr(m, "_conn", con)
    return con


def test_typo_falls_back_to_trigram(seeded):
    res = m.search("nguyn hue", 5)          # internal typo, strict FTS returns 0
    assert res and res[0]["name"] == "Nguyễn Huệ"


def test_clean_query_does_not_need_fallback(seeded):
    res = m.search("le loi", 5)
    assert res and res[0]["name"] == "Lê Lợi"


def test_reverse_prefers_street_over_colocated_poi(seeded):
    # Bến Thành POI and Lê Lợi street are ~co-located; prefer the street.
    r = m.reverse(10.7721, 106.6991)["result"]
    assert r is not None and r["kind"] in ("street", "boundary", "place")


def test_reverse_returns_none_beyond_cap(monkeypatch):
    con = make_db([{"name": "Far POI", "kind": "poi", "lat": 21.0, "lon": 105.8}])  # Hanoi
    monkeypatch.setattr(m, "_conn", con)
    monkeypatch.setattr(m, "REVERSE_MAX_M", 5000.0)
    assert m.reverse(10.77, 106.70)["result"] is None    # ~1100 km away -> refused


def test_q1_resolves_to_legacy_district(monkeypatch):
    # Legacy "Quận 1" (importance 60) must outrank a co-named POI "Quán 19".
    con = make_db([
        {"name": "Quận 1", "kind": "boundary", "lat": 10.776, "lon": 106.701, "importance": 60},
        {"name": "Quán 19", "kind": "poi", "lat": 10.78, "lon": 106.70, "importance": 5},
        {"name": "Quán 187", "kind": "poi", "lat": 10.79, "lon": 106.69, "importance": 5},
    ])
    monkeypatch.setattr(m, "_conn", con)
    res = m.search("q1", 5)
    assert res and res[0]["name"] == "Quận 1"


def test_legacy_district_wins_fold_collision(monkeypatch):
    # "Bình Thạnh" (legacy district, imp 60) and "Bình Thành" (commune, imp 70) both
    # fold to "binh thanh"; the legacy district must win despite lower importance.
    con = make_db([
        {"name": "Bình Thành", "kind": "boundary", "lat": 10.5, "lon": 105.5, "importance": 70, "category": "admin_level_6"},
        {"name": "Bình Thạnh", "kind": "boundary", "lat": 10.81, "lon": 106.709, "importance": 60, "category": "legacy_district"},
    ])
    monkeypatch.setattr(m, "_conn", con)
    res = m.search("binh thanh", 5)
    assert res and res[0]["name"] == "Bình Thạnh"


def test_exact_match_recovered_when_pushed_out_of_fts_window(monkeypatch):
    # "binh"* "thanh"* prefix-matches every "Thanh Bình" too. Inserting 40 such decoys
    # FIRST (lower rowids) pushes the exact "Bình Thạnh" past the bm25 LIMIT (limit*12)
    # window — only the exact-folded supplement makes it reachable for the tie-break.
    rows = [{"name": "Thanh Bình", "kind": "poi", "lat": 16.0 + i * 0.01, "lon": 108.0,
             "importance": 40, "category": "village"} for i in range(40)]
    rows.append({"name": "Bình Thạnh", "kind": "boundary", "lat": 10.81, "lon": 106.709,
                 "importance": 60, "category": "legacy_district"})
    con = make_db(rows)
    monkeypatch.setattr(m, "_conn", con)
    res = m.search("bình thạnh", 3)                      # window = 36 < 41 candidates
    assert res and res[0]["name"] == "Bình Thạnh"
    assert res[0]["category"] == "legacy_district"
