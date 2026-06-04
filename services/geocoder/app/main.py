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

from app.vnorm import normalize_query, trigrams

DB_PATH = os.environ.get("GEOCODER_DB", "/data/geocoder.db")
KIND_RANK = {"place": 0, "boundary": 1, "street": 2, "poi": 3, "address": 4}
REVERSE_MAX_M = float(os.environ.get("GEOCODER_REVERSE_MAX_M", "5000"))

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
    """Build a prefix FTS5 query from the VN-normalized, folded query."""
    toks = [t for t in normalize_query(fold(q)).replace('"', " ").split() if t]
    return " ".join(f'"{t}"*' for t in toks)


# Bias strength: penalty (in "importance points") per km from the bias point.
# ~4 means a result 25 km away loses ~100 pts — roughly one whole city tier.
BIAS_PER_KM = 4.0


def _trigram_fallback(qn: str, limit: int,
                      bias: tuple[float, float] | None) -> list[dict]:
    """Fuzzy fallback when strict FTS returns nothing: rank candidate folded
    strings by trigram Jaccard similarity, then importance."""
    Q = trigrams(qn)
    if not Q:
        return []
    ph = ",".join("?" * len(Q))
    cand = db().execute(
        f"SELECT folded, COUNT(*) AS shared FROM trgm WHERE g IN ({ph}) "
        "GROUP BY folded ORDER BY shared DESC LIMIT 50", tuple(Q)).fetchall()
    keep: dict[str, float] = {}
    for r in cand:
        denom = len(Q) + len(trigrams(r["folded"])) - r["shared"]
        jac = r["shared"] / denom if denom else 0.0
        if jac >= 0.3:
            keep[r["folded"]] = jac
    if not keep:
        return []
    ph2 = ",".join("?" * len(keep))
    rows = db().execute(
        f"""SELECT f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                   f.importance, f.category, f.housenumber, f.street, f.city,
                   f.district, f.region
            FROM features f WHERE f.folded IN ({ph2})""", tuple(keep)).fetchall()
    out = [dict(r) for r in rows]

    def rank(x) -> tuple:
        penalty = 0.0
        if bias is not None:
            penalty = haversine(bias[0], bias[1], x["lat"], x["lon"]) / 1000.0 * BIAS_PER_KM
        return (-keep.get(x["folded"], 0.0), -(x["importance"]) + penalty)

    out.sort(key=rank)
    return out[:limit]


def search(q: str, limit: int, bias: tuple[float, float] | None = None) -> list[dict]:
    match = fts_match(q)
    if not match:
        return []
    rows = db().execute(
        """SELECT f.id, f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                  f.importance, f.category, f.housenumber, f.street, f.city,
                  f.district, f.region, bm25(features_fts) AS r
           FROM features_fts JOIN features f ON f.id = features_fts.rowid
           WHERE features_fts MATCH ?
           ORDER BY r LIMIT ?""",
        (match, limit * 12),
    ).fetchall()
    qn = normalize_query(fold(q))
    out = [dict(r) for r in rows]
    # The bm25-ranked FTS window can omit the EXACT-name match when the query tokens are
    # very common (e.g. "binh thanh" prefix-matches every "Thanh Bình" too), pushing a
    # legacy district / exact place past the limit so it never reaches rank(). Pull exact
    # folded matches in unconditionally — there are few, and they are the best answers.
    seen = {x["id"] for x in out}
    for r in db().execute(
        """SELECT f.id, f.osm_id, f.name, f.folded, f.kind, f.lat, f.lon, f.extra,
                  f.importance, f.category, f.housenumber, f.street, f.city,
                  f.district, f.region, 0.0 AS r
           FROM features f WHERE f.folded = ? LIMIT 50""", (qn,)).fetchall():
        if r["id"] not in seen:
            out.append(dict(r))
            seen.add(r["id"])
    if not out and len(qn) >= 3:
        out = _trigram_fallback(qn, limit, bias)
        for x in out:
            x.pop("folded", None)
            x.pop("importance", None)
        return out

    def rank(x) -> tuple:
        penalty = 0.0
        if bias is not None:
            penalty = haversine(bias[0], bias[1], x["lat"], x["lon"]) / 1000.0 * BIAS_PER_KM
        # exact match first, then prefix, then legacy district (so a colloquial
        # "Bình Thạnh"/"Quận 1" wins a fold-collision with a same-name commune),
        # then most-prominent-and-nearest, then bm25
        return (
            0 if x["folded"] == qn else 1,
            0 if x["folded"].startswith(qn) else 1,
            0 if x.get("category") == "legacy_district" else 1,
            -(x["importance"]) + penalty,
            x["r"],
        )

    out.sort(key=rank)
    for x in out:
        x.pop("id", None)
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
    # expand the window up to the cap (~5 km ≈ 0.05°); within each band keep only
    # candidates inside REVERSE_MAX_M, then prefer street/place/boundary over a POI
    # at similar distance. Refuse anything beyond the cap.
    for d in (0.005, 0.02, 0.05):
        rows = db().execute(
            f"""SELECT {_FIELDS}
               FROM features_rtree t JOIN features f ON f.id = t.id
               WHERE t.minlat BETWEEN ? AND ? AND t.minlon BETWEEN ? AND ?""",
            (lat - d, lat + d, lon - d, lon + d),
        ).fetchall()
        cands = []
        for r in rows:
            dist = haversine(lat, lon, r["lat"], r["lon"])
            if dist <= REVERSE_MAX_M:
                cands.append((r, dist))
        if cands:
            best, dist = min(
                cands, key=lambda rd: (round(rd[1] / 100), KIND_RANK.get(rd[0]["kind"], 9), rd[1]))
            b = dict(best)
            b["distance_m"] = round(dist, 1)
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
