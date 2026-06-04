"""Phase-③b routing depth: costing knobs, avoid-zones, snap, matrix addresses."""
import importlib
import os

from fastapi.testclient import TestClient


def load(api_key="secret"):
    os.environ["API_KEY"] = api_key
    os.environ.pop("RATE_LIMIT_PER_MIN", None)   # don't inherit a low limit leaked by another test file
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


def test_directions_default_snap_radius(monkeypatch):
    m = load()
    seen = _capture(m, fake_route)
    c = TestClient(m.app)
    c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    assert all(loc.get("radius") == 50 for loc in seen["payload"]["locations"])


def test_matrix_retries_on_null_cell(monkeypatch):
    m = load()
    calls = {"n": 0}

    async def flaky(path, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"sources_to_targets": [[{"distance": None}]]}     # null on first pass
        return {"sources_to_targets": [[{"distance": 2.7, "time": 300}]]}  # resolves on retry

    monkeypatch.setattr(m, "valhalla", flaky)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json", params={
        "origins": "10.77,106.69", "destinations": "10.76,106.68", "key": "secret"})
    assert calls["n"] == 2
    assert r.json()["rows"][0]["elements"][0]["status"] == "OK"


def test_route_retries_on_zero_results(monkeypatch):
    m = load()
    calls = {"n": 0}

    async def flaky(path, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"trip": {"status": 1}}            # non-OK on first pass
        return await fake_route(path, payload)

    monkeypatch.setattr(m, "valhalla", flaky)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    assert calls["n"] == 2
    assert r.json()["status"] == "OK"


def test_route_escapes_service_road_island(monkeypatch):
    """A point snapped to a disconnected service-road island (e.g. an airport
    airfield centroid) fails the nearest-edge attempts; the adapter retries once
    more excluding service roads (search_filter), which connects."""
    m = load()
    seen = []

    async def flaky(path, payload):
        seen.append(payload)
        if all("search_filter" in loc for loc in payload["locations"]):
            return await fake_route(path, payload)   # connects only once service roads excluded
        return {"trip": {"status": 1}}               # "No path" on the nearest-edge attempts

    monkeypatch.setattr(m, "valhalla", flaky)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.8175,106.6565", "key": "secret"})
    assert len(seen) == 3                             # normal + radius-200 + road-snap
    assert seen[-1]["locations"][0]["search_filter"]["min_road_class"] == "residential"
    assert r.json()["status"] == "OK"


def test_matrix_escapes_service_road_island(monkeypatch):
    """Still-null matrix cells get the same service-road-excluding retry."""
    m = load()
    seen = []

    async def flaky(path, payload):
        seen.append(payload)
        if "search_filter" in payload["targets"][0]:
            return {"sources_to_targets": [[{"distance": 2.7, "time": 300}]]}
        return {"sources_to_targets": [[{"distance": None}]]}   # null until service roads excluded

    monkeypatch.setattr(m, "valhalla", flaky)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json", params={
        "origins": "10.77,106.69", "destinations": "10.8175,106.6565", "key": "secret"})
    assert len(seen) == 3                             # normal + radius-200 + road-snap
    assert r.json()["rows"][0]["elements"][0]["status"] == "OK"


def test_matrix_whole_none_escalates_snap(monkeypatch):
    """A whole-matrix Valhalla failure (sources_to_targets=None) now escalates snap
    (radius + service-road-excluding) instead of returning ZERO_RESULTS outright."""
    m = load()
    seen = []

    async def flaky(path, payload):
        seen.append(payload)
        if all("search_filter" in s for s in payload["sources"]):
            return {"sources_to_targets": [[{"distance": 5.0, "time": 600}]]}
        return {"sources_to_targets": None}          # whole-matrix failure until road-snap

    monkeypatch.setattr(m, "valhalla", flaky)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json", params={
        "origins": "10.77,106.69", "destinations": "10.8,106.7", "key": "secret"})
    assert len(seen) == 3                             # initial + radius-200 + road-snap
    assert r.json()["rows"][0]["elements"][0]["status"] == "OK"


def test_parse_latlng_rejects_nonfinite_and_out_of_range():
    m = load()
    c = TestClient(m.app)
    for bad in ["nan,106.7", "1e500,106.7", "inf,106.7", "200,106.7", "10.7,400"]:
        r = c.get("/maps/api/directions/json", params={
            "origin": bad, "destination": "10.7,106.7", "key": "secret"})
        assert r.status_code == 400, bad


def test_isochrone_bad_contours_is_400_not_500():
    m = load()
    c = TestClient(m.app)
    for bad in ["10,abc", "5;10", "0", "-5"]:
        r = c.get("/v1/isochrone", params={
            "location": "10.7,106.7", "contours": bad, "key": "secret"})
        assert r.status_code == 400, bad


def test_avoid_zones_caps_by_point_count_not_chars():
    import json as _j
    m = load()

    class Req:
        def __init__(self, qp):
            self.query_params = qp

    ring = [[106.123456 + i * 1e-6, 10.123456 + i * 1e-6] for i in range(600)]
    poly = _j.dumps({"type": "Polygon", "coordinates": [ring]})
    assert len(poly) > 10000                          # old char cap would have dropped this
    out = m.avoid_zones(Req({"avoid_zones": poly}))
    assert out and len(out[0]) == 600                 # kept: 600 points < 10000-vertex budget
    big = _j.dumps({"type": "Polygon", "coordinates": [[[106.0, 10.0]] * 10001]})
    assert m.avoid_zones(Req({"avoid_zones": big})) == []   # dropped: > 10000 vertices


def test_avoid_zones_non_dict_geojson_ignored():
    m = load()

    class Req:
        def __init__(self, qp):
            self.query_params = qp

    assert m.avoid_zones(Req({"avoid_zones": "[1,2,3]"})) == []   # was AttributeError -> 500
    assert m.avoid_zones(Req({"avoid_zones": "42"})) == []
    assert m.avoid_zones(Req({"avoid_zones": '"str"'})) == []


def test_costing_options_rejects_nonfinite():
    m = load()

    class Req:
        def __init__(self, qp):
            self.query_params = qp

    assert m.costing_options(Req({"top_speed": "nan"}), "auto") == {}        # was nan -> 500 Valhalla
    assert m.costing_options(Req({"use_ferry": "inf"}), "auto") == {}
    assert m.costing_options(Req({"top_speed": "80"}), "auto") == {"auto": {"top_speed": 80.0}}


def test_clean_text_strips_control_chars():
    m = load()
    assert m._clean_text("\x00abc") == "abc"
    assert m._clean_text("ben\tthanh") == "ben\tthanh"
    assert m._clean_text("Nguyễn Huệ 🚗") == "Nguyễn Huệ 🚗"     # unicode + emoji survive
    assert m._clean_text(None) is None


def test_geocode_and_autocomplete_nullbyte_no_500(monkeypatch):
    m = load()
    seen = {}

    async def fake_geo(path, params):
        seen[path] = params
        return {"results": []}

    monkeypatch.setattr(m, "geocoder", fake_geo)
    c = TestClient(m.app)
    r = c.get("/maps/api/geocode/json", params={"address": "\x00abc", "key": "secret"})
    assert r.status_code == 200                                  # cleaned -> not a 5xx
    assert seen["/geocode"]["q"] == "abc"                        # NUL stripped before the engine
    r = c.get("/maps/api/place/autocomplete/json", params={"input": "\x00abc", "key": "secret"})
    assert r.status_code == 200


def test_route_reports_snapped_distance(monkeypatch):
    m = load()

    async def snapped_far(path, payload):
        from app.polyline import encode
        shape6 = encode([(10.800, 106.700), (10.79, 106.72)], precision=6)
        # trip.locations are ~3 km from the requested origin (10.77,106.69)
        return {"trip": {"status": 0,
                         "locations": [{"lat": 10.800, "lon": 106.700, "original_index": 0},
                                       {"lat": 10.79, "lon": 106.72, "original_index": 1}],
                         "legs": [{"summary": {"length": 5.4, "time": 540}, "shape": shape6}]}}

    monkeypatch.setattr(m, "valhalla", snapped_far)
    c = TestClient(m.app)
    r = c.get("/maps/api/directions/json", params={
        "origin": "10.77,106.69", "destination": "10.79,106.72", "key": "secret"})
    leg = r.json()["routes"][0]["legs"][0]
    assert leg["snapped_distance_m"] > 25


async def fake_matrix_ok(path, payload):
    return {"sources_to_targets": [[{"distance": 2.7, "time": 300}]]}


async def fake_reverse_addr(path, params):
    return {"result": {"name": "Chợ Bến Thành", "kind": "poi",
                       "lat": params["lat"], "lon": params["lon"],
                       "district": "Quận 1", "city": "HCMC"}}


def test_addresses_opt_in(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_matrix_ok)
    monkeypatch.setattr(m, "geocoder", fake_reverse_addr)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json", params={
        "origins": "10.77,106.69", "destinations": "10.76,106.68",
        "addresses": "true", "key": "secret"}).json()
    assert "Bến Thành" in r["origin_addresses"][0]
    assert "Bến Thành" in r["destination_addresses"][0]


def test_addresses_default_is_latlng(monkeypatch):
    m = load()
    monkeypatch.setattr(m, "valhalla", fake_matrix_ok)
    c = TestClient(m.app)
    r = c.get("/maps/api/distancematrix/json", params={
        "origins": "10.77,106.69", "destinations": "10.76,106.68", "key": "secret"}).json()
    assert r["origin_addresses"][0] == "10.77,106.69"
