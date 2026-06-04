"""Adapter hardening: bounded rate-limit/metrics dicts, Google-shaped errors,
and the places/fleet endpoints (engines mocked)."""
import importlib
import os

from fastapi.testclient import TestClient


def load(api_key="secret", rate_per_min=None):
    os.environ["API_KEY"] = api_key
    if rate_per_min is not None:
        os.environ["RATE_LIMIT_PER_MIN"] = str(rate_per_min)
    else:
        os.environ.pop("RATE_LIMIT_PER_MIN", None)
    import app.main as m
    importlib.reload(m)
    return m


def test_rate_limit_dicts_are_bounded():
    m = load()
    c = TestClient(m.app)
    for i in range(1100):  # > maxsize (1024) distinct keys
        c.get("/maps/api/geocode/json", params={"address": "x", "key": f"k{i}"})
    assert len(m._by_key) <= 1024
    assert len(m._rl) <= 1024


def test_missing_key_returns_google_request_denied():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "x"})  # no key
    assert r.status_code == 403
    b = r.json()
    assert b["status"] == "REQUEST_DENIED"
    assert "error_message" in b


def test_bad_latlng_returns_google_invalid_request():
    m = load()
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "not-a-coord", "key": "secret"})
    assert r.status_code == 400
    assert r.json()["status"] == "INVALID_REQUEST"


def test_metrics_keeps_default_error_shape():
    m = load()
    c = TestClient(m.app)
    r = c.get("/metrics")  # not under /maps or /v1 -> default {detail}
    assert r.status_code == 403
    assert "detail" in r.json()


# --- canned engine responses ---------------------------------------------------
async def fake_reverse(path, params):
    return {"result": {"osm_id": "n9", "name": "Chợ Bến Thành", "kind": "poi",
                       "lat": 10.7725, "lon": 106.6980, "district": "Quận 1", "city": "HCMC"}}


async def fake_results(path, params):
    return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                        "lat": 10.7704, "lon": 106.6951, "extra": "HCMC",
                        "category": "marketplace", "distance_m": 120}]}


async def fake_detail(path, params):
    return {"result": {"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                       "lat": 10.7704, "lon": 106.6951}}


async def fake_isochrone(path, payload):
    return {"type": "FeatureCollection", "features": [{"type": "Feature"}]}


async def fake_trace(path, payload):
    from app.polyline import encode
    shape6 = encode([(10.77, 106.69), (10.79, 106.72)], precision=6)
    return {"trip": {"status": 0, "summary": {"length": 3.1, "time": 240},
                    "legs": [{"shape": shape6}]}}


def test_reverse_geocode_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_reverse)
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"latlng": "10.7725,106.6980", "key": "secret"})
    assert r.status_code == 200
    g = r.json()["results"][0]
    assert g["geometry"]["location"] == {"lat": 10.7725, "lng": 106.6980}


def test_autocomplete_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/autocomplete/json", params={"input": "ben thanh", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["predictions"][0]["structured_formatting"]["main_text"] == "Bến Thành"


def test_nearbysearch_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/nearbysearch/json",
              params={"location": "10.77,106.69", "radius": "500", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["results"][0]["distance_m"] == 120


def test_place_details_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "geocoder", fake_detail)
    c = TestClient(m.app)
    r = c.get("/maps/api/place/details/json", params={"place_id": "n1", "key": "secret"})
    assert r.json()["result"]["name"] == "Bến Thành"


def test_isochrone_passthrough(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_isochrone)
    c = TestClient(m.app)
    r = c.get("/v1/isochrone", params={"location": "10.77,106.69", "contours": "10", "key": "secret"})
    assert r.json()["type"] == "FeatureCollection"


def test_snap_shape(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_trace)
    c = TestClient(m.app)
    r = c.get("/v1/snap", params={"path": "10.77,106.69|10.79,106.72", "key": "secret"})
    b = r.json()
    assert b["status"] == "OK"
    assert b["distance"]["value"] == 3100
    assert b["snapped_polyline"]["points"]


def test_rate_limit_429(monkeypatch):
    m = load(rate_per_min=1)
    monkeypatch.setattr(m, "geocoder", fake_results)
    c = TestClient(m.app)
    first = c.get("/maps/api/geocode/json", params={"address": "a", "key": "secret"})
    second = c.get("/maps/api/geocode/json", params={"address": "b", "key": "secret"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["status"] == "OVER_QUERY_LIMIT"


def test_photon_feature_maps_to_internal_shape():
    m = load()
    f = {"geometry": {"coordinates": [106.7003, 10.7773]},
         "properties": {"name": "Highlands Coffee", "osm_id": 42, "osm_type": "N",
                        "osm_key": "amenity", "osm_value": "cafe",
                        "district": "Quận 1", "city": "Hồ Chí Minh", "state": "Hồ Chí Minh"}}
    r = m._photon_feature(f)
    assert r["name"] == "Highlands Coffee"
    assert (r["lat"], r["lon"]) == (10.7773, 106.7003)
    assert r["kind"] == "poi" and r["category"] == "cafe"
    assert r["extra"] == "Quận 1, Hồ Chí Minh"          # context, deduped (state == city)
    assert r["osm_id"] == "N42"


def test_photon_feature_kinds_and_address_name():
    m = load()
    street = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                                "properties": {"name": "Nguyễn Huệ", "osm_key": "highway", "osm_value": "primary"}})
    assert street["kind"] == "street"
    addr = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                              "properties": {"housenumber": "543", "street": "Nguyễn Duy Trinh"}})
    assert addr["kind"] == "address" and addr["name"] == "543 Nguyễn Duy Trinh"
    place = m._photon_feature({"geometry": {"coordinates": [1, 2]},
                               "properties": {"name": "Bến Thành", "type": "suburb"}})
    assert place["kind"] == "place"
