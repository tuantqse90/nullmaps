"""Phase-③b routing depth: costing knobs, avoid-zones, snap, matrix addresses."""
import importlib
import os

from fastapi.testclient import TestClient


def load(api_key="secret"):
    os.environ["API_KEY"] = api_key
    import app.main as m
    importlib.reload(m)
    return m


async def fake_route(path, payload):
    from app.polyline import encode
    shape6 = encode([(10.77, 106.69), (10.79, 106.72)], precision=6)
    return {"trip": {"status": 0, "locations": [{"lat": 10.77, "lon": 106.69},
                                                {"lat": 10.79, "lon": 106.72}],
                     "legs": [{"summary": {"length": 5.4, "time": 540}, "shape": shape6}]}}


def _capture(m, fake):
    seen = {}

    async def cap(path, payload):
        seen["path"] = path
        seen["payload"] = payload
        return await fake(path, payload)

    m_attr = "valhalla"
    setattr(m, m_attr, cap)
    return seen


def test_costing_knobs_forwarded(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72",
        "use_highways": "0", "use_ferry": "0.2", "top_speed": "40", "key": "secret"})
    co = seen["payload"]["costing_options"]["motor_scooter"]
    assert co["use_highways"] == 0.0
    assert co["use_ferry"] == 0.2
    assert co["top_speed"] == 40.0


def test_use_knob_clamped(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72",
        "use_tolls": "5", "key": "secret"})        # >1 clamps to 1.0
    assert seen["payload"]["costing_options"]["motor_scooter"]["use_tolls"] == 1.0


def test_truck_dims_still_merge(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72",
        "mode": "truck", "height": "3.5", "use_tolls": "0", "key": "secret"})
    co = seen["payload"]["costing_options"]["truck"]
    assert co["height"] == 3.5 and co["use_tolls"] == 0.0


import json as _json


def test_avoid_zones_polygon_forwarded(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    poly = _json.dumps({"type": "Polygon",
                        "coordinates": [[[106.6, 10.7], [106.7, 10.7], [106.7, 10.8], [106.6, 10.7]]]})
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72",
        "avoid_zones": poly, "key": "secret"})
    ep = seen["payload"]["exclude_polygons"]
    assert ep == [[[106.6, 10.7], [106.7, 10.7], [106.7, 10.8], [106.6, 10.7]]]


def test_avoid_zones_malformed_ignored(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72",
        "avoid_zones": "not-json", "key": "secret"})
    assert "exclude_polygons" not in seen["payload"]
