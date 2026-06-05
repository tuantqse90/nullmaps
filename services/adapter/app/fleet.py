"""Fleet store for the rental use case: GPS telemetry + operator geofence zones.

A small SQLite DB (one writable file, single-operator scale) the adapter owns. Telemetry
pings stream in from vehicles; zones are operator-defined polygons (allowed / restricted /
pricing areas). Pure point-in-polygon here (no GEOS dependency) — good enough for a handful
of zones checked against a point. Map-matched mileage lives in main.py (it needs Valhalla).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

DEFAULT_DB = "/fleet/fleet.db"
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    """Open (and lazily create) the fleet DB. Re-opens if FLEET_DB changed (tests)."""
    global _conn, _conn_path
    path = os.environ.get("FLEET_DB", DEFAULT_DB)
    if _conn is not None and _conn_path == path:
        return _conn
    if _conn is not None:
        _conn.close()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")        # concurrent reads while a ping writes
    c.executescript("""
        CREATE TABLE IF NOT EXISTS pings(
            vehicle_id TEXT NOT NULL, lat REAL, lon REAL, ts INTEGER,
            speed REAL, heading REAL);
        CREATE INDEX IF NOT EXISTS idx_pings_v_ts ON pings(vehicle_id, ts);
        CREATE TABLE IF NOT EXISTS zones(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, type TEXT, props TEXT, geojson TEXT NOT NULL);
    """)
    c.commit()
    _conn, _conn_path = c, path
    return c


# --- telemetry ---------------------------------------------------------------
def add_pings(rows: list[tuple]) -> int:
    """rows: (vehicle_id, lat, lon, ts, speed, heading). Returns count stored."""
    db = _db()
    with _lock:
        db.executemany(
            "INSERT INTO pings(vehicle_id,lat,lon,ts,speed,heading) VALUES (?,?,?,?,?,?)", rows)
        db.commit()
    return len(rows)


def latest_positions() -> list[dict]:
    """Most recent ping per vehicle — the live fleet map."""
    db = _db()
    rows = db.execute("""
        SELECT p.vehicle_id, p.lat, p.lon, p.ts, p.speed, p.heading FROM pings p
        JOIN (SELECT vehicle_id, MAX(ts) AS mts FROM pings GROUP BY vehicle_id) m
          ON m.vehicle_id = p.vehicle_id AND m.mts = p.ts
        ORDER BY p.vehicle_id""").fetchall()
    return [dict(r) for r in rows]


def track(vehicle_id: str, frm: int, to: int) -> list[dict]:
    """One vehicle's ordered ping track within [frm, to] (epoch seconds)."""
    db = _db()
    rows = db.execute(
        "SELECT lat,lon,ts,speed,heading FROM pings WHERE vehicle_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
        (vehicle_id, frm, to)).fetchall()
    return [dict(r) for r in rows]


# --- geofence zones ----------------------------------------------------------
def put_zone(name, ztype, geometry: dict, props: dict | None) -> int:
    db = _db()
    with _lock:
        cur = db.execute("INSERT INTO zones(name,type,props,geojson) VALUES (?,?,?,?)",
                         (name, ztype, json.dumps(props or {}), json.dumps(geometry)))
        db.commit()
        return cur.lastrowid


def list_zones() -> list[dict]:
    db = _db()
    out = []
    for r in db.execute("SELECT id,name,type,props,geojson FROM zones ORDER BY id").fetchall():
        out.append({"id": r["id"], "name": r["name"], "type": r["type"],
                    "props": json.loads(r["props"] or "{}"), "geometry": json.loads(r["geojson"])})
    return out


def delete_zone(zid: int) -> int:
    db = _db()
    with _lock:
        cur = db.execute("DELETE FROM zones WHERE id=?", (zid,))
        db.commit()
        return cur.rowcount


def zones_containing(lat: float, lon: float) -> list[dict]:
    """Zones whose polygon contains the point (geofence membership)."""
    return [z for z in list_zones() if point_in_geometry(lat, lon, z["geometry"])]


# --- point-in-polygon (ray casting; GeoJSON [lon,lat]) -----------------------
def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """polygon = [outer_ring, hole1, ...], each ring a list of [lon,lat]."""
    if not polygon or not _point_in_ring(lat, lon, polygon[0]):
        return False
    return not any(_point_in_ring(lat, lon, hole) for hole in polygon[1:])


def point_in_geometry(lat: float, lon: float, geom: dict) -> bool:
    t = (geom or {}).get("type")
    c = (geom or {}).get("coordinates")
    if t == "Polygon":
        return _point_in_polygon(lat, lon, c)
    if t == "MultiPolygon":
        return any(_point_in_polygon(lat, lon, poly) for poly in c)
    return False
