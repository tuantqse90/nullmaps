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


def test_geocode_caches_repeated_query(monkeypatch):
    m = load()
    calls = {"n": 0}

    async def fake_fetch(path, params):
        calls["n"] += 1
        return {"results": [{"osm_id": "n1", "name": "Bến Thành", "kind": "poi",
                             "lat": 10.7704, "lon": 106.6951}]}

    monkeypatch.setattr(m, "_geocoder_fetch", fake_fetch)
    c = TestClient(m.app)
    p = {"address": "ben thanh", "key": "secret"}
    c.get("/maps/api/geocode/json", params=p)
    c.get("/maps/api/geocode/json", params=p)
    assert calls["n"] == 1  # second request served from cache

    c.get("/maps/api/geocode/json", params={"address": "cho lon", "key": "secret"})
    assert calls["n"] == 2  # different query -> a fresh fetch


def test_metrics_requires_key():
    m = load()
    c = TestClient(m.app)
    assert c.get("/metrics").status_code == 403
    assert c.get("/metrics", params={"key": "secret"}).status_code == 200


async def fake_route_maneuvers(path, payload):
    from app.polyline import encode
    shape6 = encode([(10.7725, 106.6980), (10.7951, 106.7218)], precision=6)
    return {"trip": {"status": 0,
                     "locations": [{"lat": 10.7725, "lon": 106.6980}, {"lat": 10.7951, "lon": 106.7218}],
                     "legs": [{"summary": {"length": 5.4, "time": 540}, "shape": shape6,
                               "maneuvers": [{"type": 15, "street_names": ["Lê Thánh Tôn"],
                                              "instruction": "Turn left onto Lê Thánh Tôn",
                                              "begin_shape_index": 0, "end_shape_index": 1}]}]}}


def test_directions_steps_vietnamese_by_default(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_route_maneuvers)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json",
              params={"origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    step = r.json()["routes"][0]["legs"][0]["steps"][0]
    assert step["html_instructions"] == "Rẽ trái vào Lê Thánh Tôn"


def test_directions_steps_english_when_language_en(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_route_maneuvers)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json",
              params={"origin": "10.77,106.69", "destination": "10.79,106.72",
                      "language": "en", "key": "secret"})
    step = r.json()["routes"][0]["legs"][0]["steps"][0]
    assert step["html_instructions"] == "Turn left onto Lê Thánh Tôn"


# --- /v1/optimize (VROOM VRP) --------------------------------------------------
def test_prep_optimize_defaults_profile_and_validates():
    m = load()
    body, err = m._prep_optimize({
        "vehicles": [{"id": 1, "start": [106.70, 10.77]}, {"id": 2, "start": [106.70, 10.77], "profile": "auto"}],
        "jobs": [{"id": 1, "location": [106.71, 10.78]}],
    })
    assert err is None
    assert body["vehicles"][0]["profile"] == m.VROOM_PROFILE   # defaulted (motorbike-first)
    assert body["vehicles"][1]["profile"] == "auto"            # caller override kept
    # validation
    assert m._prep_optimize({"jobs": [{"id": 1, "location": [1, 2]}]})[1]      # no vehicles
    assert m._prep_optimize({"vehicles": [{"id": 1, "start": [1, 2]}]})[1]     # no jobs/shipments
    assert m._prep_optimize([])[1]                                            # not an object


def test_optimize_endpoint_proxies_vroom(monkeypatch):
    m = load()
    seen = {}

    async def fake_vroom(body):
        seen["body"] = body
        return {"code": 0, "summary": {"cost": 1200, "routes": 2, "unassigned": 0},
                "routes": [{"vehicle": 1, "steps": []}, {"vehicle": 2, "steps": []}],
                "unassigned": []}, 200

    monkeypatch.setattr(m, "vroom_call", fake_vroom)
    c = TestClient(m.app)
    r = c.post("/v1/optimize", params={"key": "secret"}, json={
        "vehicles": [{"id": 1, "start": [106.70, 10.77]}, {"id": 2, "start": [106.70, 10.77]}],
        "jobs": [{"id": 1, "location": [106.71, 10.78]}, {"id": 2, "location": [106.69, 10.79]}],
    })
    assert r.status_code == 200
    b = r.json()
    assert b["code"] == 0 and b["summary"]["routes"] == 2
    assert seen["body"]["vehicles"][0]["profile"] == m.VROOM_PROFILE   # profile injected before proxy


def test_optimize_rejects_bad_body():
    m = load()
    c = TestClient(m.app)
    r = c.post("/v1/optimize", params={"key": "secret"}, json={"jobs": [{"id": 1, "location": [1, 2]}]})
    assert r.status_code == 400 and r.json()["code"] == 1


def test_optimize_requires_key():
    m = load()
    c = TestClient(m.app)
    r = c.post("/v1/optimize", json={"vehicles": [{"id": 1, "start": [1, 2]}], "jobs": [{"id": 1, "location": [1, 2]}]})
    assert r.status_code in (401, 403)


def test_optimize_remaps_vroom_routing_error_to_422(monkeypatch):
    m = load()

    async def fake_vroom(body):  # VROOM "unfound route" = 500 + error code
        return {"code": 2, "error": "Unfound route(s) from location [106.68,10.80]"}, 500

    monkeypatch.setattr(m, "vroom_call", fake_vroom)
    c = TestClient(m.app)
    r = c.post("/v1/optimize", params={"key": "secret"}, json={
        "vehicles": [{"id": 1, "start": [106.70, 10.77]}], "jobs": [{"id": 1, "location": [106.68, 10.80]}]})
    assert r.status_code == 422 and "Unfound route" in r.json()["error"]   # bad input, not a 500


# --- /v1/speed_limit -----------------------------------------------------------
async def fake_trace_attrs(path, payload):
    return {"edges": [
        {"names": ["Lê Lai"], "speed_limit": None, "speed": 57, "length": 0.10, "road_class": "primary"},
        {"names": ["Lê Lai"], "speed_limit": None, "speed": 57, "length": 0.05, "road_class": "primary"},
        {"names": ["Nguyễn Du"], "speed_limit": 60, "speed": 60, "length": 0.20, "road_class": "primary"},
    ]}


def test_merge_speed_segments_collapses_and_converts_km():
    m = load()
    segs = m._merge_speed_segments([
        {"names": ["Lê Lai"], "speed_limit": None, "speed": 57, "length": 0.10, "road_class": "primary"},
        {"names": ["Lê Lai"], "speed_limit": None, "speed": 57, "length": 0.05, "road_class": "primary"},
        {"names": ["X"], "speed_limit": 60, "speed": 60, "length": 0.20, "road_class": "primary"},
    ])
    assert len(segs) == 2                          # the two Lê Lai edges merge
    assert segs[0]["name"] == "Lê Lai" and segs[0]["speed_limit"] is None and segs[0]["speed"] == 57
    assert segs[0]["length"]["value"] == 150       # 0.10+0.05 km -> 150 m
    assert segs[1]["speed_limit"] == 60


def test_speed_limit_endpoint(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_trace_attrs)
    c = TestClient(m.app)
    r = c.get("/v1/speed_limit", params={"path": "10.77,106.69|10.78,106.70", "key": "secret"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "OK" and b["units"] == "km/h"
    assert any(s["speed_limit"] == 60 for s in b["segments"])   # posted limit surfaced
    assert all("speed" in s for s in b["segments"])             # modeled speed always present
    r2 = c.get("/v1/speed_limit", params={"location": "10.77,106.69", "key": "secret"})  # single point
    assert r2.status_code == 200 and r2.json()["segments"]


def test_speed_limit_requires_input():
    m = load()
    c = TestClient(m.app)
    assert c.get("/v1/speed_limit", params={"key": "secret"}).status_code == 400
