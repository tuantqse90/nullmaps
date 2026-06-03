# Routing Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen Valhalla routing via the adapter — VN costing knobs, GeoJSON avoid-zones, snap robustness, and opt-in reverse-geocoded matrix addresses — with no `valhalla.json` change and no graph rebuild.

**Architecture:** All changes are in `services/adapter/app/main.py` (the adapter's single module). New helpers (`avoid_zones`, `with_radius`, `snap_radius`, `haversine_m`) are small and take `Request`/primitives. Tests mock Valhalla + geocoder via the existing `monkeypatch` + `TestClient` pattern.

**Tech Stack:** Python 3.12, FastAPI, `json`/`math` stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-06-03-routing-depth-design.md`

**Branch:** `feat/routing-depth` (already created)

**Ships with:** an adapter redeploy (no `valhalla.json` change, no graph rebuild).

---

## File Structure

- **Modify** `services/adapter/app/main.py` — extend `costing_options`; add `avoid_zones`, `with_radius`, `snap_radius`, `haversine_m`; wire into `directions()` and `distance_matrix()`.
- **Create** `services/adapter/tests/test_routing_depth.py` — costing/avoid/snap/addresses tests (engines mocked).
- **Modify** `services/adapter/README.md` — document the new query params.

All adapter tests run with: `cd services/adapter && python3 -m pytest -q` (deps already in `requirements.txt`).

---

## Task 1: VN costing knobs (extend `costing_options`)

**Files:**
- Modify: `services/adapter/app/main.py` (`costing_options` ~lines 108-125; `distance_matrix` payload ~lines 357-362)
- Test: `services/adapter/tests/test_routing_depth.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `services/adapter/tests/test_routing_depth.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_routing_depth.py -k costing -v`
Expected: FAIL — `costing_options` returns `{}` for non-truck, so `payload["costing_options"]` is absent → KeyError.

- [ ] **Step 3: Rewrite `costing_options`**

In `services/adapter/app/main.py`, replace the whole `costing_options` function (currently lines 108-125):

```python
def costing_options(request: Request, costing: str) -> dict:
    """Per-costing options. For trucks, pass dimensions/limits so routing avoids
    too-low/too-narrow/weight-restricted roads (logistics)."""
    if costing != "truck":
        return {}
    q = request.query_params
    truck = {}
    for param, key in (("height", "height"), ("width", "width"), ("length", "length"),
                       ("weight", "weight"), ("axle_load", "axle_load")):
        v = q.get(param)
        if v:
            try:
                truck[key] = float(v)
            except ValueError:
                pass
    if (q.get("hazmat") or "").lower() in ("1", "true", "yes"):
        truck["hazmat"] = True
    return {"truck": truck} if truck else {}
```

with:

```python
_USE_KNOBS = ("use_ferry", "use_tolls", "use_highways", "use_living_streets")


def costing_options(request: Request, costing: str) -> dict:
    """Per-costing options forwarded into Valhalla costing_options[costing].
    General knobs (use_*/top_speed) apply to any costing — Valhalla ignores ones
    that don't apply — and trucks additionally get dimensions/limits (logistics)."""
    q = request.query_params
    opts: dict = {}
    for k in _USE_KNOBS:
        v = q.get(k)
        if v is not None:
            try:
                opts[k] = max(0.0, min(1.0, float(v)))   # Valhalla use_* are 0..1
            except ValueError:
                pass
    ts = q.get("top_speed")
    if ts:
        try:
            opts["top_speed"] = float(ts)
        except ValueError:
            pass
    if costing == "truck":
        for param in ("height", "width", "length", "weight", "axle_load"):
            v = q.get(param)
            if v:
                try:
                    opts[param] = float(v)
                except ValueError:
                    pass
        if (q.get("hazmat") or "").lower() in ("1", "true", "yes"):
            opts["hazmat"] = True
    return {costing: opts} if opts else {}
```

- [ ] **Step 4: Wire `costing_options` into the matrix payload**

In `distance_matrix()`, replace (currently ~lines 357-362):

```python
    data = await valhalla("/sources_to_targets", {
        "sources": sources,
        "targets": targets,
        "costing": costing_for(request),
        "units": "kilometers",
    })
```

with:

```python
    costing = costing_for(request)
    payload = {"sources": sources, "targets": targets,
               "costing": costing, "units": "kilometers"}
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    data = await valhalla("/sources_to_targets", payload)
```

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass (directions already wired `costing_options`; matrix now wired too).

- [ ] **Step 6: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_routing_depth.py
git commit -m "feat(adapter): VN costing knobs (use_*/top_speed) for any costing + matrix"
```

---

## Task 2: Avoid-zones via `exclude_polygons`

**Files:**
- Modify: `services/adapter/app/main.py` (`import json`; new `avoid_zones`; wire into `directions` + `distance_matrix`)
- Test: `services/adapter/tests/test_routing_depth.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/adapter/tests/test_routing_depth.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_routing_depth.py -k avoid -v`
Expected: FAIL — `avoid_zones` undefined / `exclude_polygons` not set.

- [ ] **Step 3: Add `import json` and the `avoid_zones` helper**

In `services/adapter/app/main.py`, add `import json` to the stdlib imports (near `import os`, `import time`).

Add the helper right after the existing `avoid_locations` function:

```python
def avoid_zones(request: Request) -> list:
    """?avoid_zones=<GeoJSON Polygon|MultiPolygon> -> Valhalla exclude_polygons
    (a list of rings, each a list of [lon, lat]). Fail-open on malformed input;
    reject oversize to respect valhalla.json max_exclude_polygons_length=10000."""
    raw = request.query_params.get("avoid_zones")
    if not raw or len(raw) > 10000:
        return []
    try:
        geo = json.loads(raw)
        t = geo.get("type")
        if t == "Polygon":
            rings = [geo["coordinates"][0]]
        elif t == "MultiPolygon":
            rings = [poly[0] for poly in geo["coordinates"]]
        else:
            return []
        return [[[float(pt[0]), float(pt[1])] for pt in ring] for ring in rings]
    except (ValueError, KeyError, TypeError, IndexError):
        return []
```

- [ ] **Step 4: Wire into `directions()`**

In `directions()`, find:

```python
    excl = avoid_locations(request)
    if excl:
        payload["exclude_locations"] = excl
```

and insert immediately after it:

```python
    zones = avoid_zones(request)
    if zones:
        payload["exclude_polygons"] = zones
```

- [ ] **Step 5: Wire into `distance_matrix()`**

In `distance_matrix()`, find (added in Task 1):

```python
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    data = await valhalla("/sources_to_targets", payload)
```

and insert the zones block before the `data = ...` line:

```python
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    zones = avoid_zones(request)
    if zones:
        payload["exclude_polygons"] = zones
    data = await valhalla("/sources_to_targets", payload)
```

- [ ] **Step 6: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_routing_depth.py
git commit -m "feat(adapter): GeoJSON avoid_zones -> Valhalla exclude_polygons (fail-open)"
```

---

## Task 3: Snap radius + one-shot retry

**Files:**
- Modify: `services/adapter/app/main.py` (`snap_radius`, `with_radius` helpers; `directions` + `distance_matrix` retry)
- Test: `services/adapter/tests/test_routing_depth.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/adapter/tests/test_routing_depth.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_routing_depth.py -k "snap or retries" -v`
Expected: FAIL — no `radius` on locations; only one Valhalla call.

- [ ] **Step 3: Add `snap_radius` + `with_radius` helpers**

In `services/adapter/app/main.py`, add after the `avoid_zones` helper:

```python
def snap_radius(request: Request) -> int:
    """Per-location snap search radius (m). Lets borderline points snap to a road."""
    try:
        r = int(request.query_params.get("snap_radius") or 50)
    except ValueError:
        r = 50
    return max(0, min(r, 200))


def with_radius(locs: list, radius: int) -> list:
    """Return a copy of each location with a snap `radius` set."""
    return [{**loc, "radius": radius} for loc in locs]
```

- [ ] **Step 4: Add radius + retry to `directions()`**

In `directions()`, replace:

```python
    payload = {"locations": locs, "costing": costing, "units": "kilometers",
               "directions_options": {"language": language_for(request)}}
```

with:

```python
    rad = snap_radius(request)
    payload = {"locations": with_radius(locs, rad), "costing": costing, "units": "kilometers",
               "directions_options": {"language": language_for(request)}}
```

Then replace:

```python
    data = await valhalla(endpoint, payload)
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)
```

with:

```python
    data = await valhalla(endpoint, payload)
    trip = data.get("trip")
    if (not trip or trip.get("status") != 0) and rad < 200:
        payload["locations"] = with_radius(locs, 200)   # one wider-radius retry
        data = await valhalla(endpoint, payload)
        trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)
```

- [ ] **Step 5: Add radius + retry to `distance_matrix()`**

In `distance_matrix()`, replace the payload's sources/targets construction (added in Task 1):

```python
    costing = costing_for(request)
    payload = {"sources": sources, "targets": targets,
               "costing": costing, "units": "kilometers"}
```

with:

```python
    costing = costing_for(request)
    rad = snap_radius(request)
    payload = {"sources": with_radius(sources, rad), "targets": with_radius(targets, rad),
               "costing": costing, "units": "kilometers"}
```

Then replace:

```python
    data = await valhalla("/sources_to_targets", payload)
    matrix = data.get("sources_to_targets")
    if matrix is None:
        return JSONResponse({"status": "ZERO_RESULTS", "rows": [],
                             "error_message": data.get("error", "")}, status_code=200)
```

with:

```python
    data = await valhalla("/sources_to_targets", payload)
    matrix = data.get("sources_to_targets")
    if matrix is not None and rad < 200 and any(
            c.get("distance") is None for row in matrix for c in row):
        payload["sources"] = with_radius(sources, 200)
        payload["targets"] = with_radius(targets, 200)
        m2 = (await valhalla("/sources_to_targets", payload)).get("sources_to_targets")
        if m2 is not None:
            for i, row in enumerate(matrix):
                for j, c in enumerate(row):
                    if c.get("distance") is None and m2[i][j].get("distance") is not None:
                        matrix[i][j] = m2[i][j]
    if matrix is None:
        return JSONResponse({"status": "ZERO_RESULTS", "rows": [],
                             "error_message": data.get("error", "")}, status_code=200)
```

- [ ] **Step 6: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_routing_depth.py
git commit -m "feat(adapter): snap radius (default 50m) + one-shot wider-radius retry"
```

---

## Task 4: `snapped_distance_m` note on route legs

**Files:**
- Modify: `services/adapter/app/main.py` (`import math`; `haversine_m`; `build_route` leg note)
- Test: `services/adapter/tests/test_routing_depth.py`

- [ ] **Step 1: Write the failing test**

Append to `services/adapter/tests/test_routing_depth.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_routing_depth.py::test_route_reports_snapped_distance -v`
Expected: FAIL — `KeyError: 'snapped_distance_m'`.

- [ ] **Step 3: Add `import math` + `haversine_m`**

In `services/adapter/app/main.py`, add `import math` with the stdlib imports, and add this helper near the other small helpers (e.g. after `dur_text`):

```python
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))
```

- [ ] **Step 4: Emit the note in `build_route`**

In `build_route` (inside `directions()`), find the leg append:

```python
            a, b = visit[i], visit[i + 1]
            legs.append({
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
                "start_location": {"lat": a["lat"], "lng": a.get("lon", a.get("lng"))},
                "end_location": {"lat": b["lat"], "lng": b.get("lon", b.get("lng"))},
                "steps": build_steps(leg, leg_coords),
            })
```

and replace it with:

```python
            a, b = visit[i], visit[i + 1]
            leg_entry = {
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
                "start_location": {"lat": a["lat"], "lng": a.get("lon", a.get("lng"))},
                "end_location": {"lat": b["lat"], "lng": b.get("lon", b.get("lng"))},
                "steps": build_steps(leg, leg_coords),
            }
            oi = a.get("original_index")
            if oi is not None and 0 <= oi < len(locs):
                req = locs[oi]
                snapped = haversine_m(a["lat"], a.get("lon", a.get("lng")), req["lat"], req["lon"])
                if snapped > 25:
                    leg_entry["snapped_distance_m"] = round(snapped, 1)
            legs.append(leg_entry)
```

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass (existing route tests have `locations` without `original_index` or co-located, so the note is simply absent there).

- [ ] **Step 6: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_routing_depth.py
git commit -m "feat(adapter): report snapped_distance_m on route legs snapped >25m away"
```

---

## Task 5: Opt-in reverse-geocoded matrix addresses

**Files:**
- Modify: `services/adapter/app/main.py` (`distance_matrix` return)
- Test: `services/adapter/tests/test_routing_depth.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/adapter/tests/test_routing_depth.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest tests/test_routing_depth.py -k addresses -v`
Expected: FAIL — addresses are always bare `"lat,lng"`.

- [ ] **Step 3: Add a reverse-address helper**

In `services/adapter/app/main.py`, add this helper just above `distance_matrix`:

```python
async def _matrix_addresses(request: Request, points: list) -> list:
    """Reverse-geocode each point for human origin/destination_addresses. Opt-in via
    ?addresses=true; cached via geocoder(); falls back to 'lat,lng' on any failure."""
    if (request.query_params.get("addresses") or "").lower() not in ("1", "true", "yes"):
        return [f'{p["lat"]},{p["lon"]}' for p in points]
    out = []
    for p in points:
        bare = f'{p["lat"]},{p["lon"]}'
        try:
            data = await geocoder("/reverse", {"lat": p["lat"], "lon": p["lon"]})
            r = data.get("result")
            out.append(_formatted(r) if r else bare)
        except HTTPException:
            out.append(bare)
    return out
```

- [ ] **Step 4: Use it in the `distance_matrix` return**

In `distance_matrix()`, replace the return block:

```python
    return {
        "status": "OK",
        "origin_addresses": [f'{s["lat"]},{s["lon"]}' for s in sources],
        "destination_addresses": [f'{t["lat"]},{t["lon"]}' for t in targets],
        "rows": rows,
    }
```

with:

```python
    return {
        "status": "OK",
        "origin_addresses": await _matrix_addresses(request, sources),
        "destination_addresses": await _matrix_addresses(request, targets),
        "rows": rows,
    }
```

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/adapter/app/main.py services/adapter/tests/test_routing_depth.py
git commit -m "feat(adapter): opt-in reverse-geocoded matrix addresses (?addresses=true, cached)"
```

---

## Task 6: Document params + final verification

**Files:**
- Modify: `services/adapter/README.md`

- [ ] **Step 1: Document the new query params**

In `services/adapter/README.md`, after the "Travel mode → costing" paragraph, add:

```markdown
## Routing depth params (③b)

- **Costing knobs** (any mode): `use_ferry`, `use_tolls`, `use_highways`, `use_living_streets` (0..1),
  `top_speed` (km/h). E.g. keep scooters off expressways with `use_highways=0`. Truck dims
  (`height`/`width`/`length`/`weight`/`axle_load`/`hazmat`) still apply for `mode=truck`.
- **Avoid zones**: `avoid_zones=<GeoJSON Polygon|MultiPolygon>` (URL-encoded) → Valhalla
  `exclude_polygons`. Malformed input is ignored; max 10000 chars. (`avoid=lat,lng|...` still excludes points.)
- **Snap**: `snap_radius=<m>` (default 50, max 200) lets borderline points snap. A `ZERO_RESULTS`
  route / null matrix cell triggers one wider-radius (200 m) retry. `/directions` legs include
  `snapped_distance_m` when a point snapped more than 25 m from the request.
- **Matrix addresses**: `addresses=true` fills `origin_addresses`/`destination_addresses` with
  reverse-geocoded strings (N+M cached calls); default returns bare `lat,lng`.

> Deferred (no historical speed dataset): `date_time`/time-dependent routing and predictive traffic —
> with free-flow speeds they add no accuracy yet.
```

- [ ] **Step 2: Final verification**

Run:
```bash
cd /Users/nullshift-labs/dev/nullmap/services/adapter && python3 -m pytest -q
cd /Users/nullshift-labs/dev/nullmap && docker compose -f docker-compose.yml config -q && echo "compose ok"
```
Expected: all adapter tests pass; `compose ok`.

- [ ] **Step 3: Branch log**

Run: `git log --oneline main..HEAD`
Expected: the spec + plan commits + the 6 task commits.

- [ ] **Step 4: Commit**

```bash
git add services/adapter/README.md
git commit -m "docs(adapter): document ③b routing depth params"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** §1 costing knobs (T1, incl. matrix wiring) ✓ · §2 avoid_zones GeoJSON→exclude_polygons in directions+matrix (T2) ✓ · §3 snap radius + retry (T3) + snapped_distance_m note (T4) ✓ · §4 opt-in reverse addresses (T5) ✓ · README docs (T6) ✓. All success criteria mapped.
- **Placeholder scan:** none — every step shows full code or exact find/replace anchors; later tasks' anchors reflect the state after earlier tasks (done in order).
- **Type/name consistency:** `costing_options` returns `{costing: opts}` (T1) and is read by both `directions()` (existing) and `distance_matrix()` (T1 wiring); `avoid_zones`→`exclude_polygons` list-of-rings is the same shape asserted in the T2 test and consumed by Valhalla; `snap_radius`/`with_radius` (T3) are used in both endpoints and the `rad < 200` retry guard is consistent; `haversine_m` (T4) is defined once and used in `build_route`; `_matrix_addresses` (T5) reuses the existing `_formatted` + cached `geocoder()` and the same `?addresses=` flag parsing style as `maybe_normalize`. The matrix `payload` dict introduced in T1 is the same object extended by T2 (`exclude_polygons`) and T3 (radius'd sources/targets).
```
