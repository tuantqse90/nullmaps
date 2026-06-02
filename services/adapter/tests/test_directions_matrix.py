"""Phase-4 adapter: Directions + Distance Matrix shape mapping (Valhalla mocked),
geocode/autocomplete pending, and a polyline precision roundtrip."""
import importlib
import os

from fastapi.testclient import TestClient
import pytest


def load(api_key="secret"):
    os.environ["API_KEY"] = api_key
    import app.main as m
    importlib.reload(m)
    return m


# --- canned Valhalla responses -------------------------------------------------
async def fake_route(path, payload):
    # one leg, ~5.4km / 540s, trivial 2-point shape at precision 6
    from app.polyline import encode
    shape6 = encode([(10.7725, 106.6980), (10.7951, 106.7218)], precision=6)
    return {"trip": {"status": 0, "legs": [
        {"summary": {"length": 5.4, "time": 540}, "shape": shape6}]}}


async def fake_matrix(path, payload):
    return {"sources_to_targets": [
        [{"distance": 2.761, "time": 300}, {"distance": None}],
    ]}


def test_directions_maps_to_google_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_route)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json",
              params={"origin": "10.7725,106.6980", "destination": "10.7951,106.7218",
                      "key": "secret"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "OK"
    leg = b["routes"][0]["legs"][0]
    assert leg["distance"]["value"] == 5400
    assert leg["duration"]["value"] == 540
    assert leg["distance"]["text"] == "5.4 km"
    assert b["routes"][0]["overview_polyline"]["points"]  # non-empty polyline5


def test_directions_default_is_motorbike(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    assert seen["costing"] == "motor_scooter"  # motorbike-first default


def test_directions_mode_driving_maps_to_auto(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72",
                  "mode": "driving", "key": "secret"})
    assert seen["costing"] == "auto"


def test_matrix_maps_and_handles_null(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_matrix)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json",
              params={"origins": "10.77,106.69", "destinations": "10.76,106.68|10.80,106.71",
                      "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    els = b["rows"][0]["elements"]
    assert els[0]["status"] == "OK" and els[0]["distance"]["value"] == 2761
    assert els[1]["status"] == "ZERO_RESULTS"


def test_directions_requires_key():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json",
              params={"origin": "10.77,106.69", "destination": "10.79,106.72"})
    assert r.status_code == 403


async def fake_geocode(path, params):
    return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                         "lat": 10.7704, "lon": 106.6951, "extra": "HCMC"}]}


def test_geocode_forward_maps_to_google_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_geocode)
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "ben thanh", "key": "secret"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "OK"
    g = b["results"][0]
    assert g["geometry"]["location"] == {"lat": 10.7704, "lng": 106.6951}
    assert "Bến Thành" in g["formatted_address"]


def test_geocode_requires_key():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "X"})
    assert r.status_code == 403


def test_polyline_roundtrip():
    from app.polyline import decode, encode
    pts = [(10.7725, 106.6980), (10.7951, 106.7218), (10.8015, 106.7106)]
    out = decode(encode(pts, precision=5), precision=5)
    for (a, b), (c, d) in zip(pts, out):
        assert abs(a - c) < 1e-4 and abs(b - d) < 1e-4


def test_directions_default_language_is_vietnamese(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    assert seen["directions_options"]["language"] == "vi-VN"


def test_directions_language_override_en(monkeypatch):
    m = load()
    seen = {}

    async def capture(path, payload):
        seen.update(payload)
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", capture)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json",
          params={"origin": "10.77,106.69", "destination": "10.79,106.72",
                  "language": "en", "key": "secret"})
    assert seen["directions_options"]["language"] == "en-US"
