"""NullMaps Google/Goong-compat adapter (Phase 4).

Maps Google Maps API request/response shapes onto the native NullMaps engines so
existing apps can repoint without rewriting client code. Goong's REST shapes mirror
Google's closely, so this Google-compatible surface covers most of Goong too.

Live now (Valhalla is up):
  GET /maps/api/directions/json        -> Valhalla /route
  GET /maps/api/distancematrix/json    -> Valhalla /sources_to_targets

Pending Phase 3 (Photon) — return a clear 503 until geocoding is online:
  GET /maps/api/geocode/json
  GET /maps/api/place/autocomplete/json

Auth: single shared API_KEY, checked on every endpoint except /healthz.
Pass it Google-style as ?key=... or as an X-API-Key header.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .polyline import reencode, encode, decode

_geo_cache: TTLCache = TTLCache(maxsize=2048, ttl=120)  # geocoder read cache (2 min)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    lifespan=lifespan,
    title="NullMaps API",
    version="1.0.0",
    description=(
        "Self-hosted, Google/Goong-compatible maps API. Authenticate with the shared "
        "key via `?key=...` or header `X-API-Key`.\n\n"
        "**Directions** `GET /maps/api/directions/json` — `origin`, `destination` (lat,lng), "
        "optional `waypoints` (`optimize:true|lat,lng|...` for TSP), `mode`/`vehicle` "
        "(motorbike by default), returns Google shape incl. turn-by-turn `steps`.\n\n"
        "**Distance Matrix** `GET /maps/api/distancematrix/json` — `origins`, `destinations` "
        "(`|`-separated lat,lng).\n\n"
        "**Geocoding** `GET /maps/api/geocode/json` — `address=` (forward) or `latlng=` (reverse); "
        "optional `location=lat,lng` viewport bias, `normalize=1` for AI cleanup.\n\n"
        "**Autocomplete** `GET /maps/api/place/autocomplete/json` — `input=`, optional `location=`.\n\n"
        "**Fleet (native):** `GET /v1/isochrone?location=&contours=10,20`, "
        "`GET /v1/snap?path=lat,lng|...`."
    ),
)

API_KEY = os.environ.get("API_KEY", "")
VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://valhalla:8002").rstrip("/")
GEOCODER_URL = os.environ.get("GEOCODER_URL", "http://geocoder:2322").rstrip("/")
NORMALIZER_URL = os.environ.get("NORMALIZER_URL", "").rstrip("/")

# Google travel modes / Goong vehicle -> Valhalla costing. Motorbike-first: an
# unspecified or two-wheeler mode routes as a scooter (NullMaps' primary use case).
COSTING = {
    None: "motor_scooter",
    "": "motor_scooter",
    "two_wheeler": "motor_scooter",
    "motorcycle": "motorcycle",
    "motorbike": "motor_scooter",
    "bike": "motor_scooter",
    "scooter": "motor_scooter",
    "driving": "auto",
    "car": "auto",
    "walking": "pedestrian",
    "bicycling": "bicycle",
    "bicycle": "bicycle",
    "truck": "truck",
    "hgv": "truck",
    "lorry": "truck",
}


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


def avoid_locations(request: Request) -> list:
    """Google-ish `avoid=lat,lng|lat,lng` -> Valhalla exclude_locations."""
    out = []
    for s in (request.query_params.get("avoid") or "").split("|"):
        s = s.strip()
        if "," in s:
            try:
                out.append(parse_latlng(s))
            except HTTPException:
                pass
    return out


def require_key(request: Request) -> None:
    """One shared key. Accept ?key= (Google style) or X-API-Key header."""
    supplied = request.query_params.get("key") or request.headers.get("x-api-key")
    if not API_KEY or supplied != API_KEY:
        raise HTTPException(status_code=403, detail="invalid API key")


# --- usage metrics + simple per-key rate limit (single uvicorn worker) -------
RATE_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "600"))
_counts: dict = defaultdict(int)          # (endpoint, status) -> total
_by_key: dict = defaultdict(int)          # key -> total requests
_rl: dict = defaultdict(lambda: [0, 0])   # key -> [minute_window, count]


@app.middleware("http")
async def metrics_and_ratelimit(request: Request, call_next):
    path = request.url.path
    metered = path.startswith("/maps") or path.startswith("/v1")
    if metered:
        key = request.query_params.get("key") or request.headers.get("x-api-key") or "anon"
        minute = int(time.time() // 60)
        st = _rl[key]
        if st[0] != minute:
            st[0], st[1] = minute, 0
        st[1] += 1
        _by_key[key] += 1
        if st[1] > RATE_PER_MIN:
            _counts[(path, 429)] += 1
            return JSONResponse(
                {"status": "OVER_QUERY_LIMIT", "error_message": f"rate limit {RATE_PER_MIN}/min"},
                status_code=429)
    resp = await call_next(request)
    ep = (request.scope.get("route").path if request.scope.get("route") else path)
    _counts[(ep, resp.status_code)] += 1
    return resp


@app.get("/metrics")
def metrics():
    """Prometheus text format — scrape from Grafana/Prometheus (gateway gates it)."""
    out = ["# TYPE nullmaps_requests_total counter"]
    for (ep, st), n in sorted(_counts.items()):
        out.append(f'nullmaps_requests_total{{endpoint="{ep}",status="{st}"}} {n}')
    out.append("# TYPE nullmaps_requests_by_key_total counter")
    for k, n in sorted(_by_key.items()):
        out.append(f'nullmaps_requests_by_key_total{{key="{k[:8]}"}} {n}')
    return PlainTextResponse("\n".join(out) + "\n")


def parse_latlng(s: str) -> dict:
    """'10.77,106.70' -> {'lat':10.77,'lon':106.70}. Raises on bad input."""
    try:
        lat, lon = (float(x) for x in s.split(",", 1))
        return {"lat": lat, "lon": lon}
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"bad lat,lng: {s!r}")


def costing_for(request: Request) -> str:
    mode = request.query_params.get("mode") or request.query_params.get("vehicle")
    return COSTING.get((mode or "").lower(), "motor_scooter")


# Google `language` / Goong -> Valhalla (Odin) locale. Default vi-VN for a VN-only
# product; unknown codes pass through and Odin falls back to en-US.
LANG = {"vi": "vi-VN", "vi-vn": "vi-VN", "en": "en-US", "en-us": "en-US"}


def language_for(request: Request) -> str:
    raw = request.query_params.get("language") or ""
    return LANG.get(raw.lower(), raw or "vi-VN")


def dist_text(meters: float) -> str:
    return f"{meters/1000:.1f} km" if meters >= 1000 else f"{round(meters)} m"


def dur_text(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 60:
        return f"{m} min"
    return f"{m//60} h {m%60} min"


async def valhalla(path: str, payload: dict) -> dict:
    try:
        r = await app.state.http.post(f"{VALHALLA_URL}{path}", json=payload, timeout=20)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"routing engine unreachable: {e}")
    return r.json() if r.content else {}


async def _geocoder_fetch(path: str, params: dict) -> dict:
    try:
        r = await app.state.http.get(f"{GEOCODER_URL}{path}", params=params, timeout=10)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"geocoder unreachable: {e}")
    return r.json() if r.content else {}


async def geocoder(path: str, params: dict) -> dict:
    """Cached geocoder read. Typeahead/reverse repeat heavily; cache the engine
    response for `ttl` seconds. Errors raise from _geocoder_fetch and are not cached."""
    key = (path, frozenset(params.items()))
    if key in _geo_cache:
        return _geo_cache[key]
    result = await _geocoder_fetch(path, params)
    _geo_cache[key] = result
    return result


async def maybe_normalize(request: Request, text: str, timeout: float = 8) -> str:
    """Optional Phase-5 AI cleanup, opt-in via ?normalize=1. Fail-open: any error,
    timeout, or unconfigured normalizer returns the input unchanged."""
    flag = (request.query_params.get("normalize") or "").lower()
    if not NORMALIZER_URL or flag not in ("1", "true", "yes"):
        return text
    try:
        r = await app.state.http.get(f"{NORMALIZER_URL}/normalize",
                                     params={"q": text}, timeout=timeout)
        return (r.json().get("normalized") or text) if r.content else text
    except httpx.HTTPError:
        return text


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "nullmaps-adapter",
        "phase": 4,
        "live": ["directions", "distancematrix", "geocode", "place/autocomplete"],
        "native": ["v1/isochrone", "v1/snap", "directions optimize:true"],
        "pending": [],
    }


@app.get("/maps/api/directions/json")
async def directions(request: Request):
    """Google Directions shape. `waypoints=optimize:true|lat,lng|...` reorders the
    intermediate stops (Valhalla /optimized_route = TSP) and returns waypoint_order."""
    require_key(request)
    origin = request.query_params.get("origin")
    destination = request.query_params.get("destination")
    if not origin or not destination:
        return JSONResponse({"status": "INVALID_REQUEST", "routes": []}, status_code=400)

    segs = [s for s in (request.query_params.get("waypoints") or "").split("|") if s]
    optimize = False
    if segs and segs[0].startswith("optimize:"):
        optimize = segs[0].split(":", 1)[1].lower() == "true"
        segs = segs[1:]
    mids = [parse_latlng(s.replace("via:", "")) for s in segs]
    locs = [parse_latlng(origin), *mids, parse_latlng(destination)]

    endpoint = "/optimized_route" if (optimize and len(mids) >= 1) else "/route"
    costing = costing_for(request)
    payload = {"locations": locs, "costing": costing, "units": "kilometers",
               "directions_options": {"language": language_for(request)}}
    co = costing_options(request, costing)
    if co:
        payload["costing_options"] = co
    excl = avoid_locations(request)
    if excl:
        payload["exclude_locations"] = excl
    if (request.query_params.get("alternatives") or "").lower() in ("1", "true", "yes") and not optimize:
        payload["alternates"] = 2
    data = await valhalla(endpoint, payload)
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS", "routes": [],
                             "error_message": data.get("error", "")}, status_code=200)

    def build_route(t: dict) -> dict:
        visit = t.get("locations", locs)
        legs, coords = [], []
        for i, leg in enumerate(t["legs"]):
            summ = leg["summary"]
            meters, secs = summ["length"] * 1000, summ["time"]
            leg_coords = decode(leg["shape"], precision=6)
            coords.extend(leg_coords)
            a, b = visit[i], visit[i + 1]
            legs.append({
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
                "start_location": {"lat": a["lat"], "lng": a.get("lon", a.get("lng"))},
                "end_location": {"lat": b["lat"], "lng": b.get("lon", b.get("lng"))},
                "steps": build_steps(leg, leg_coords),
            })
        r = {"summary": "", "legs": legs, "overview_polyline": {"points": encode(coords, precision=5)}}
        if optimize:
            r["waypoint_order"] = [v["original_index"] - 1 for v in visit[1:-1]
                                   if v.get("original_index", 0) not in (0, len(locs) - 1)]
        return r

    routes = [build_route(trip)]
    for alt in data.get("alternates", []):
        if alt.get("trip"):
            routes.append(build_route(alt["trip"]))
    return {"status": "OK", "routes": routes}


@app.get("/maps/api/distancematrix/json")
async def distance_matrix(request: Request):
    require_key(request)
    origins = request.query_params.get("origins")
    destinations = request.query_params.get("destinations")
    if not origins or not destinations:
        return JSONResponse({"status": "INVALID_REQUEST", "rows": []}, status_code=400)
    sources = [parse_latlng(s) for s in origins.split("|")]
    targets = [parse_latlng(s) for s in destinations.split("|")]

    data = await valhalla("/sources_to_targets", {
        "sources": sources,
        "targets": targets,
        "costing": costing_for(request),
        "units": "kilometers",
    })
    matrix = data.get("sources_to_targets")
    if matrix is None:
        return JSONResponse({"status": "ZERO_RESULTS", "rows": [],
                             "error_message": data.get("error", "")}, status_code=200)

    rows = []
    for row in matrix:
        elements = []
        for cell in row:
            if cell.get("distance") is None:
                elements.append({"status": "ZERO_RESULTS"})
                continue
            meters, secs = cell["distance"] * 1000, cell["time"]
            elements.append({
                "status": "OK",
                "distance": {"text": dist_text(meters), "value": round(meters)},
                "duration": {"text": dur_text(secs), "value": round(secs)},
            })
        rows.append({"elements": elements})

    return {
        "status": "OK",
        "origin_addresses": [f'{s["lat"]},{s["lon"]}' for s in sources],
        "destination_addresses": [f'{t["lat"]},{t["lon"]}' for t in targets],
        "rows": rows,
    }


# Valhalla maneuver type -> Google directions `maneuver` string (best-effort).
_MANEUVER = {
    9: "turn-slight-right", 10: "turn-right", 11: "turn-sharp-right",
    12: "uturn-right", 13: "uturn-left", 14: "turn-sharp-left", 15: "turn-left",
    16: "turn-slight-left", 8: "straight", 17: "straight",
    18: "ramp-right", 19: "ramp-left", 20: "ramp-right", 21: "ramp-left",
    23: "fork-right", 24: "fork-left", 25: "merge",
    26: "roundabout-right", 27: "roundabout-right",
}


def build_steps(leg: dict, leg_coords: list) -> list:
    """Map Valhalla leg maneuvers -> Google Directions steps[]."""
    steps = []
    n = len(leg_coords)
    for mv in leg.get("maneuvers", []):
        bi = min(mv.get("begin_shape_index", 0), n - 1) if n else 0
        ei = min(mv.get("end_shape_index", bi), n - 1) if n else 0
        a = leg_coords[bi] if n else (0, 0)
        b = leg_coords[ei] if n else (0, 0)
        meters, secs = mv.get("length", 0) * 1000, mv.get("time", 0)
        step = {
            "html_instructions": mv.get("instruction", ""),
            "distance": {"text": dist_text(meters), "value": round(meters)},
            "duration": {"text": dur_text(secs), "value": round(secs)},
            "start_location": {"lat": a[0], "lng": a[1]},
            "end_location": {"lat": b[0], "lng": b[1]},
            "polyline": {"points": encode(leg_coords[bi:ei + 1], precision=5)},
        }
        m = _MANEUVER.get(mv.get("type"))
        if m:
            step["maneuver"] = m
        steps.append(step)
    return steps


def _bias_params(request: Request) -> dict:
    """Google-style viewport bias: ?location=lat,lng -> geocoder lat/lon."""
    loc = request.query_params.get("location") or request.query_params.get("locationbias")
    if not loc:
        return {}
    try:
        b = parse_latlng(loc.replace("circle:", "").split("@")[-1])
        return {"lat": b["lat"], "lon": b["lon"]}
    except HTTPException:
        return {}


def _feature_types(kind: str) -> list[str]:
    return {"place": ["locality", "political"], "street": ["route"],
            "poi": ["point_of_interest", "establishment"],
            "address": ["street_address"]}.get(kind, ["establishment"])


def _address_components(r: dict) -> list:
    comps = []

    def add(val, types):
        if val:
            comps.append({"long_name": val, "short_name": val, "types": types})

    add(r.get("housenumber"), ["street_number"])
    add(r.get("street"), ["route"])
    add(r.get("district"), ["administrative_area_level_2", "political"])
    add(r.get("city"), ["administrative_area_level_1", "locality", "political"])
    add(r.get("region"), ["administrative_area_level_1", "political"])
    add("Vietnam", ["country", "political"])
    return comps


def _formatted(r: dict) -> str:
    line1 = " ".join(filter(None, [r.get("housenumber"), r.get("street")]))
    seen = []
    for p in [r.get("name"), line1 or None, r.get("district"), r.get("city"), r.get("region")]:
        if p and p not in seen:
            seen.append(p)
    return ", ".join(seen) or (r.get("name") or "")


def _geo_result(r: dict) -> dict:
    return {
        "formatted_address": _formatted(r),
        "geometry": {"location": {"lat": r["lat"], "lng": r["lon"]},
                     "location_type": "APPROXIMATE"},
        "types": _feature_types(r.get("kind", "")),
        "place_id": r.get("osm_id", ""),
        "name": r.get("name"),
        "address_components": _address_components(r),
    }


@app.get("/maps/api/geocode/json")
async def geocode(request: Request):
    """Google Geocoding shape. ?address=... forward; ?latlng=lat,lng reverse."""
    require_key(request)
    latlng = request.query_params.get("latlng")
    if latlng:
        loc = parse_latlng(latlng)
        data = await geocoder("/reverse", {"lat": loc["lat"], "lon": loc["lon"]})
        r = data.get("result")
        if not r:
            return {"status": "ZERO_RESULTS", "results": []}
        results = [r]
    else:
        address = request.query_params.get("address")
        if not address:
            return JSONResponse({"status": "INVALID_REQUEST", "results": []}, status_code=400)
        address = await maybe_normalize(request, address, timeout=8)
        params = {"q": address, "limit": 5}
        params.update(_bias_params(request))
        data = await geocoder("/geocode", params)
        results = data.get("results", [])
        if not results:
            return {"status": "ZERO_RESULTS", "results": []}

    return {"status": "OK", "results": [_geo_result(r) for r in results]}


@app.get("/maps/api/place/nearbysearch/json")
async def nearbysearch(request: Request):
    """Google Nearby Search. ?location=lat,lng&radius=&type=<category>&keyword=<text>."""
    require_key(request)
    loc = request.query_params.get("location")
    if not loc:
        return JSONResponse({"status": "INVALID_REQUEST", "results": []}, status_code=400)
    b = parse_latlng(loc)
    params = {"lat": b["lat"], "lon": b["lon"],
              "radius": request.query_params.get("radius", "1500")}
    if request.query_params.get("type"):
        params["category"] = request.query_params["type"]
    if request.query_params.get("keyword"):
        params["q"] = request.query_params["keyword"]
    data = await geocoder("/nearby", params)
    res = data.get("results", [])
    return {
        "status": "OK" if res else "ZERO_RESULTS",
        "results": [{
            "name": r["name"],
            "place_id": r.get("osm_id", ""),
            "geometry": {"location": {"lat": r["lat"], "lng": r["lon"]}},
            "types": _feature_types(r.get("kind", "")),
            "vicinity": _formatted(r),
            "category": r.get("category"),
            "distance_m": r.get("distance_m"),
        } for r in res],
    }


@app.get("/maps/api/place/details/json")
async def place_details(request: Request):
    """Google Place Details. ?place_id=<osm_id>."""
    require_key(request)
    pid = request.query_params.get("place_id")
    if not pid:
        return JSONResponse({"status": "INVALID_REQUEST", "result": {}}, status_code=400)
    data = await geocoder("/detail", {"osm_id": pid})
    r = data.get("result")
    if not r:
        return {"status": "NOT_FOUND", "result": {}}
    return {"status": "OK", "result": _geo_result(r)}


@app.get("/maps/api/place/autocomplete/json")
async def autocomplete(request: Request):
    """Google Places Autocomplete shape -> geocoder typeahead.

    Supports ?location=lat,lng viewport bias and opt-in ?normalize=1.
    """
    require_key(request)
    text = request.query_params.get("input")
    if not text:
        return JSONResponse({"status": "INVALID_REQUEST", "predictions": []}, status_code=400)
    text = await maybe_normalize(request, text, timeout=2)  # typeahead must not hang on the LLM
    params = {"q": text, "limit": 8}
    params.update(_bias_params(request))
    data = await geocoder("/autocomplete", params)
    preds = data.get("results", [])
    if not preds:
        return {"status": "ZERO_RESULTS", "predictions": []}
    return {
        "status": "OK",
        "predictions": [{
            "description": ", ".join(filter(None, [p["name"], p.get("extra")])),
            "place_id": p.get("osm_id", ""),
            "structured_formatting": {
                "main_text": p["name"],
                "secondary_text": p.get("extra", ""),
            },
            "types": _feature_types(p.get("kind", "")),
        } for p in preds],
    }


# --- NullMaps-native fleet extensions (no Google equivalent) ------------------

@app.get("/v1/isochrone")
async def isochrone(request: Request):
    """Reachability polygons. ?location=lat,lng&contours=10,20 (minutes)&mode=...
    Returns Valhalla GeoJSON (FeatureCollection of contour polygons)."""
    require_key(request)
    loc = request.query_params.get("location")
    if not loc:
        return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
    mins = [float(m) for m in (request.query_params.get("contours") or "15").split(",") if m]
    data = await valhalla("/isochrone", {
        "locations": [parse_latlng(loc)],
        "costing": costing_for(request),
        "contours": [{"time": m} for m in mins],
        "polygons": True,
    })
    return data


@app.get("/v1/snap")
async def snap(request: Request):
    """Snap-to-roads / map-matching. ?path=lat,lng|lat,lng|...&mode=...
    Returns the matched route distance/duration + encoded polyline."""
    require_key(request)
    path = request.query_params.get("path")
    if not path:
        return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
    shape = [parse_latlng(p) for p in path.split("|") if p]
    data = await valhalla("/trace_route", {
        "shape": shape, "costing": costing_for(request),
        "shape_match": "map_snap", "units": "kilometers",
    })
    trip = data.get("trip")
    if not trip or trip.get("status") != 0:
        return JSONResponse({"status": "ZERO_RESULTS",
                             "error_message": data.get("error", "")}, status_code=200)
    coords = []
    for leg in trip["legs"]:
        coords.extend(decode(leg["shape"], precision=6))
    s = trip["summary"]
    return {
        "status": "OK",
        "distance": {"text": dist_text(s["length"] * 1000), "value": round(s["length"] * 1000)},
        "duration": {"text": dur_text(s["time"]), "value": round(s["time"])},
        "snapped_polyline": {"points": encode(coords, precision=5)},
    }
