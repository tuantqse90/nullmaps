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
