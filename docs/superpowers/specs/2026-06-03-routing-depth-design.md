# Routing Depth — Design Spec

**Date:** 2026-06-03
**Sub-project:** ③b of the NullMaps upgrade program (①✅ ②✅ ③a✅ → **③b Routing depth** → ④ Visual)
**Branch:** `feat/routing-depth`
**Status:** Approved design → ready for implementation plan

## Context

Goal: deepen Valhalla routing for fleet/dispatch — VN-relevant costing controls, avoid-zones, snap
robustness, and human-readable matrix addresses. **Entirely adapter changes** in
`services/adapter/app/main.py`: no `valhalla.json` change, no graph rebuild, no geocoder reindex — just an
adapter redeploy.

**Confirmed from code/config (read, not assumed):**

- `valhalla.json:204` `max_exclude_polygons_length: 10000` — `exclude_polygons` is already allowed; the
  adapter only forwards point `exclude_locations` today (via `?avoid=`).
- `valhalla.json:203` `max_exclude_locations: 50`; `:207` `max_timedep_distance: 500000` (time-dependent
  route already enabled); `:208` `max_timedep_distance_matrix: 0`; `:144` `traffic_extract` declared but
  no `traffic.tar` is built (no predictive traffic).
- `main.py` `costing_options(request, costing)` returns `{}` for non-truck and `{"truck": {...}}` for
  truck only — no general costing knobs.
- `main.py` `directions()` builds `payload` with `locations`, `costing`, `units`, `directions_options`
  (vi from ①), optional `costing_options` (truck), `exclude_locations` (from `?avoid=`), `alternates`.
- `main.py` `distance_matrix()` returns `origin_addresses`/`destination_addresses` as bare `"lat,lng"`
  strings; null Valhalla cells become `{"status": "ZERO_RESULTS"}`.
- The adapter has a shared `httpx.AsyncClient` + an in-process geocoder response cache (from ①).

**Design decisions (locked with operator):**

- `date_time` / time-dependent matrix / `build_time_zones` are **deferred** — with no historical speed
  dataset they give ~no accuracy gain (Valhalla stays free-flow), and `build_time_zones` would force a
  graph rebuild. Not worth it now.
- `avoid_zones` accepts **GeoJSON** (`Polygon`/`MultiPolygon`), not encoded polylines.

**Explicitly out of scope:** `date_time`/time-dependent routing, predictive/historical traffic,
`build_time_zones`, any `valhalla.json` change, `exclude_closures` defaults (no traffic to act on).

## Goals / Success Criteria

1. `?use_ferry=`, `?use_tolls=`, `?use_highways=`, `?use_living_streets=` (0–1) and `?top_speed=` reach
   Valhalla via `costing_options[costing]`, alongside the existing truck dimensions.
2. `?avoid_zones=<GeoJSON>` is forwarded as Valhalla `exclude_polygons`; malformed input is ignored
   (fail-open), oversize input (>10000 chars) is rejected.
3. A default snap `radius` lets borderline points snap; a matrix with null cells / a `ZERO_RESULTS` route
   triggers one wider-radius retry; `/route` reports `snapped_distance_m` when a point snapped notably far.
4. `?addresses=true` fills matrix `origin/destination_addresses` with reverse-geocoded strings (N+M
   cached calls), and the default matrix output is unchanged.
5. All of the above is covered by tests with Valhalla + geocoder mocked.

## Design

All changes live in `services/adapter/app/main.py` (the adapter's single module, consistent with the
existing structure). New helpers are small and pure-ish (take `Request`, return a dict/list).

### 1 — VN costing knobs (extend `costing_options`)

Generalize `costing_options(request, costing)` to build a per-costing option dict for **any** costing,
merging the existing truck dimensions:

```python
_USE_KNOBS = ("use_ferry", "use_tolls", "use_highways", "use_living_streets")

def costing_options(request, costing):
    q = request.query_params
    opts = {}
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
        # existing truck dims/hazmat, merged into the same dict
        ...
    return {costing: opts} if opts else {}
```

Valhalla ignores costing-option keys that don't apply to a given costing, so forwarding the full set is
safe. VN relevance: `use_highways=0` keeps scooters off expressways they're banned from; `use_ferry`
tunes the common delta ferries.

### 2 — Avoid-zones via `exclude_polygons` (new `avoid_zones` helper)

```python
def avoid_zones(request):
    """?avoid_zones=<GeoJSON Polygon|MultiPolygon> -> Valhalla exclude_polygons.
    Fail-open: malformed JSON/geometry is ignored. Oversize is rejected to respect
    valhalla.json max_exclude_polygons_length=10000."""
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
        # Valhalla exclude_polygons = list of rings, each a list of [lon, lat]
        return [[[float(pt[0]), float(pt[1])] for pt in ring] for ring in rings]
    except (ValueError, KeyError, TypeError, IndexError):
        return []
```

In `directions()` (and `distance_matrix()`), if `avoid_zones(request)` is non-empty, set
`payload["exclude_polygons"] = zones`. The existing point `?avoid=` (`exclude_locations`) stays.

### 3 — Snap mitigation (radius + one-shot retry + snapped note)

- A helper `with_radius(locs, radius)` returns a copy of each location dict with `"radius"` set (meters).
- Default `radius = int(?snap_radius=, default 50)`, clamped to a sane max (e.g. 200). Apply to all route
  locations and all matrix sources/targets.
- **Route retry:** if `/route` (or `/optimized_route`) returns a non-OK trip, retry once with
  `radius = max(snap_radius, 200)` before returning `ZERO_RESULTS`.
- **Matrix retry:** if any matrix cell is null, retry `/sources_to_targets` once with the wider radius and
  merge in any cells that resolve on the second pass.
- **Snapped note (route only):** Valhalla echoes snapped coordinates in `trip.locations`. For each leg,
  if the snapped point is more than ~25 m from the requested point, add `snapped_distance_m` to the leg
  start. (Matrix does not return per-source snap coordinates, so no note there — the retry is the mitigation.)

### 4 — Reverse-geocoded matrix addresses (opt-in)

In `distance_matrix()`, when `?addresses=true|1|yes`:
- For each distinct source and target, call the cached `geocoder("/reverse", {lat, lon})` (N+M calls, not
  N×M; repeats hit the ① cache). Build the formatted string with the existing `_formatted(result)` helper.
- On any reverse failure/empty, fall back to the bare `"lat,lng"` string (never break the matrix).
- Default (flag absent) leaves the current `"lat,lng"` behavior untouched (fast path).

### Testing (`services/adapter/tests/test_routing_depth.py`, new)

Following the existing `monkeypatch`-the-engine + `TestClient` pattern:

- **costing knobs:** `?use_highways=0&use_ferry=0.2&top_speed=40` on a scooter route → captured Valhalla
  payload has `costing_options['motor_scooter'] == {'use_highways': 0.0, 'use_ferry': 0.2, 'top_speed': 40.0}`;
  out-of-range `use_*` clamps to 0/1; truck dims still merge.
- **avoid_zones:** a GeoJSON Polygon → `payload['exclude_polygons']` has the ring as `[lon,lat]` pairs;
  malformed JSON → key absent; a >10000-char string → key absent.
- **snap retry:** a Valhalla mock that returns a null matrix cell on call 1 and a resolved cell on call 2
  → the element is OK and the mock was called twice; a route mock returning ZERO_RESULTS then OK → retried.
- **snapped note:** a route mock whose `trip.locations` are >25 m from the request → leg has
  `snapped_distance_m`.
- **addresses:** `?addresses=true` with a geocoder mock → `origin_addresses`/`destination_addresses` are
  reverse strings; without the flag → bare `"lat,lng"`; a geocoder error → falls back to `"lat,lng"`.

## Risks & Mitigations

- **Snap retry doubles latency on failures only** — the retry runs only when the first pass has a null
  cell / ZERO_RESULTS, so the common path is unaffected; the wider radius is capped.
- **`addresses=true` adds N+M geocoder calls** — opt-in, cached, and N+M (not N×M); a large matrix without
  the flag is unaffected.
- **Forwarding all `use_*` knobs to every costing** — Valhalla ignores inapplicable keys; values are
  clamped, so a bad client value can't produce an invalid request.
- **GeoJSON only** — clients sending encoded polylines won't match; documented in the adapter README, and
  malformed input fails open (no avoid zone applied) rather than erroring the route.

## Definition of Done

- All success criteria met; `python3 -m pytest` green in `services/adapter` (existing + new tests).
- Adapter README documents the new params (`use_*`, `top_speed`, `avoid_zones`, `snap_radius`, `addresses`).
- No `valhalla.json` change, no graph rebuild required — ships with an adapter redeploy.
