"""Fleet store: GPS telemetry, geofence zones (point-in-polygon), map-matched mileage."""
import importlib
import os

from fastapi.testclient import TestClient


def load(tmp_path, api_key="secret"):
    os.environ["API_KEY"] = api_key
    os.environ["FLEET_DB"] = str(tmp_path / "fleet.db")
    import app.main as m
    importlib.reload(m)
    return m


# a ~2km box around HCMC centre, with one point inside and one outside
BOX = {"type": "Polygon", "coordinates": [[
    [106.69, 10.77], [106.71, 10.77], [106.71, 10.79], [106.69, 10.79], [106.69, 10.77]]]}
INSIDE = (10.78, 106.70)
OUTSIDE = (10.78, 106.72)


def test_point_in_geometry_polygon_hole_multipolygon():
    from app import fleet
    assert fleet.point_in_geometry(10.78, 106.70, BOX)
    assert not fleet.point_in_geometry(10.78, 106.72, BOX)
    # polygon with a hole: point in the hole is OUTSIDE
    holed = {"type": "Polygon", "coordinates": [
        [[106.69, 10.77], [106.71, 10.77], [106.71, 10.79], [106.69, 10.79], [106.69, 10.77]],
        [[106.698, 10.778], [106.702, 10.778], [106.702, 10.782], [106.698, 10.782], [106.698, 10.778]]]}
    assert not fleet.point_in_geometry(10.780, 106.700, holed)   # inside the hole
    assert fleet.point_in_geometry(10.772, 106.695, holed)       # in the ring, not the hole
    multi = {"type": "MultiPolygon", "coordinates": [BOX["coordinates"]]}
    assert fleet.point_in_geometry(10.78, 106.70, multi)


def test_telemetry_store_latest_and_track(tmp_path):
    m = load(tmp_path)
    m.fleet.add_pings([
        ("car-1", 10.77, 106.70, 1000, 30.0, None),
        ("car-1", 10.78, 106.70, 1100, 42.0, None),   # newer -> wins as latest
        ("car-2", 10.76, 106.66, 1050, 0.0, None),
    ])
    latest = {v["vehicle_id"]: v for v in m.fleet.latest_positions()}
    assert latest["car-1"]["ts"] == 1100 and latest["car-1"]["speed"] == 42.0
    assert set(latest) == {"car-1", "car-2"}
    tr = m.fleet.track("car-1", 0, 2000)
    assert [p["ts"] for p in tr] == [1000, 1100]      # ordered


def test_ping_endpoint_and_vehicles(tmp_path):
    m = load(tmp_path)
    c = TestClient(m.app)
    r = c.post("/v1/ping", params={"key": "secret"}, json={"pings": [
        {"vehicle_id": "bike-9", "lat": 10.78, "lon": 106.70, "ts": 5000, "speed": 25},
        {"id": "bike-9", "lat": 10.79, "lon": 106.70, "ts": 5100}]})
    assert r.status_code == 200 and r.json()["stored"] == 2
    v = c.get("/v1/vehicles", params={"key": "secret"}).json()["vehicles"]
    assert len(v) == 1 and v[0]["vehicle_id"] == "bike-9" and v[0]["ts"] == 5100
    # a ping missing lat/lon is rejected
    assert c.post("/v1/ping", params={"key": "secret"}, json={"vehicle_id": "x"}).status_code == 400


def test_zones_crud_and_geofence_check(tmp_path):
    m = load(tmp_path)
    c = TestClient(m.app)
    zid = c.post("/v1/zones", params={"key": "secret"},
                 json={"name": "Khu giao Q1", "type": "allowed", "geometry": BOX,
                       "props": {"price_mult": 1.0}}).json()["id"]
    assert c.get("/v1/zones", params={"key": "secret"}).json()["zones"][0]["id"] == zid
    # point inside -> matched + inside true
    chk = c.get("/v1/zones/check", params={"key": "secret", "location": f"{INSIDE[0]},{INSIDE[1]}"}).json()
    assert chk["inside"] is True and chk["zones"][0]["name"] == "Khu giao Q1"
    assert chk["zones"][0]["props"]["price_mult"] == 1.0
    # point outside -> no match
    out = c.get("/v1/zones/check", params={"key": "secret", "location": f"{OUTSIDE[0]},{OUTSIDE[1]}"}).json()
    assert out["inside"] is False and out["zones"] == []
    # delete
    assert c.delete(f"/v1/zones/{zid}", params={"key": "secret"}).json()["deleted"] == 1
    assert c.get("/v1/zones", params={"key": "secret"}).json()["zones"] == []


def test_zone_rejects_non_polygon(tmp_path):
    m = load(tmp_path)
    c = TestClient(m.app)
    r = c.post("/v1/zones", params={"key": "secret"},
               json={"name": "x", "geometry": {"type": "Point", "coordinates": [106.7, 10.78]}})
    assert r.status_code == 400


def test_mileage_map_matches_track(tmp_path, monkeypatch):
    m = load(tmp_path)

    async def fake_trace_route(path, payload):
        from app.polyline import encode
        shape6 = encode([(10.772, 106.698), (10.795, 106.722)], precision=6)
        return {"trip": {"status": 0, "legs": [
            {"summary": {"length": 5.4, "time": 540}, "shape": shape6}],
            "summary": {"length": 5.4, "time": 540}}}

    monkeypatch.setattr(m, "valhalla", fake_trace_route)
    m.fleet.add_pings([("car-7", 10.772, 106.698, 1000, 20.0, None),
                       ("car-7", 10.780, 106.705, 1060, 25.0, None),
                       ("car-7", 10.795, 106.722, 1120, 18.0, None)])
    c = TestClient(m.app)
    r = c.get("/v1/vehicles/car-7/mileage", params={"key": "secret"}).json()
    assert r["status"] == "OK" and r["distance"]["value"] == 5400      # 5.4 km map-matched
    assert r["raw_points"] == 3 and r["polyline"]["points"]
    # too few points -> zero
    z = c.get("/v1/vehicles/ghost/mileage", params={"key": "secret"}).json()
    assert z["status"] == "ZERO_RESULTS" and z["distance"]["value"] == 0
