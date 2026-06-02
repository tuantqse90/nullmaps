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


# Bias strength: penalty (in "importance points") per km from the bias point.
# ~4 means a result 25 km away loses ~100 pts — roughly one whole city tier.
BIAS_PER_KM = 4.0


def search(q: str, limit: int, bias: tuple[float, float] | None = None) -> list[dict]:
    match = fts_match(q)
    if not match:
        return []
    rows = db().execute(
        """SELECT f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                  f.importance, f.category, f.housenumber, f.street, f.city,
                  f.district, f.region, bm25(features_fts) AS r
           FROM features_fts JOIN features f ON f.id = features_fts.rowid
           WHERE features_fts MATCH ?
           ORDER BY r LIMIT ?""",
        (match, limit * 12),
    ).fetchall()
    qf = fold(q)
    out = [dict(r) for r in rows]

    def rank(x) -> tuple:
        penalty = 0.0
        if bias is not None:
            penalty = haversine(bias[0], bias[1], x["lat"], x["lon"]) / 1000.0 * BIAS_PER_KM
        # exact match first, then prefix, then most-prominent-and-nearest, then bm25
        return (
            0 if x["folded"] == qf else 1,
            0 if x["folded"].startswith(qf) else 1,
            -(x["importance"]) + penalty,
            x["r"],
        )

    out.sort(key=rank)
    for x in out:
        x.pop("folded", None)
        x.pop("importance", None)
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


def _bias(lat: float | None, lon: float | None) -> tuple[float, float] | None:
    return (lat, lon) if lat is not None and lon is not None else None


@app.get("/autocomplete")
def autocomplete(q: str = Query(...), limit: int = 8,
                 lat: float | None = None, lon: float | None = None) -> dict:
    return {"query": q, "results": search(q, min(limit, 20), _bias(lat, lon))}


@app.get("/geocode")
def geocode(q: str = Query(...), limit: int = 5,
            lat: float | None = None, lon: float | None = None) -> dict:
    return {"query": q, "results": search(q, min(limit, 20), _bias(lat, lon))}


_FIELDS = ("f.osm_id, f.name, f.kind, f.lat, f.lon, f.extra, f.category, "
          "f.housenumber, f.street, f.city, f.district, f.region")


@app.get("/reverse")
def reverse(lat: float = Query(...), lon: float = Query(...)) -> dict:
    # expand the R*Tree window until we have candidates, then nearest by haversine
    for d in (0.005, 0.02, 0.08, 0.3):
        rows = db().execute(
            f"""SELECT {_FIELDS}
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


@app.get("/nearby")
def nearby(lat: float = Query(...), lon: float = Query(...),
           category: str | None = None, q: str | None = None,
           radius: int = 1500, limit: int = 20) -> dict:
    """POIs near a point, optionally filtered by category (restaurant/fuel/...)
    or a text query, sorted by distance."""
    d = min(max(radius, 100), 50000) / 111000.0  # deg per ~meter
    sql = (f"SELECT {_FIELDS} FROM features_rtree t JOIN features f ON f.id = t.id "
           "WHERE t.minlat BETWEEN ? AND ? AND t.minlon BETWEEN ? AND ?")
    args = [lat - d, lat + d, lon - d, lon + d]
    if category:
        sql += " AND f.category = ?"
        args.append(category)
    if q:
        sql += " AND f.folded LIKE ?"
        args.append(f"%{fold(q)}%")
    rows = [dict(r) for r in db().execute(sql, args).fetchall()]
    for r in rows:
        r["distance_m"] = round(haversine(lat, lon, r["lat"], r["lon"]), 1)
    rows = [r for r in rows if r["distance_m"] <= radius]
    rows.sort(key=lambda r: r["distance_m"])
    return {"lat": lat, "lon": lon, "results": rows[:min(limit, 50)]}


@app.get("/detail")
def detail(osm_id: str = Query(...)) -> dict:
    """Place details by osm_id (place_id)."""
    row = db().execute(f"SELECT {_FIELDS} FROM features f WHERE f.osm_id = ? LIMIT 1",
                       (osm_id,)).fetchone()
    return {"result": dict(row) if row else None}
