"""Lightweight VN geocoder service (Phase 3, internal-use).

Serves typeahead + geocode + reverse from the SQLite index built by importer.py.
Diacritic-folded matching ('nguyen' -> 'Nguyễn'). "Good enough," not Photon.

  GET /healthz
  GET /autocomplete?q=...&limit=8
  GET /geocode?q=...&limit=5
  GET /reverse?lat=..&lon=..
"""
from __future__ import annotations

import math
import os
import sqlite3
import unicodedata

from fastapi import FastAPI, Query

DB_PATH = os.environ.get("GEOCODER_DB", "/data/geocoder.db")
KIND_RANK = {"place": 0, "street": 1, "poi": 2, "address": 3}

app = FastAPI(title="NullMaps Geocoder", version="0.3.0")
_conn: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def fold(s: str) -> str:
    s = s.replace("Đ", "D").replace("đ", "d")
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()


def fts_match(q: str) -> str:
    """Build a prefix FTS5 query: each token becomes token* (typeahead)."""
    toks = [t for t in fold(q).replace('"', " ").split() if t]
    return " ".join(f'"{t}"*' for t in toks)


def search(q: str, limit: int) -> list[dict]:
    match = fts_match(q)
    if not match:
        return []
    rows = db().execute(
        """SELECT f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                  bm25(features_fts) AS r
           FROM features_fts JOIN features f ON f.id = features_fts.rowid
           WHERE features_fts MATCH ?
           ORDER BY r LIMIT ?""",
        (match, limit * 8),
    ).fetchall()
    qf = fold(q)
    out = [dict(r) for r in rows]
    # Rank: exact folded match, then prefix match, then place>street>poi, then bm25.
    # Lifts "Bến Thành" (== query) above fuzzy "Thạnh Bền".
    out.sort(key=lambda x: (
        0 if x["folded"] == qf else 1,
        0 if x["folded"].startswith(qf) else 1,
        KIND_RANK.get(x["kind"], 9),
        x["r"],
    ))
    for x in out:
        x.pop("folded", None)
    return out[:limit]


def haversine(a_lat, a_lon, b_lat, b_lon) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


@app.get("/healthz")
def healthz() -> dict:
    try:
        n = db().execute("SELECT count(*) FROM features").fetchone()[0]
        return {"status": "ok", "service": "nullmaps-geocoder", "features": n}
    except sqlite3.Error as e:
        return {"status": "error", "detail": str(e)}


@app.get("/autocomplete")
def autocomplete(q: str = Query(...), limit: int = 8) -> dict:
    return {"query": q, "results": search(q, min(limit, 20))}


@app.get("/geocode")
def geocode(q: str = Query(...), limit: int = 5) -> dict:
    return {"query": q, "results": search(q, min(limit, 20))}


@app.get("/reverse")
def reverse(lat: float = Query(...), lon: float = Query(...)) -> dict:
    # expand the R*Tree window until we have candidates, then nearest by haversine
    for d in (0.005, 0.02, 0.08, 0.3):
        rows = db().execute(
            """SELECT f.osm_id, f.name, f.kind, f.lat, f.lon, f.extra
               FROM features_rtree t JOIN features f ON f.id = t.id
               WHERE t.minlat BETWEEN ? AND ? AND t.minlon BETWEEN ? AND ?""",
            (lat - d, lat + d, lon - d, lon + d),
        ).fetchall()
        if rows:
            best = min(rows, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))
            b = dict(best)
            b["distance_m"] = round(haversine(lat, lon, b["lat"], b["lon"]), 1)
            return {"lat": lat, "lon": lon, "result": b}
    return {"lat": lat, "lon": lon, "result": None}
